"""练习计划生成回归测试。

现象：图谱节点**还没有题库**、但已到期时，「今日练习」点「开始练习」会直接 409
「今天没有可以真正作答的题目：计划中的知识点还没有题库，且当前没有可用的远程模型可以出题。」
即使工作区已经配好了可用的远程模型。

根因预期：``build_today_plan`` 把"没有题库"的节点放进 ``skipped_nodes``，
而 ``create_session`` 只遍历 ``plan.items``，于是这些节点永远不会触发出题。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.database import SessionLocal
from app.domain.models import (
    Graph,
    GraphNode,
    Goal,
    MasterySchedule,
    new_id,
    utc_now,
)


class GeneratingProvider:
    """可用的远程模型桩：真的会被调用来出题。"""

    available = True
    provider_id = "practice-generating-stub"
    model_id = "practice-generating-stub-model"
    remote_capability = True
    thinking_mode = "off"
    actual_reasoning_effort = None
    search_route = "disabled"
    max_output_tokens = 2048
    last_usage: dict[str, int] = {}

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate_json(self, prompt, schema_name, schema):
        self.calls.append({"schema_name": schema_name, "prompt": prompt})
        return {
            "items": [
                {
                    "question_type": "single_choice",
                    "prompt": f"为「无题库节点」生成的题目 {index + 1}",
                    "options": ["甲", "乙", "丙", "丁"],
                    "answer_key": "甲",
                    "explanation": "因为甲。",
                    "rubric_points": [],
                    "hint": "先回忆该知识点的定义。",
                    "source_chunk_ids": [],
                }
                for index in range(3)
            ]
        }


@pytest.fixture()
def generating_provider(monkeypatch):
    from app.api.routers import exercises as exercises_router
    from app.api.routers import practice as practice_router

    provider = GeneratingProvider()
    monkeypatch.setattr(
        practice_router, "model_provider_for_workspace", lambda *a, **k: provider
    )
    monkeypatch.setattr(
        exercises_router, "model_provider_for_workspace", lambda *a, **k: provider
    )
    return provider


def _seed_due_nodes_without_exercises(workspace_id: str) -> dict[str, str]:
    """一个图谱 + 两个到期节点，但**没有任何题库**。"""

    with SessionLocal() as db:
        goal = Goal(
            id=new_id(),
            workspace_id=workspace_id,
            title="数据结构",
            raw_prompt="学习数据结构",
            status="active",
        )
        db.add(goal)
        graph = Graph(
            id=new_id(),
            workspace_id=workspace_id,
            goal_id=goal.id,
            title="数据结构图谱",
            status="published",
        )
        db.add(graph)
        node_a = GraphNode(
            id=new_id(),
            workspace_id=workspace_id,
            graph_id=graph.id,
            label="红黑树",
            description="红黑树是一种自平衡二叉查找树，通过颜色约束保证树高为 O(log n)。",
            target_weight=90,
        )
        node_b = GraphNode(
            id=new_id(),
            workspace_id=workspace_id,
            graph_id=graph.id,
            label="并查集",
            description="并查集支持近乎常数时间的合并与查询，常用路径压缩与按秩合并。",
            target_weight=60,
        )
        db.add_all([node_a, node_b])
        for node in (node_a, node_b):
            db.add(
                MasterySchedule(
                    id=new_id(),
                    workspace_id=workspace_id,
                    node_id=node.id,
                    next_review_at=utc_now() - timedelta(days=2),
                )
            )
        db.commit()
        return {"graph_id": graph.id, "node_a": node_a.id, "node_b": node_b.id}


@pytest.fixture()
def no_bank_workspace(client, register_user):
    token, ws, *_ = register_user("nobank")
    seeded = _seed_due_nodes_without_exercises(ws)
    headers = {"Authorization": f"Bearer {token}", "X-Workspace-ID": ws}
    return {"token": token, "ws": ws, "headers": headers, **seeded}


def test_plan_marks_nodes_without_bank_as_generatable(
    client, no_bank_workspace, generating_provider
):
    headers = no_bank_workspace["headers"]
    overview = client.get("/api/v1/practice/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    plan = overview.json()["today_plan"]
    assert plan["mode"] != "empty", "节点已到期，计划不应是空的"
    assert plan["provider_available"] is True
    assert plan["items"], "模型可用时，没有题库的节点必须留在计划里（否则永远出不了题）"
    assert all(item["available_exercises"] == 0 for item in plan["items"])
    assert not plan["skipped_nodes"], "有模型可用时不该把节点标记为无法出题"
    assert all("远程模型" in item["detail"] for item in plan["items"])
    # 计划口径也要跟上：这些题是真的会被生成的，不能报成 0 道题
    assert plan["question_count"] == sum(
        item["planned_questions"] for item in plan["items"]
    )
    assert plan["question_count"] > 0


def test_scheduled_session_generates_for_nodes_without_bank(
    client, no_bank_workspace, generating_provider
):
    """核心断言：模型可用时，今日练习必须真的出题并开始，而不是 409。"""

    headers = no_bank_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "scheduled", "question_type": "mixed"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["items"], "今日练习必须真的能作答"
    assert generating_provider.calls, "缺少题库时应该调用远程模型出题"


def test_scheduled_session_without_model_reports_honestly(
    client, no_bank_workspace, monkeypatch
):
    """模型不可用时：必须明确说明原因，且不得伪造题目。"""

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
    headers = no_bank_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "scheduled", "question_type": "mixed"},
        headers=headers,
    )
    assert created.status_code == 409
    payload = created.json()["error"]
    assert payload["code"] == "practice_plan_unavailable"
    assert payload["details"]["provider_available"] is False
    assert payload["details"]["skipped_nodes"], "必须把没有题库的节点告诉前端"
    assert "远程模型" in payload["message"]


class NodeSensitiveProvider(GeneratingProvider):
    """只对「红黑树」这个知识点失败，模拟单个节点出题失败。"""

    def generate_json(self, prompt, schema_name, schema):
        if "红黑树" in prompt:
            raise RuntimeError("provider exploded for this node")
        return super().generate_json(prompt, schema_name, schema)


@pytest.fixture()
def flaky_provider(monkeypatch):
    from app.api.routers import exercises as exercises_router
    from app.api.routers import practice as practice_router

    provider = NodeSensitiveProvider()
    monkeypatch.setattr(
        practice_router, "model_provider_for_workspace", lambda *a, **k: provider
    )
    monkeypatch.setattr(
        exercises_router, "model_provider_for_workspace", lambda *a, **k: provider
    )
    return provider


def test_scheduled_session_survives_one_failing_node(client, no_bank_workspace, flaky_provider):
    """某个知识点出题失败时：其余知识点照常开始，且把原因如实带回前端。"""

    headers = no_bank_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "scheduled", "question_type": "mixed"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["items"], "一个节点出题失败不该让整场今日练习失败"
    labels = {item["node_label"] for item in body["items"]}
    assert "红黑树" not in labels
    assert body["warnings"], "少题必须显式告知，不能静默"
    assert any("红黑树" in warning and "出题失败" in warning for warning in body["warnings"])


def test_scheduled_session_raises_when_every_node_fails(client, no_bank_workspace, monkeypatch):
    """全部知识点都出不了题时：保留模型的真实报错，不降级成假题。"""

    from app.api.routers import exercises as exercises_router
    from app.api.routers import practice as practice_router

    class AlwaysFailingProvider(GeneratingProvider):
        def generate_json(self, prompt, schema_name, schema):
            raise RuntimeError("provider exploded")

    provider = AlwaysFailingProvider()
    monkeypatch.setattr(
        practice_router, "model_provider_for_workspace", lambda *a, **k: provider
    )
    monkeypatch.setattr(
        exercises_router, "model_provider_for_workspace", lambda *a, **k: provider
    )
    headers = no_bank_workspace["headers"]
    created = client.post(
        "/api/v1/practice/sessions",
        json={"mode": "scheduled", "question_type": "mixed"},
        headers=headers,
    )
    assert created.status_code == 502
    payload = created.json()["error"]
    assert payload["code"] == "structured_generation_failed"
    assert "RuntimeError" in payload["message"]
    assert payload["details"]["errors"], "必须带上模型侧的真实失败原因"
