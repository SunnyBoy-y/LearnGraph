"""练习模型路由回归测试。

现象：复习与练习中心点「开始今日练习」报
「远程模型连续 3 次都没有返回可用的结果：ProviderHTTPError: Provider returned
HTTP 404 ... "is not supported by any configured account in this group"」。
用户既不知道该去哪里改模型（练习中心没有任何指定模型的入口），也被同一个必然失败的
请求打了 3 次。

覆盖三件事：
1. 接线级：工作区设置 ``practice.exercise_model`` 必须真的被练习路由读走并传给
   provider 工厂（未配置时保持旧的回落行为）。
2. 行为级：Provider 明确拒绝请求（HTTP 404 unknown model）时禁止重试，且报错必须
   指向「设置 → 功能模型」。
3. 前置级：显式指定的模型若已从 Provider 的清单里消失，必须在调用前判定不可用。
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
from app.domain.settings import PRACTICE_EXERCISE_MODEL_SETTING_KEY
from app.providers.factory import _pinned_model_rejection
from app.providers.remote.openai import ProviderHTTPError


class GeneratingProvider:
    """可用且真的会被调用的出题桩。"""

    available = True
    provider_id = "practice-routing-stub"
    model_id = "practice-routing-stub-model"
    remote_capability = True
    thinking_mode = "off"
    actual_reasoning_effort = None
    search_route = "disabled"
    max_output_tokens = 2048
    last_usage: dict[str, int] = {}
    last_request_id: str | None = None

    def __init__(self) -> None:
        self.calls = 0

    def generate_json(self, prompt, schema_name, schema):
        self.calls += 1
        return {
            "items": [
                {
                    "question_type": "single_choice",
                    "prompt": f"路由桩题目 {index + 1}",
                    "options": ["甲", "乙", "丙", "丁"],
                    "answer_key": "甲",
                    "explanation": "因为甲。",
                    "rubric_points": [],
                    "hint": "先回忆定义。",
                    "source_chunk_ids": [],
                }
                for index in range(3)
            ]
        }


class RejectingProvider(GeneratingProvider):
    """像被下线的网关一样，对每次调用都回 HTTP 404。"""

    provider_id = "practice-rejecting-stub"
    model_id = "deepseek-v4.1-flash-expires-on-0910"

    def generate_json(self, prompt, schema_name, schema):
        self.calls += 1
        raise ProviderHTTPError(
            "Provider returned HTTP 404; request_id=req-1; body="
            '{"error":{"message":"Model \\"'
            f"{self.model_id}"
            '\\" is not supported by any configured account in this group",'
            '"type":"model_not_found"}}',
            status_code=404,
        )


@pytest.fixture()
def due_node_workspace(client, register_user):
    """一个到期但没有任何题库的知识点。"""

    token, ws, *_ = register_user("practice-model")
    with SessionLocal() as db:
        goal = Goal(
            id=new_id(),
            workspace_id=ws,
            title="数据结构",
            raw_prompt="学习数据结构",
            status="active",
        )
        db.add(goal)
        graph = Graph(
            id=new_id(),
            workspace_id=ws,
            goal_id=goal.id,
            title="数据结构图谱",
            status="published",
        )
        db.add(graph)
        node = GraphNode(
            id=new_id(),
            workspace_id=ws,
            graph_id=graph.id,
            label="红黑树",
            description="红黑树是一种自平衡二叉查找树，通过颜色约束保证树高为 O(log n)。",
            target_weight=90,
        )
        db.add(node)
        db.add(
            MasterySchedule(
                id=new_id(),
                workspace_id=ws,
                node_id=node.id,
                next_review_at=utc_now() - timedelta(days=2),
            )
        )
        db.commit()
    return {
        "ws": ws,
        "node_id": node.id,
        "headers": {"Authorization": f"Bearer {token}", "X-Workspace-ID": ws},
    }


def _start_today_practice(client, headers):
    return client.post(
        "/api/v1/practice/sessions",
        json={"mode": "scheduled", "question_type": "mixed"},
        headers=headers,
    )


def test_practice_route_passes_the_designated_model_to_the_factory(
    client, due_node_workspace, monkeypatch
):
    """接线级：设置里指定的模型必须真的走到 provider 解析处。"""

    from app.api.routers import practice as practice_router

    captured: dict[str, object] = {}

    def factory(db, workspace_id, settings, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return GeneratingProvider()

    monkeypatch.setattr(practice_router, "model_provider_for_workspace", factory)

    # 未配置：保持既有行为（不指定 → 工厂自己回落工作区默认路由）
    created = _start_today_practice(client, due_node_workspace["headers"])
    assert created.status_code == 201, created.text
    assert captured == {}, "没有配置时不得凭空虚造 provider_id/model_id"

    # 通过设置接口保存（顺带覆盖 settings 校验分支与解析缓存失效）
    saved = client.put(
        f"/api/v1/settings/{PRACTICE_EXERCISE_MODEL_SETTING_KEY}",
        json={"value": {"provider_id": "prov-9", "model_id": "model-9"}},
        headers=due_node_workspace["headers"],
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["value"] == {"provider_id": "prov-9", "model_id": "model-9"}

    created = _start_today_practice(client, due_node_workspace["headers"])
    assert created.status_code == 201, created.text
    assert captured == {"provider_id": "prov-9", "model_id": "model-9"}


def test_provider_rejection_is_not_retried_and_names_the_fix(
    client, due_node_workspace, monkeypatch
):
    """HTTP 404 unknown model 是不可重试错误：只打一次，且要指路。"""

    from app.api.routers import practice as practice_router

    provider = RejectingProvider()
    monkeypatch.setattr(
        practice_router, "model_provider_for_workspace", lambda *a, **k: provider
    )

    created = _start_today_practice(client, due_node_workspace["headers"])
    assert created.status_code == 502, created.text
    error = created.json()["error"]
    assert error["code"] == "remote_model_rejected_request"
    assert provider.calls == 1, "被 Provider 拒绝的请求不得重试 3 次"
    assert "功能模型" in error["message"], "报错必须告诉用户去哪里改模型"
    assert error["details"]["attempts"] == 1
    assert error["details"]["model_id"] == RejectingProvider.model_id


def test_deleted_model_is_rejected_before_the_call():
    capabilities = {"hidden_model_ids": ["gone-model"]}
    reason = _pinned_model_rejection(capabilities, "gone-model")
    assert reason and "removed" in reason


def test_model_missing_from_the_latest_catalog_is_rejected():
    capabilities = {
        "discovered_model_ids": ["still-here"],
        "models": {"still-here": {"context_window_tokens": 128000}},
        "default_model": "still-here",
    }
    assert _pinned_model_rejection(capabilities, "still-here") is None
    reason = _pinned_model_rejection(capabilities, "vanished-model")
    assert reason and "no longer" in reason


def test_never_discovered_provider_asserts_nothing():
    """没有发现快照时不能凭空判定模型不存在（手填默认模型是允许的）。"""

    assert _pinned_model_rejection({}, "hand-typed-model") is None
    assert _pinned_model_rejection({"model_states": {}}, "hand-typed-model") is None
