"""审计修复的回归测试。

覆盖：
1. 错题重练（mode=wrong_book）只抽"真的答错过"的题；
2. 学习报告的「全部」窗口不再被截到 90 天，「全部」= 全部；
3. status_label 不漏内部状态码（unverified/fresh/...）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.database import SessionLocal
from app.domain.models import (
    AnswerRecord,
    Exercise,
    Graph,
    GraphNode,
    Goal,
    MasterySchedule,
    new_id,
    utc_now,
)


def _seed(workspace_id: str) -> dict:
    now = utc_now()
    with SessionLocal() as db:
        goal = Goal(
            id=new_id(),
            workspace_id=workspace_id,
            title="审计",
            raw_prompt="审计",
            status="active",
        )
        db.add(goal)
        graph = Graph(
            id=new_id(),
            workspace_id=workspace_id,
            goal_id=goal.id,
            title="审计图谱",
            status="published",
        )
        db.add(graph)
        node = GraphNode(
            id=new_id(),
            workspace_id=workspace_id,
            graph_id=graph.id,
            label="节点A",
            description="A",
        )
        db.add(node)
        wrong = Exercise(
            id=new_id(),
            workspace_id=workspace_id,
            node_id=node.id,
            question_type="single_choice",
            prompt="答错过的题",
            options=["甲", "乙", "丙", "丁"],
            answer_key="甲",
            explanation="",
            difficulty="medium",
            metadata_json={},
        )
        fresh = Exercise(
            id=new_id(),
            workspace_id=workspace_id,
            node_id=node.id,
            question_type="single_choice",
            prompt="从未作答的题",
            options=["甲", "乙", "丙", "丁"],
            answer_key="甲",
            explanation="",
            difficulty="medium",
            metadata_json={},
        )
        db.add_all([wrong, fresh])
        db.add(
            MasterySchedule(
                id=new_id(),
                workspace_id=workspace_id,
                node_id=node.id,
                next_review_at=now - timedelta(days=1),
            )
        )
        db.commit()
        # 一条 120 天前的答错记录：早于旧的 90 天截断线
        db.add(
            AnswerRecord(
                workspace_id=workspace_id,
                exercise_id=wrong.id,
                answer="乙",
                is_correct=False,
                feedback="错了",
                actor_id=None,
                attempt_index=1,
                duration_ms=1_000,
                hint_count=0,
                score_ratio=0.0,
                evaluation_json={"error_type": "wrong_judgement", "missing_points": []},
                created_at=now - timedelta(days=120),
                updated_at=now - timedelta(days=120),
            )
        )
        db.commit()
        return {"node": node.id, "wrong": wrong.id, "fresh": fresh.id}


@pytest.fixture()
def audit_workspace(client, register_user):
    token, ws, *_ = register_user("auditscope")
    seeded = _seed(ws)
    headers = {"Authorization": f"Bearer {token}", "X-Workspace-ID": ws}
    return {"token": token, "ws": ws, "headers": headers, **seeded}


def test_wrong_book_session_only_picks_wrong_questions(client, audit_workspace, monkeypatch):
    """错题重练不能把从未答错的题混进来。"""

    from app.api.routers import practice as practice_router

    class UnavailableProvider:
        available = False
        provider_id = "unavailable"
        remote_capability = False
        reason = "no remote model configured"
        last_usage: dict[str, int] = {}

    monkeypatch.setattr(
        practice_router,
        "model_provider_for_workspace",
        lambda *a, **k: UnavailableProvider(),
    )
    headers = audit_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={
            "mode": "wrong_book",
            "node_ids": [audit_workspace["node"]],
            "question_type": "mixed",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    picked = {item["exercise_id"] for item in created.json()["items"]}
    assert picked == {audit_workspace["wrong"]}, f"错题重练抽到了无关题：{picked}"

    wrong_book = client.get("/api/v1/practice/wrong-book", headers=headers).json()
    node = wrong_book["nodes"][0]
    assert node["wrong_question_count"] == 1
    assert node["exercises"][0]["error_type"] == "wrong_judgement"


def test_wrong_book_without_history_falls_back_visibly(client, register_user, monkeypatch):
    """没有历史错题的节点：退回普通练习，但必须如实告知。"""

    from app.api.routers import practice as practice_router

    class UnavailableProvider:
        available = False
        provider_id = "unavailable"
        remote_capability = False
        reason = "no remote model configured"
        last_usage: dict[str, int] = {}

    monkeypatch.setattr(
        practice_router,
        "model_provider_for_workspace",
        lambda *a, **k: UnavailableProvider(),
    )
    token, ws, *_ = register_user("auditnohistory")
    seeded = _seed(ws)
    headers = {"Authorization": f"Bearer {token}", "X-Workspace-ID": ws}
    with SessionLocal() as db:
        db.query(AnswerRecord).filter(AnswerRecord.workspace_id == ws).delete()
        db.commit()
    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "wrong_book", "node_ids": [seeded["node"]], "question_type": "mixed"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert any(
        "没有历史错题" in warning for warning in created.json()["warnings"]
    ), created.json()["warnings"]


def test_learning_report_all_window_has_no_90_day_cut(client, audit_workspace):
    """「全部」窗口必须覆盖 120 天前的作答，且状态标签不漏内部码。"""

    headers = audit_workspace["headers"]
    report = client.get(
        "/api/v1/practice/report", params={"window": "all"}, headers=headers
    ).json()
    assert report["answered"] == 1, "「全部」窗口把 120 天前的作答漏掉了"
    assert report["window_start"][:10] < (
        utc_now() - timedelta(days=100)
    ).strftime("%Y-%m-%d")
    assert report["nodes"], "120 天前的作答必须出现在知识点表现里"
    label = report["nodes"][0]["status_label"]
    assert label not in {"unverified", "due", "fresh", "relearning"}, (
        f"状态标签漏了内部码：{label}"
    )


def test_session_first_try_accuracy_uses_answered_denominator(client, audit_workspace):
    """首次正确率以"已作答的题"为分母：未作答不按 0 分拉低。"""

    headers = audit_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={
            "mode": "node",
            "node_ids": [audit_workspace["node"]],
            "count": 2,
            "question_type": "mixed",
        },
        headers=headers,
    ).json()
    session_id = created["session"]["id"]
    first = created["items"][0]["exercise_id"]
    # 只作答 1 题（且答对），另一题留空
    answer = client.post(
        f"/api/v1/practice/sessions/{session_id}/answer",
        json={"exercise_id": first, "answer": "甲", "duration_ms": 4_000},
        headers=headers,
    ).json()
    assert answer["is_correct"] is True, answer
    report = client.post(
        f"/api/v1/practice/sessions/{session_id}/complete",
        json={"abandon": False},
        headers=headers,
    ).json()
    assert report["session"]["planned_question_count"] == 2
    assert report["session"]["completed_question_count"] == 1
    assert report["session"]["first_try_accuracy"] == pytest.approx(1.0), (
        "未作答的题不能进首次正确率的分母"
    )
    assert report["session"]["final_accuracy"] == pytest.approx(1.0)


def test_wrong_answer_enters_mastery_projection(client, audit_workspace):
    """答错必须进掌握投影：拉低掌握分并写进误区，而不是只出现在会话报告里。"""

    headers = audit_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={
            "mode": "node",
            "node_ids": [audit_workspace["node"]],
            "count": 2,
            "question_type": "mixed",
        },
        headers=headers,
    ).json()
    session_id = created["session"]["id"]
    # 第 1 题（历史错题）答对，第 2 题答错 → 掌握投影必须同时看到两条证据
    ids = [item["exercise_id"] for item in created["items"]]
    for exercise_id, value in zip(ids, ["甲", "乙"]):
        client.post(
            f"/api/v1/practice/sessions/{session_id}/answer",
            json={"exercise_id": exercise_id, "answer": value, "duration_ms": 5_000},
            headers=headers,
        )
    assert client.post(
        f"/api/v1/practice/sessions/{session_id}/complete",
        json={"abandon": False},
        headers=headers,
    ).status_code == 200

    with SessionLocal() as db:
        state = db.execute(
            text(
                "select status, mastery_score, confidence, evidence_count,"
                " misconceptions_json from learning_node_states where workspace_id = :w"
            ),
            {"w": audit_workspace["ws"]},
        ).first()
    assert state is not None, "做答后必须产生掌握状态投影"
    status_value, mastery, confidence, evidence_count, misconceptions = state
    assert evidence_count == 2, "答错的证据没有被投影统计"
    assert mastery is not None and 0.0 < mastery < 1.0, f"答错后掌握分不应满分：{mastery}"
    # 一次答对 + 一次答错 → 加权掌握分被拉到 1.0 以下，且绝不能被判为已掌握
    assert status_value != "mastered", status_value
    assert misconceptions, "答错必须写进 misconceptions"
