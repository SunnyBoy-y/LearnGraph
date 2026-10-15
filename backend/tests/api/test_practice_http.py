"""Practice & Review center HTTP tests.

覆盖：overview / 今日练习计划原因 / 创建 Session / 逐题作答 / 首次 vs 最终正确 /
Retry / Hint / 简答部分正确 / 完成与报告 / 调度写回 / 错题本 / 学习报告 /
空状态 / 刷新后恢复 Session / 图谱深链所需的 graph_id。
"""

from __future__ import annotations

import json
import time
from datetime import timedelta

import pytest

from app.core.database import SessionLocal
from app.domain.models import (
    Exercise,
    Graph,
    GraphNode,
    Goal,
    MasterySchedule,
    new_id,
    utc_now,
)


class PracticeStubProvider:
    """确定性模型桩：出题 + 简答批改，不访问任何真实模型。"""

    available = True
    provider_id = "practice-stub"
    model_id = "practice-stub-model"
    remote_capability = True
    thinking_mode = "off"
    actual_reasoning_effort = None
    search_route = "disabled"
    max_output_tokens = 2048
    last_usage: dict[str, int] = {}

    def __init__(self) -> None:
        self.generate_calls = 0

    def generate_json(self, prompt, schema_name, schema):
        if schema_name == "ModelShortAnswerGrade":
            answer = prompt.split("学生回答：")[-1]
            covered = []
            missing = ["原子操作", "等待 / 唤醒机制"]
            if "临界区" in answer:
                covered.append("临界区")
            if "互斥" in answer:
                covered.append("互斥")
            if "信号量" in answer:
                covered.append("信号量")
            ratio = len(covered) / (len(covered) + len(missing))
            return {
                "covered_points": covered,
                "missing_points": missing,
                "is_correct": ratio >= 0.5,
                "score_ratio": ratio,
                "feedback": f"覆盖了 {len(covered)} 个要点，仍需补充缺失要点。",
            }
        self.generate_calls += 1
        count = 2
        items = []
        for index in range(count):
            items.append(
                {
                    "question_type": "single_choice",
                    "prompt": f"生成的题目 {index + 1}",
                    "options": ["甲", "乙", "丙", "丁"],
                    "answer_key": "甲",
                    "explanation": "因为甲。",
                    "rubric_points": [],
                    "hint": "回想一下互斥的含义。",
                    "source_chunk_ids": [],
                }
            )
        return {"items": items}


@pytest.fixture()
def stub_practice_provider(monkeypatch):
    from app.api.routers import practice as practice_router

    provider = PracticeStubProvider()
    monkeypatch.setattr(
        practice_router, "model_provider_for_workspace", lambda *a, **k: provider
    )
    from app.api.routers import exercises as exercises_router

    monkeypatch.setattr(
        exercises_router, "model_provider_for_workspace", lambda *a, **k: provider
    )
    return provider


