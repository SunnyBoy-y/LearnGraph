"""教学包生成层：分类重试、确定性归一化、阶段内并发与中断自动重放。

These tests exist because the pipeline used to fail its 试卷 stage with
"请检查模型配置后重试本阶段" while the real cause was that
``ProviderResponseError`` (truncated JSON, stray keys, numeric answer indexes) is
not a ``ValueError`` and therefore never reached the retry branch at all. Every
case below pins one half of that repair: what is fixed locally for free, what is
retried, and what must fail fast.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.errors import AppError
from app.domain.learning_package_models import LearningBuild, LearningEligibility
from app.domain.models import DurableJob, Graph, GraphNode, utc_now
from app.domain.schemas.learning_packages import Activity, Exam, Lesson
from app.providers.remote.openai import (
    ProviderHTTPError,
    ProviderInvalidUrlError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from app.services import learning_generation as lg
from app.services import learning_packages as lp


def exam_payload(**overrides):
    payload = {
        "title": "火候测评",
        "pass_score": 80,
        "practical_points": 30,
        "questions": [
            {"id": "q1", "kind": "single_choice", "prompt": "先做什么？", "options": ["热锅", "下菜"],
             "points": 30, "answers": ["0"], "rubric": "选对给分", "explanation": "先热锅。", "critical": True},
            {"id": "q2", "kind": "fill_blank", "prompt": "填空：____", "options": [],
             "points": 20, "answers": ["熟成"], "rubric": "同义可接受", "explanation": "见教材。", "critical": False},
            {"id": "q3", "kind": "short_answer", "prompt": "说明理由。", "options": [],
             "points": 20, "answers": ["理由"], "rubric": "按点给分", "explanation": "评分见 rubric", "critical": False},
        ],
    }
    payload.update(overrides)
    return payload


def valid_section(index: int):
    return {"id": f"s{index}", "title": f"标题{index}", "body": "正文" * 20, "takeaway": "要点"}


# --------------------------------------------------------------------------- #
# 归一化：能在本地免费修好的，绝不花钱重问模型
# --------------------------------------------------------------------------- #

def test_numeric_option_indexes_are_normalized_to_strings():
    payload = exam_payload()
    payload["questions"][0]["answers"] = [0]

    normalized = lg.normalize_exam(payload, has_activity=True)

    assert normalized["questions"][0]["answers"] == ["0"]
    assert Exam.model_validate(normalized).practical_points == 30


def test_a_valid_exam_is_not_silently_rewritten():
    """不变量：合规试卷必须原样通过，归一化只修不合规的东西。"""
    payload = exam_payload()

    normalized = lg.normalize_exam(payload, has_activity=True)

    assert normalized == payload
    assert Exam.model_validate(normalized).model_dump() == Exam.model_validate(payload).model_dump()


def test_undeclared_keys_are_projected_away():
    payload = exam_payload()
    payload["questions"][0]["difficulty"] = "easy"
    payload["difficulty"] = "medium"

    normalized = lg.normalize_payload(Exam, payload)
    normalized = lg.normalize_exam(normalized, has_activity=True)

    assert "difficulty" not in normalized
    assert "difficulty" not in normalized["questions"][0]
    Exam.model_validate(normalized)


def test_missing_rubric_and_explanation_get_deterministic_defaults():
    payload = exam_payload()
    payload["questions"][2].pop("rubric")
    payload["questions"][2]["explanation"] = ""

    normalized = lg.normalize_exam(payload, has_activity=True)
    question = normalized["questions"][2]

    assert question["rubric"] == lg.DEFAULT_RUBRIC
    assert question["explanation"] == lg.DEFAULT_EXPLANATION
    Exam.model_validate(normalized)


@pytest.mark.parametrize(
    ("practical_points", "has_activity"),
    [(0, True), (30, False), (10, True)],
)
def test_practical_points_follow_whether_an_activity_exists(practical_points, has_activity):
    payload = exam_payload(practical_points=practical_points)

    normalized = lg.normalize_exam(payload, has_activity=has_activity)
    exam = Exam.model_validate(normalized)

    assert exam.practical_points == (30 if has_activity else 0)
    assert sum(q.points for q in exam.questions) + exam.practical_points == 100


def test_points_are_rescaled_to_exactly_one_hundred():
    payload = exam_payload()
    payload["questions"][0]["points"] = 35  # 总分 95，JSON Schema 表达不了

    normalized = lg.normalize_exam(payload, has_activity=True)
    exam = Exam.model_validate(normalized)

    assert sum(q.points for q in exam.questions) == 70
    assert sum(q.points for q in exam.questions) + exam.practical_points == 100
    # 顺序与相对权重保留：最重的题仍然最重。
    assert exam.questions[0].points >= max(q.points for q in exam.questions[1:])


def test_true_false_keeps_a_canonical_pair_but_is_never_reordered():
    payload = exam_payload()
    payload["questions"][0] = {"id": "q1", "kind": "true_false", "prompt": "先热锅再下菜，对吗？",
                               "options": ["对", "错", "不确定"], "points": 30, "answers": ["0"],
                               "rubric": "x", "explanation": "y", "critical": True}

    normalized = lg.normalize_exam(payload, has_activity=True)

    assert normalized["questions"][0]["options"] == ["对", "错"]
    Exam.model_validate(normalized)


def test_answer_keys_are_never_invented():
    """单选答案越界时丢弃该题（宁可重问），绝不替模型挑一个答案。"""
    payload = exam_payload()
    payload["questions"].append({"id": "q4", "kind": "single_choice", "prompt": "越界的答案？",
                                 "options": ["甲", "乙"], "points": 10, "answers": ["9"],
                                 "rubric": "x", "explanation": "y", "critical": False})

    normalized = lg.normalize_exam(payload, has_activity=True)
    ids = [q["id"] for q in normalized["questions"]]

    assert "q4" not in ids, "无法确定的答案只能丢弃"
    assert len(ids) == 3
    Exam.model_validate(normalized)
    assert sum(q["points"] for q in normalized["questions"]) == 70


def test_unusable_payload_passes_through_so_the_model_is_asked_again():
    payload = exam_payload()
    payload["questions"] = payload["questions"][:1]
    payload["questions"].append({"id": "q9", "kind": "single_choice", "prompt": "越界？",
                                 "options": ["甲", "乙"], "points": 1, "answers": ["9"],
                                 "rubric": "x", "explanation": "y", "critical": False})

    # 只剩 2 道可用题 → 低于下限，原样返回交给重试（带上具体规则），不编造内容。
    assert lg.normalize_exam(payload, has_activity=True) is payload


def test_written_questions_lose_choice_options():
    payload = exam_payload()
    payload["questions"][1]["options"] = ["A", "B"]

    normalized = lg.normalize_exam(payload, has_activity=True)

    assert normalized["questions"][1]["options"] == []


def test_activity_references_are_aligned_from_unique_ids_and_labels():
    payload = activity_payload()
    payload["variables"][0]["id"] = "Sun-Points"
    payload["variables"][0]["label"] = "阳光值"
    payload["actions"][0]["id"] = "Collect Sun"
    payload["actions"][0]["label"] = "收集阳光"
    payload["actions"][0]["effects"][0]["variable"] = "阳光值"
    payload["actions"][1]["requires"][0]["variable"] = "sun points"
    payload["actions"][1]["effects"][0]["variable"] = "SUN_POINTS"
    payload["success"][0]["variable"] = "Sun-Points"
    payload["solution"] = ["收集阳光", "Collect Sun", "cook"]

    normalized = lg.normalize_activity(payload)
    activity = Activity.model_validate(normalized)

    assert activity.variables[0].id == "sun_points"
    assert activity.actions[0].id == "collect_sun"
    assert [effect.variable for action in activity.actions for effect in action.effects] == ["sun_points", "sun_points"]
    assert activity.solution == ["collect_sun", "collect_sun", "cook"]


def test_activity_normalization_never_guesses_an_unknown_reference():
    payload = activity_payload()
    payload["actions"][0]["effects"][0]["variable"] = "undeclared_resource"

    normalized = lg.normalize_activity(payload)

    assert normalized["actions"][0]["effects"][0]["variable"] == "undeclared_resource"
    with pytest.raises(ValidationError, match="unknown activity reference"):
        Activity.model_validate(normalized)


def test_activity_retry_instruction_explains_reference_contract():
    prompt = lg.repair_instruction(
        lg.CATEGORY_VALIDATION,
        "$: Value error, unknown activity reference",
        schema=Activity,
        label="实验规则",
    )

    assert "requires/effects/success" in prompt
    assert "逐字复制 variables[].id" in prompt
    assert "逐字复制 actions[].id" in prompt
    assert "points" not in prompt


# --------------------------------------------------------------------------- #
# 分类：决定"要不要再花一次钱"
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProviderResponseError("Provider response contains invalid JSON"), lg.CATEGORY_STRUCTURAL),
        (ProviderResponseError("Configured structured output schema is invalid"), lg.CATEGORY_FATAL),
        (ProviderTimeoutError("timed out"), lg.CATEGORY_TRANSIENT),
        (TimeoutError("timed out"), lg.CATEGORY_TRANSIENT),
        (ProviderHTTPError("rate limited", status_code=429), lg.CATEGORY_TRANSIENT),
        (ProviderHTTPError("gateway", status_code=503), lg.CATEGORY_TRANSIENT),
        (ProviderInvalidUrlError("bad base url", status_code=400), lg.CATEGORY_FATAL),
        (AppError(503, "learning_model_unavailable", "no model"), lg.CATEGORY_FATAL),
        (AppError(409, "usage_budget_exceeded", "budget"), lg.CATEGORY_FATAL),
    ],
)
def test_error_classification(error, expected):
    assert lg.classify_generation_error(error) == expected


def test_pydantic_validation_error_is_retryable_and_its_detail_hides_values():
    try:
        Exam.model_validate(exam_payload(practical_points=0))
    except ValidationError as exc:
        assert lg.classify_generation_error(exc) == lg.CATEGORY_VALIDATION
        # 试卷里含私有答案，细节里绝不能带 input_value。
        assert "input_value" not in lg.safe_failure_detail(exc)
        assert "答案" not in lg.safe_failure_detail(exc)
    else:  # pragma: no cover - the payload must be invalid
        raise AssertionError("payload should not validate")


# --------------------------------------------------------------------------- #
# 有界重试：次数、修复提示、fatal 不重试
# --------------------------------------------------------------------------- #

@pytest.fixture()
def no_backoff(monkeypatch):
    monkeypatch.setattr(lg, "_backoff_seconds", lambda category, attempt: 0)


def test_structural_failure_is_retried_and_then_succeeds(monkeypatch, no_backoff):
    calls = {"count": 0, "prompts": []}

    def fake_generate_raw(db, workspace_id, actor_id, schema, prompt, **_kwargs):
        calls["count"] += 1
        calls["prompts"].append(prompt)
        if calls["count"] < 3:
            raise ProviderResponseError("Structured result does not match the declared schema at questions.0.answers")
        return exam_payload()

    monkeypatch.setattr(lg, "generate_raw", fake_generate_raw)

    result = lg.generate_checked("ws", "u", Exam, "出题", normalize=lambda value: value, label="测评试卷")

    assert calls["count"] == 3
    assert result["title"] == "火候测评"
    assert "上一次响应的 JSON 结构不符合契约" in calls["prompts"][1]


def test_always_failing_structural_stage_reports_category_and_attempts(monkeypatch, no_backoff):
    calls = {"count": 0}

    def fake_generate_raw(db, workspace_id, actor_id, schema, prompt, **_kwargs):
        calls["count"] += 1
        raise ProviderResponseError("Provider response contains invalid JSON")

    monkeypatch.setattr(lg, "generate_raw", fake_generate_raw)

    with pytest.raises(lg.GenerationFailure) as caught:
        lg.generate_checked("ws", "u", Exam, "出题", normalize=lambda value: value, attempts=3, label="测评试卷")

    assert calls["count"] == 3
    assert caught.value.category == lg.CATEGORY_STRUCTURAL
    assert caught.value.attempts == 3


def test_fatal_model_configuration_is_not_retried(monkeypatch, no_backoff):
    calls = {"count": 0}

    def fake_generate_raw(db, workspace_id, actor_id, schema, prompt, **_kwargs):
        calls["count"] += 1
        raise AppError(503, "learning_model_unavailable", "请在设置中配置可用的结构化生成模型。")

    monkeypatch.setattr(lg, "generate_raw", fake_generate_raw)

    with pytest.raises(lg.GenerationFailure) as caught:
        lg.generate_checked("ws", "u", Exam, "出题", normalize=lambda value: value)

    assert calls["count"] == 1
    assert caught.value.category == lg.CATEGORY_FATAL


def test_normalization_rescues_a_deviation_without_a_second_model_call(monkeypatch, no_backoff):
    calls = {"count": 0}

    def fake_generate_raw(db, workspace_id, actor_id, schema, prompt, **_kwargs):
        calls["count"] += 1
        payload = exam_payload()
        payload["questions"][0]["answers"] = [0]      # 数字索引
        payload["questions"][0]["difficulty"] = "easy"  # 未声明的键
        payload["questions"][0]["points"] = 35          # 总分 95
        return payload

    monkeypatch.setattr(lg, "generate_raw", fake_generate_raw)
    validator_calls = []
    from functools import partial

    from app.services.learning_packages import validate_exam_activity

    result = lg.generate_checked(
        "ws", "u", Exam, "出题",
        validator=partial(validate_exam_activity, has_activity=True),
        normalize=partial(lg.normalize_exam, has_activity=True),
    )
    validator_calls.append(result)

    assert calls["count"] == 1, "本地能修好的偏差不应再花钱重问"
    assert result["questions"][0]["answers"] == ["0"]
    assert sum(q["points"] for q in result["questions"]) == 70


@pytest.mark.parametrize(
    ("status", "text", "expected"),
    [
        (400, "max_tokens is too large", True),
        (400, "max_completion_tokens: invalid value", True),
        (413, "max_output_tokens exceeds model limit", True),
        (400, "model does not support structured output", False),
        (429, "rate limited", False),
        (500, "internal error", False),
    ],
)
def test_output_cap_rejection_detection(status, text, expected):
    error = ProviderHTTPError(f"Provider returned HTTP {status}; body={text}", status_code=status)

    assert lg.is_output_cap_rejection(error) is expected


def test_rejected_output_cap_is_retried_without_a_cap(monkeypatch, no_backoff):
    """上限被上游拒绝是可绕开的配置不符，不能算成模型坏了。"""
    from functools import partial

    calls: list[bool] = []
    notes: list[str] = []

    def fake_generate_raw(db, workspace_id, actor_id, schema, prompt, *, drop_output_cap=False, notes=None):
        calls.append(drop_output_cap)
        if not drop_output_cap:
            raise ProviderHTTPError(
                "Provider returned HTTP 400; body={\"error\":{\"message\":\"max_tokens is too large\"}}",
                status_code=400,
            )
        return exam_payload()

    monkeypatch.setattr(lg, "generate_raw", fake_generate_raw)

    result = lg.generate_checked(
        "ws", "u", Exam, "出题",
        normalize=partial(lg.normalize_exam, has_activity=True),
        notes=notes,
    )

    assert calls == [False, True], "第二次必须在不指定上限的情况下重试"
    assert result["practical_points"] == 30
    assert any("输出上限" in note for note in notes)


def test_cap_rejected_stage_still_surfaces_the_real_reason(monkeypatch, no_backoff):
    from functools import partial

    def always_rejected(db, workspace_id, actor_id, schema, prompt, *, drop_output_cap=False, notes=None):
        raise ProviderHTTPError("Provider returned HTTP 400; body=max_tokens is too large", status_code=400)

    monkeypatch.setattr(lg, "generate_raw", always_rejected)

    with pytest.raises(lg.GenerationFailure) as caught:
        lg.generate_checked("ws", "u", Exam, "出题", attempts=3, label="测评试卷",
                            normalize=partial(lg.normalize_exam, has_activity=True))

    assert caught.value.attempts == 3
    assert "已在不指定输出上限的情况下重试过" in caught.value.detail
    assert "测评试卷" in caught.value.detail


# --------------------------------------------------------------------------- #
# 阶段内并发
# --------------------------------------------------------------------------- #

def test_parallel_generations_overlap_and_isolate_failures():
    import time

    def slow(value):
        def run():
            time.sleep(0.3)
            return value
        return run

    def boom():
        raise ProviderResponseError("nope")

    started = time.monotonic()
    results = lg.run_parallel_generations([("lesson", slow("L")), ("activity", slow("A"))], max_workers=2)
    concurrent_elapsed = time.monotonic() - started

    assert results["lesson"][0] == "L" and results["activity"][0] == "A"
    assert concurrent_elapsed < 0.55, "两路应当重叠执行"

    started = time.monotonic()
    lg.run_parallel_generations([("lesson", slow("L")), ("activity", slow("A"))], max_workers=1)
    serial_elapsed = time.monotonic() - started
    assert serial_elapsed > concurrent_elapsed

    failures = lg.run_parallel_generations([("lesson", slow("L")), ("activity", boom)], max_workers=2)
    assert failures["lesson"][0] == "L"
    assert isinstance(failures["activity"][1], ProviderResponseError)


def test_real_generate_checked_runs_concurrently_with_its_own_session(build_world, monkeypatch):
    """生产组合：并发两路调用真正的 generate_checked，每路自建会话。"""
    import time
    from functools import partial
    from threading import current_thread

    threads: list[str] = []

    def fake_generate_raw(db, workspace_id, actor_id, schema, prompt, **_kwargs):
        threads.append(current_thread().name)
        assert schema is Activity
        time.sleep(0.4)
        return activity_payload()

    def generate_activity():
        return lg.generate_checked("ws", "u", Activity, "设计实验",
                                   normalize=partial(lg.normalize_payload, Activity), label="实验规则")

    monkeypatch.setattr(lg, "generate_raw", fake_generate_raw)
    monkeypatch.setattr(lg, "_backoff_seconds", lambda category, attempt: 0)

    started = time.monotonic()
    results = lg.run_parallel_generations([("a", generate_activity), ("b", generate_activity)], max_workers=2)
    elapsed = time.monotonic() - started

    assert all(failure is None for _, failure in results.values()), results
    assert elapsed < 0.75, f"两路真实生成应当重叠（实际 {elapsed:.2f}s）"
    assert len(set(threads)) == 2, "每路应各自在工作线程里执行"


# --------------------------------------------------------------------------- #
# 中断自动重放 + 已完成部分不重做（跑真实 run_build_stage）
# --------------------------------------------------------------------------- #

@pytest.fixture()
def build_world(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'learning.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(lp, "SessionLocal", factory)
    monkeypatch.setattr(lg, "SessionLocal", factory)
    monkeypatch.setattr(lp, "actor_can_build", lambda *args, **kwargs: True)
    monkeypatch.setattr(lp, "get_settings", lambda: type("S", (), {
        "learning_generation_attempts": 3,
        "learning_generation_parallel": 2,
        "learning_stage_replay_limit": 1,
    })())

    with Session(engine) as db:
        db.add(Graph(id="g1", workspace_id="ws", goal_id="goal", title="图", status="published", revision=2))
        node = GraphNode(id="n1", workspace_id="ws", graph_id="g1", label="热锅与火候",
                         description="观察食材变化", node_type="concept", teaching_strategy="先演示后练习")
        db.add(node)
        db.flush()
        run = LearningBuild(id="b1", workspace_id="ws", node_id="n1", actor_id="u", job_id="j1",
                            trigger="on_demand", epoch=0, fingerprint=lp.fingerprint(node), status="running", stage=1,
                            checkpoints={
                                "inflight_stage": 1,
                                "blueprint": {"title": "火候", "objectives": ["判断火候"], "sections": ["火候", "锅具"],
                                              "activity_brief": "调节火力并下菜", "image_prompt": "", "estimated_minutes": 15},
                                "lesson": {"sections": [valid_section(1), valid_section(2)],
                                           "svg": '<svg viewBox="0 0 10 10"><rect width="1" height="1"/></svg>',
                                           "caption": "图解", "html": ""},
                            })
        db.add(run)
        db.add(DurableJob(id="j1", workspace_id="ws", kind="learning.package.build", dedupe_key="learning.build:b1",
                          payload={"build_id": "b1"}, status="leased", lease_token="tok", lease_owner="w",
                          lease_expires_at=utc_now() + timedelta(seconds=300)))
        db.add(LearningEligibility(node_id="n1", workspace_id="ws", graph_id="g1", epoch=0, version=1))
        db.commit()
    return factory


def activity_payload():
    return {
        "title": "火力实验", "instructions": "调节火力",
        "variables": [{"id": "heat", "label": "火力", "initial": 0, "minimum": 0, "maximum": 3}],
        "actions": [
            {"id": "heat_up", "label": "加热", "requires": [], "effects": [{"variable": "heat", "operation": "add", "value": 1}], "feedback": "锅更热了"},
            {"id": "cook", "label": "下菜", "requires": [{"variable": "heat", "operator": "gte", "value": 2}],
             "effects": [{"variable": "heat", "operation": "set", "value": 2}], "feedback": "菜下锅了"},
        ],
        "success": [{"variable": "heat", "operator": "gte", "value": 2}],
        "solution": ["heat_up", "heat_up", "cook"],
    }


def test_interrupted_stage_is_replayed_automatically_without_redoing_finished_work(build_world, monkeypatch):
    asked: list[str] = []

    def fake_generate_checked(workspace_id, actor_id, schema, prompt, validator=None, normalize=None, *, label="", attempts=None, notes=None, **_kwargs):
        asked.append(label)
        return activity_payload()

    monkeypatch.setattr(lp, "generate_checked", fake_generate_checked)

    terminal = lp.run_build_stage("j1", "tok", "b1")

    assert terminal is False, "阶段成功后应继续后续阶段"
    with build_world() as db:
        run = db.get(LearningBuild, "b1")
        assert run.stage == 2
        assert asked == ["实验规则"], "教材已在 checkpoints 里，不应重新生成"
        assert any("已自动重放" in note for note in run.checkpoints["notes"])
        assert run.checkpoints["replays"] == {"1": 1}
        assert "inflight_stage" not in run.checkpoints


def test_replay_is_capped_and_then_asks_a_human(build_world, monkeypatch):
    monkeypatch.setattr(lp, "generate_checked", lambda *args, **kwargs: pytest.fail("不应再调用模型"))

    with build_world() as db:
        run = db.get(LearningBuild, "b1")
        run.checkpoints = {**run.checkpoints, "replays": {"1": 1}}
        db.commit()

    terminal = lp.run_build_stage("j1", "tok", "b1")

    assert terminal is True
    with build_world() as db:
        run = db.get(LearningBuild, "b1")
        assert run.status == "failed"
        assert "请手动重试本阶段" in run.error


def test_failed_stage_keeps_completed_halves_and_reports_advice(build_world, monkeypatch):
    def half_failing(workspace_id, actor_id, schema, prompt, validator=None, normalize=None, *, label="", attempts=None, notes=None, **_kwargs):
        # 一个阶段里可以有两个生成项（小剧场‖试卷），上报必须点名失败的那一项。
        raise lg.GenerationFailure(lg.CATEGORY_STRUCTURAL, 3, "测评试卷：questions.0.answers: invalid", label="测评试卷")

    monkeypatch.setattr(lp, "generate_checked", half_failing)

    assert lp.run_build_stage("j1", "tok", "b1") is True

    with build_world() as db:
        run = db.get(LearningBuild, "b1")
        view = lp.LearningPackageService.build_view(run)
        assert run.status == "failed"
        assert view["failure_category"] == lg.CATEGORY_STRUCTURAL
        assert view["attempts"] == 3
        assert view["failed_item"] == "测评试卷"
        assert view["failed_stage"] == "图文教材"
        assert "输出上限" in (view["advice"] or "")
        assert run.error.startswith("测评试卷生成未通过")
        assert "已自动尝试 3 次" in run.error
        # 已生成的部分保留，重试与重放都不会重做教材。
        assert "lesson" in run.checkpoints
