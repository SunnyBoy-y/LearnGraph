"""练习页「去设置换模型」出口的回归测试。

背景：Provider 可用但模型名在该账号组里不存在时（HTTP 404 model_not_found），
出题会以 ``remote_model_rejected_request`` 失败。用户此时能在「设置 → 功能模型
→ 练习出题与判分模型」换模型修好，所以后端必须把这个事实做成机器可读的字段
（``model_setting_required``），UI 才有依据给出直达设置的按钮。

覆盖：
1. 组卷时模型拒答 → 会话视图 ``model_setting_required`` 为 True，且警告里带原因；
2. 该标记随会话落库，刷新详情页（GET session）仍然为 True；
3. 同一个拒绝错误在「今日练习」计划里也把 ``model_setting_required`` 置为 True；
4. 非模型侧错误（普通网络/超时）不会把用户误导到设置页。
"""

from __future__ import annotations

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


class RejectedModelError(RuntimeError):
    """模拟 ProviderHTTPError：带 HTTP 状态码，因此是非可重试的拒绝。"""

    def __init__(self, status_code: int = 404) -> None:
        super().__init__('Provider returned HTTP 404; model_not_found')
        self.status_code = status_code


class RejectingModelProvider:
    """可用、有远程结构化能力，但每次出题都被上游拒绝。"""

    available = True
    remote_capability = True
    provider_id = "provider-under-test"
    model_id = "deepseek-v4.1-flash-expires-on-0910"
    max_output_tokens = 1024
    last_usage: dict[str, int] = {}
    last_request_id = "req-test"

    def generate_json(self, prompt, schema_name, schema):  # noqa: ANN001, ARG002
        raise RejectedModelError()

    def stream_answer(self, prompt):  # noqa: ANN001, ARG002
        raise RejectedModelError()


class BrokenTransportProvider(RejectingModelProvider):
    """传输层故障：重试后仍失败，但换模型救不了，不该给设置入口。"""

    def generate_json(self, prompt, schema_name, schema):  # noqa: ANN001, ARG002
        raise TimeoutError("connection reset by peer")


def _seed(workspace_id: str) -> dict:
    """一个到期知识点 + 一条已有题目（保证计划里排得进一个节点）。"""
    now = utc_now()
    with SessionLocal() as db:
        goal = Goal(
            id=new_id(),
            workspace_id=workspace_id,
            title="出题模型",
            raw_prompt="出题模型",
            status="active",
        )
        db.add(goal)
        graph = Graph(
            id=new_id(),
            workspace_id=workspace_id,
            goal_id=goal.id,
            title="出题模型图谱",
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
        db.add(
            Exercise(
                id=new_id(),
                workspace_id=workspace_id,
                node_id=node.id,
                question_type="single_choice",
                prompt="已有的题",
                options=["甲", "乙", "丙", "丁"],
                answer_key="甲",
                explanation="",
                difficulty="medium",
                metadata_json={},
            )
        )
        db.add(
            MasterySchedule(
                id=new_id(),
                workspace_id=workspace_id,
                node_id=node.id,
                next_review_at=now - timedelta(days=1),
            )
        )
        db.commit()
        return {"node": node.id}


@pytest.fixture()
def practice_workspace(client, register_user):
    token, ws, *_ = register_user("modelhint")
    seeded = _seed(ws)
    headers = {"Authorization": f"Bearer {token}", "X-Workspace-ID": ws}
    return {"token": token, "ws": ws, "headers": headers, **seeded}


def _patch_provider(monkeypatch, provider) -> None:
    from app.api.routers import practice as practice_router

    monkeypatch.setattr(
        practice_router, "model_provider_for_workspace", lambda *a, **k: provider
    )


def test_rejected_model_marks_session_and_persists(client, practice_workspace, monkeypatch):
    _patch_provider(monkeypatch, RejectingModelProvider())
    headers = practice_workspace["headers"]

    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "scheduled", "question_type": "mixed"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["model_setting_required"] is True, body
    assert any("出题失败" in warning for warning in body["warnings"]), body["warnings"]
    assert any(
        "练习出题与判分模型" in warning for warning in body["warnings"]
    ), body["warnings"]

    session_id = body["session"]["id"]
    refreshed = client.get(f"/api/v1/practice/sessions/{session_id}", headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["model_setting_required"] is True, refreshed.json()


def test_unavailable_provider_marks_today_plan(client, practice_workspace, monkeypatch):
    """没有可用模型（含选中模型已失效）时，计划本身就要带出「去设置」的依据。

    注意：Provider 明明可用、只是模型被上游拒绝的情况，计划阶段无从得知（那要
    真的发出一题才知道），所以那条路径靠组卷失败的 AppError 编码来暴露，见下一个
    用例与前端 startErrorNeedsModel。
    """

    class UnavailableProvider:
        available = False
        remote_capability = False
        provider_id = "model_provider_unavailable"
        model_id = "unavailable"
        reason = "The selected model is no longer in this Provider's model list"
        last_usage: dict[str, int] = {}

    _patch_provider(monkeypatch, UnavailableProvider())
    headers = practice_workspace["headers"]

    overview = client.get("/api/v1/practice/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    today_plan = overview.json()["today_plan"]
    assert today_plan["provider_available"] is False, today_plan
    assert today_plan["model_setting_required"] is True, today_plan


def test_rejected_model_only_surfaces_at_generation(client, practice_workspace, monkeypatch):
    """Provider 可用但模型被拒：计划阶段不冒认，组卷时才带上模型侧标记。"""
    _patch_provider(monkeypatch, RejectingModelProvider())
    headers = practice_workspace["headers"]

    today_plan = client.get("/api/v1/practice/overview", headers=headers).json()[
        "today_plan"
    ]
    assert today_plan["provider_available"] is True, today_plan
    assert today_plan["model_setting_required"] is False, today_plan

    # 节点还有旧题：组卷不会整个失败，但要少题，并且必须带上模型侧标记。
    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "custom", "node_ids": [practice_workspace["node"]], "count": 3},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["model_setting_required"] is True, body
    assert body["session"]["planned_question_count"] < 3, body["session"]

    # 一个完全没题的节点：组卷整个失败，错误详情里也要有同样的标记，
    # 前端据 AppError.code 决定是否给出「去设置」按钮。
    empty_node = new_id()
    with SessionLocal() as db:
        db.add(
            GraphNode(
                id=empty_node,
                workspace_id=practice_workspace["ws"],
                graph_id=db.query(Graph)
                .filter(Graph.workspace_id == practice_workspace["ws"])
                .first()
                .id,
                label="空节点",
                description="没有题目",
            )
        )
        db.commit()
    failed = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "custom", "node_ids": [empty_node], "count": 3},
        headers=headers,
    )
    assert failed.status_code == 502, failed.text
    error = failed.json()["error"]
    assert error["code"] == "remote_model_rejected_request", error
    assert "练习出题与判分模型" in error["message"], error
    assert error["details"]["feature"] == "exercise_generate", error["details"]


def test_transport_failure_does_not_point_to_model_settings(
    client, practice_workspace, monkeypatch
):
    _patch_provider(monkeypatch, BrokenTransportProvider())
    headers = practice_workspace["headers"]

    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "scheduled", "question_type": "mixed"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["model_setting_required"] is False, body