def _seed_workspace(workspace_id: str, *, overdue: bool = True):
    """直接写库：一个图谱、两个节点、四道题、一条到期排期。"""

    with SessionLocal() as db:
        goal = Goal(
            id=new_id(),
            workspace_id=workspace_id,
            title="操作系统",
            raw_prompt="学习操作系统",
            status="active",
        )
        db.add(goal)
        graph = Graph(
            id=new_id(),
            workspace_id=workspace_id,
            goal_id=goal.id,
            title="操作系统图谱",
            status="published",
        )
        db.add(graph)
        node_a = GraphNode(
            id=new_id(),
            workspace_id=workspace_id,
            graph_id=graph.id,
            label="并发与同步",
            description="并发指多个任务交替推进；互斥锁保证临界区同一时刻只有一个执行者。",
            target_weight=90,
            retrieval_state="relearning" if overdue else "unverified",
        )
        node_b = GraphNode(
            id=new_id(),
            workspace_id=workspace_id,
            graph_id=graph.id,
            label="死锁四个必要条件",
            description="互斥、占有并等待、不可剥夺、循环等待。",
        )
        db.add_all([node_a, node_b])
        exercises = [
            Exercise(
                id=new_id(),
                workspace_id=workspace_id,
                node_id=node_a.id,
                question_type="single_choice",
                prompt="互斥锁的主要作用是？",
                options=["保证临界区同一时刻只有一个执行者", "提高 CPU 频率", "减少内存占用", "加快磁盘 IO"],
                answer_key="保证临界区同一时刻只有一个执行者",
                explanation="互斥锁提供互斥语义，保护临界区。",
                difficulty="medium",
                metadata_json={"hint": "想想同一时刻能有几个执行者进入临界区。"},
            ),
            Exercise(
                id=new_id(),
                workspace_id=workspace_id,
                node_id=node_a.id,
                question_type="true_false",
                prompt="并发一定意味着并行。",
                options=["正确", "错误"],
                answer_key="错误",
                explanation="并发是交替推进，并行才是同时执行。",
                difficulty="easy",
                metadata_json={},
            ),
            Exercise(
                id=new_id(),
                workspace_id=workspace_id,
                node_id=node_a.id,
                question_type="short_answer",
                prompt="说明互斥锁如何避免多个线程同时进入临界区。",
                options=[],
                answer_key="通过互斥与信号量保证同一时刻只有一个执行者",
                explanation="互斥锁加锁失败会让线程等待，直到锁释放。",
                difficulty="hard",
                rubric_json={"points": ["临界区", "互斥", "信号量", "原子操作", "等待 / 唤醒机制"]},
                metadata_json={},
            ),
            Exercise(
                id=new_id(),
                workspace_id=workspace_id,
                node_id=node_b.id,
                question_type="single_choice",
                prompt="下列哪一个不是死锁的必要条件？",
                options=["互斥", "不可剥夺", "循环等待", "时间片轮转"],
                answer_key="时间片轮转",
                explanation="死锁四条件不含时间片轮转。",
                difficulty="medium",
                metadata_json={},
            ),
        ]
        db.add_all(exercises)
        db.add(
            MasterySchedule(
                id=new_id(),
                workspace_id=workspace_id,
                node_id=node_a.id,
                next_review_at=utc_now() - timedelta(days=2),
                last_qualified_recall_at=utc_now() - timedelta(days=9),
            )
        )
        db.add(
            MasterySchedule(
                id=new_id(),
                workspace_id=workspace_id,
                node_id=node_b.id,
                next_review_at=utc_now() + timedelta(days=1),
            )
        )
        db.commit()
        return {
            "graph_id": graph.id,
            "node_a": node_a.id,
            "node_b": node_b.id,
            "choice": exercises[0].id,
            "true_false": exercises[1].id,
            "short": exercises[2].id,
            "choice_b": exercises[3].id,
        }


@pytest.fixture()
def practice_workspace(client, register_user, stub_practice_provider):
    token, ws, *_ = register_user("practice")
    seeded = _seed_workspace(ws)
    headers = {"Authorization": f"Bearer {token}", "X-Workspace-ID": ws}
    return {"token": token, "ws": ws, "headers": headers, **seeded}


def test_practice_overview_reports_real_plan_and_stats(practice_workspace, client):
    headers = practice_workspace["headers"]
    resp = client.get("/api/v1/practice/overview", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    plan = body["today_plan"]
    assert plan["mode"] in {"scheduled", "maintenance"}
    assert plan["question_count"] > 0
    assert plan["estimated_minutes"] > 0
    reasons = {item["reason"] for item in plan["items"]}
    assert any("到期" in reason or "答错" in reason for reason in reasons)
    # 可解释性：不允许把内部字段名暴露给用户
    assert all("due" != item["reason"] for item in plan["items"])
    assert body["stats"]["due_node_count"] >= 1
    assert len(body["trend"]) == 7
    # 没有练习记录时不伪造正确率
    assert body["stats"]["first_try_accuracy_7d"] is None
    assert body["recent_session"] is None


def test_practice_session_full_loop(client, practice_workspace):
    headers = practice_workspace["headers"]
    short_id = practice_workspace["short"]
    choice_id = practice_workspace["choice"]

    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "node", "node_ids": [practice_workspace["node_a"]], "count": 3},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    session = created.json()
    session_id = session["session"]["id"]
    assert session["session"]["status"] == "planned"
    assert len(session["items"]) == 3
    order = [item["exercise_id"] for item in session["items"]]
    assert session["current_exercise_id"] == order[0]

    # 刷新后恢复：同一个 Session 仍然返回同样的题目与进度
    resumed = client.get(f"/api/v1/practice/sessions/{session_id}", headers=headers)
    assert resumed.status_code == 200
    assert [item["exercise_id"] for item in resumed.json()["items"]] == order

    # 1) 首次独立答对
    first = order[0]
    exercise = next(item for item in session["items"] if item["exercise_id"] == first)
    correct_option = (
        "保证临界区同一时刻只有一个执行者"
        if exercise["question_type"] == "single_choice"
        else "错误"
    )
    if first == choice_id:
        correct_option = "保证临界区同一时刻只有一个执行者"
    answer_payload = {
        "exercise_id": first,
        "answer": correct_option,
        "duration_ms": 42_000,
    }
    result = client.post(
        f"/api/v1/practice/sessions/{session_id}/answer",
        json=answer_payload,
        headers=headers,
    )
    assert result.status_code == 201, result.text
    first_body = result.json()
    assert first_body["is_correct"] is True
    assert first_body["is_first_try_correct"] is True
    assert first_body["attempt_index"] == 1
    assert "首次独立" in first_body["schedule_reason"]
    assert first_body["next_review_at"] is not None

    # 2) 答错 → 允许重试 → 请求提示（不泄露答案）→ 重试答对
    second = order[1]
    wrong = client.post(
        f"/api/v1/practice/sessions/{session_id}/answer",
        json={"exercise_id": second, "answer": "提高 CPU 频率" if second == choice_id else "正确"},
        headers=headers,
    )
    assert wrong.status_code == 201, wrong.text
    wrong_body = wrong.json()
    assert wrong_body["is_correct"] is False
    assert wrong_body["retry_allowed"] is True
    assert wrong_body["explanation"] is None  # 首次错误不直接给完整答案

    hint = client.post(
        f"/api/v1/practice/sessions/{session_id}/items/{second}/hint", headers=headers
    )
    assert hint.status_code == 200, hint.text
    hint_body = hint.json()
    assert hint_body["hint"]
    assert hint_body["hint_count"] == 1

    reveal_before = client.get(
        f"/api/v1/practice/sessions/{session_id}/items/{second}/review", headers=headers
    )
    assert reveal_before.status_code == 200
    assert reveal_before.json()["explanation"]

    retry_answer = "保证临界区同一时刻只有一个执行者" if second == choice_id else "错误"
    retry = client.post(
        f"/api/v1/practice/sessions/{session_id}/answer",
        json={"exercise_id": second, "answer": retry_answer, "duration_ms": 15_000},
        headers=headers,
    )
    assert retry.status_code == 201, retry.text
    retry_body = retry.json()
    assert retry_body["is_correct"] is True
    assert retry_body["attempt_index"] == 2
    assert retry_body["is_first_try_correct"] is False
    assert retry_body["hint_count"] == 1
    assert "提示/重试" in retry_body["schedule_reason"]

    # 3) 简答题部分正确：score_ratio 与覆盖/缺失要点都要透传
    third = order[2]
    assert third == short_id
    partial = client.post(
        f"/api/v1/practice/sessions/{session_id}/answer",
        json={
            "exercise_id": third,
            "answer": "临界区里用互斥保证只有一个线程执行，也可以说信号量提供同样的互斥语义。",
            "duration_ms": 60_000,
        },
        headers=headers,
    )
    assert partial.status_code == 201, partial.text
    partial_body = partial.json()
    assert 0 < partial_body["score_ratio"] < 1
    assert partial_body["covered_points"]
    assert partial_body["missing_points"]
    assert partial_body["is_correct"] is True  # ratio >= 0.5 视为通过

    # 4) 完成 → 报告
    completed = client.post(
        f"/api/v1/practice/sessions/{session_id}/complete",
        json={"abandon": False},
        headers=headers,
    )
    assert completed.status_code == 200, completed.text
    report = completed.json()
    assert report["session"]["status"] == "completed"
    assert report["session"]["planned_question_count"] == 3
    assert report["session"]["completed_question_count"] == 3
    # 首次正确 2 题（第 1、3 题），重试后才正确的第 2 题不计入首次
    assert report["session"]["first_try_correct_count"] == 2
    assert report["session"]["final_correct_count"] == 3
    assert report["session"]["first_try_accuracy"] == pytest.approx(2 / 3, abs=1e-3)
    assert report["session"]["final_accuracy"] == 1.0
    assert report["session"]["total_attempt_count"] == 4
    assert report["next_review"]["due_at"] is not None
    # 「已巩固」与学习报告同源：本次产生过合格回忆（mastery_schedules.
    # last_qualified_recall_at）的节点就是已巩固，不再要求"首次全对"。
    assert report["consolidated"], "本次产生合格回忆的节点必须进入已巩固"
    assert report["consolidated"][0]["reason"] == "本次产生了合格回忆"
    assert report["consolidated"][0]["requires_attention"] is False
    # 同源校验：会话报告说已巩固，学习报告必须给出同样的结论
    learning = client.get(
        "/api/v1/practice/report", params={"window": "7d"}, headers=headers
    ).json()
    assert learning["consolidated_node_count"] >= 1, (
        "会话报告与学习报告的「已巩固」必须同源"
    )
    assert report["misconceptions"], "错误作答必须进入误区清单"
    assert report["misconceptions"][0]["node_label"] == "并发与同步"
    # 报告数据来自真实记录：掌握分变化只在真实变化时出现
    assert report["mastery_changes"], "答对后掌握分会真实变化"
    for change in report["mastery_changes"]:
        assert change["changed"] is True
        assert change["mastery_after"] is not None

    # 报告可重复读取
    again = client.get(
        f"/api/v1/practice/sessions/{session_id}/report", headers=headers
    )
    assert again.status_code == 200
    assert again.json()["session"]["id"] == session_id

    # 5) 调度写回：答对后 next_review_at 从过去推到未来
    overview = client.get("/api/v1/practice/overview", headers=headers).json()
    assert overview["recent_session"]["id"] == session_id
    assert overview["stats"]["answered_7d"] == 4
    assert overview["stats"]["first_try_accuracy_7d"] == pytest.approx(2 / 3, abs=0.01)


def test_answer_after_complete_is_rejected(client, practice_workspace):
    headers = practice_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "custom", "node_ids": [practice_workspace["node_b"]], "count": 1},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    session_id = body["session"]["id"]
    exercise_id = body["items"][0]["exercise_id"]
    client.post(
        f"/api/v1/practice/sessions/{session_id}/complete",
        json={"abandon": False},
        headers=headers,
    )
    late = client.post(
        f"/api/v1/practice/sessions/{session_id}/answer",
        json={"exercise_id": exercise_id, "answer": "时间片轮转"},
        headers=headers,
    )
    assert late.status_code == 409


def test_wrong_book_groups_by_node(client, practice_workspace):
    headers = practice_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "node", "node_ids": [practice_workspace["node_b"]], "count": 1},
        headers=headers,
    ).json()
    session_id = created["session"]["id"]
    exercise_id = created["items"][0]["exercise_id"]
    client.post(
        f"/api/v1/practice/sessions/{session_id}/answer",
        json={"exercise_id": exercise_id, "answer": "互斥"},
        headers=headers,
    )
    book = client.get("/api/v1/practice/wrong-book", headers=headers)
    assert book.status_code == 200, book.text
    body = book.json()
    assert body["total_wrong_questions"] == 1
    assert body["nodes"][0]["label"] == "死锁四个必要条件"
    assert body["nodes"][0]["graph_id"] == practice_workspace["graph_id"]
    assert body["nodes"][0]["recent_results"] == [False]
    assert body["nodes"][0]["exercises"][0]["error_type"] == "wrong_answer"


def test_learning_report_and_empty_state(client, register_user, stub_practice_provider):
    token, ws, *_ = register_user("empty")
    headers = {"Authorization": f"Bearer {token}", "X-Workspace-ID": ws}
    overview = client.get("/api/v1/practice/overview", headers=headers).json()
    assert overview["today_plan"]["mode"] == "empty"
    assert overview["today_plan"]["items"] == []
    assert overview["stats"]["pending_node_count"] == 0
    assert overview["stats"]["first_try_accuracy_7d"] is None
    assert overview["recent_session"] is None
    assert all(point["first_try_accuracy"] is None for point in overview["trend"])

    report = client.get("/api/v1/practice/report?window=7d", headers=headers)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["answered"] == 0
    assert body["first_try_accuracy"] is None
    assert body["delayed_recall"]["available"] is False
    assert body["delayed_recall"]["reason"]
    assert body["consolidated_node_count"] == 0


def test_scheduled_plan_session_and_resume(client, practice_workspace):
    headers = practice_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "scheduled", "question_type": "mixed"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["session"]["mode"] == "scheduled"
    assert body["items"], "今日练习必须真的能开始练习"
    assert body["plan"] is not None
    assert body["plan"]["items"], "今日练习计划必须带可解释理由"
    node_ids = {item["node_id"] for item in body["items"]}
    assert len(node_ids) >= 1
    # 交错排序：同一知识点的题目不会被整块堆在一起，答错后几题才会再次遇到同节点变式
    positions: dict[str, list[int]] = {}
    for index, item in enumerate(body["items"]):
        positions.setdefault(item["node_id"], []).append(index)
    for node_id, index_list in positions.items():
        if len(index_list) < 2 or len(node_ids) < 2:
            continue
        assert index_list != list(range(index_list[0], index_list[0] + len(index_list))), (
            f"节点 {node_id} 的题目被整块排列：{index_list}"
        )


def test_short_answer_reveal_requires_attempt(client, practice_workspace):
    headers = practice_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "node", "node_ids": [practice_workspace["node_a"]], "count": 3},
        headers=headers,
    ).json()
    session_id = created["session"]["id"]
    short = practice_workspace["short"]
    reveal = client.get(
        f"/api/v1/practice/sessions/{session_id}/items/{short}/review", headers=headers
    )
    assert reveal.status_code == 409
    answer = client.post(
        f"/api/v1/practice/sessions/{session_id}/answer",
        json={"exercise_id": short, "answer": "不知道"},
        headers=headers,
    )
    assert answer.status_code == 201
    assert answer.json()["error_type"] == "no_attempt"
    reveal = client.get(
        f"/api/v1/practice/sessions/{session_id}/items/{short}/review", headers=headers
    )
    assert reveal.status_code == 200
    assert reveal.json()["answer_display"]


def test_minimal_session_does_not_require_model(client, practice_workspace, monkeypatch):
    """无远程模型时：已有题库仍可练习，缺题库的节点被明确跳过而不伪造题目。"""

    from app.api.routers import practice as practice_router

    class UnavailableProvider:
        available = False
        provider_id = "unavailable"
        remote_capability = False
        reason = "no remote model configured"
        last_usage: dict[str, int] = {}

    monkeypatch.setattr(
        practice_router, "model_provider_for_workspace", lambda *a, **k: UnavailableProvider()
    )
    headers = practice_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "node", "node_ids": [practice_workspace["node_a"]], "count": 3},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["items"]

    missing = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "custom", "node_ids": [new_id()], "count": 2},
        headers=headers,
    )
    assert missing.status_code == 404
