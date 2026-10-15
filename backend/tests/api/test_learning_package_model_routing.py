"""工作区设置「教学包生成模型」（``learning.package_model``）必须真的生效。

覆盖三件事：
1. 设置能被登记与校验（成对写入；半写必须 422）——它同时是「设置 → 功能模型」卡片的
   数据来源，登记漏了前端就没有这项；
2. 未配置时不得凭空虚造 provider/model（保持回落到工作区对话模型）；
3. 配置后，教学包生成的每一次调用都必须走这个模型——包括阶段内并发的两路。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.learning_package_models import LearningBuild
from app.domain.models import DurableJob, Goal, Graph, GraphNode, new_id
from app.domain.settings import LEARNING_PACKAGE_MODEL_SETTING_KEY
from app.services.durable_queue import DurableQueue

BLUEPRINT = {
    "title": "烹饪课",
    "objectives": ["掌握顺序"],
    "sections": ["热锅", "下菜"],
    "activity_brief": "烹饪小剧场",
    "image_prompt": "",
    "estimated_minutes": 15,
}
LESSON = {
    "sections": [
        {"id": "s0", "title": "步骤", "body": "先了解热锅的作用，观察食物的变化并判断是否完成。", "takeaway": "观察后操作"},
        {"id": "s1", "title": "火候", "body": "火候决定口感，先中火预热，再按食材调整火力大小。", "takeaway": "先中火"},
    ],
    "svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="80" height="80"/></svg>',
    "caption": "锅具图解",
    "html": "",
}
ACTIVITY = {
    "title": "炒菜实验",
    "instructions": "先热锅再放菜，观察温度。",
    "variables": [{"id": "heat", "label": "温度", "initial": 0, "minimum": 0, "maximum": 2}],
    "actions": [
        {"id": "heat", "label": "热锅", "requires": [{"variable": "heat", "operator": "eq", "value": 0}],
         "effects": [{"variable": "heat", "operation": "set", "value": 1}], "feedback": "锅已热"},
        {"id": "cook", "label": "下菜", "requires": [{"variable": "heat", "operator": "eq", "value": 1}],
         "effects": [{"variable": "heat", "operation": "set", "value": 2}], "feedback": "完成"},
    ],
    "success": [{"variable": "heat", "operator": "eq", "value": 2}],
    "solution": ["heat", "cook"],
}
PAYLOADS = {"Blueprint": BLUEPRINT, "Lesson": LESSON, "Activity": ACTIVITY}


class StubProvider:
    """可用、只回答 Blueprint/Lesson/Activity 的结构化桩。"""

    available = True
    provider_id = "learning-routing-stub"
    model_id = "learning-routing-stub-model"
    remote_capability = True
    thinking_mode = "off"
    actual_reasoning_effort = None
    search_route = "disabled"
    max_output_tokens = 142_000
    last_usage: dict[str, int] = {}
    last_request_id: str | None = None

    def generate_json(self, prompt, schema_name, schema):
        from copy import deepcopy

        return deepcopy(PAYLOADS[schema_name])


@pytest.fixture()
def learning_node(client, register_user):
    token, ws, *_ = register_user("learning-model")
    with SessionLocal() as db:
        goal = Goal(id=new_id(), workspace_id=ws, title="烹饪", raw_prompt="学炒菜", status="active")
        db.add(goal)
        graph = Graph(id=new_id(), workspace_id=ws, goal_id=goal.id, title="烹饪图谱", status="published")
        db.add(graph)
        node = GraphNode(
            id=new_id(),
            workspace_id=ws,
            graph_id=graph.id,
            label="热锅与火候",
            description="观察食材在热锅中的变化并判断火候。",
            node_type="practice",
            target_weight=80,
        )
        db.add(node)
        db.commit()
    return {
        "ws": ws,
        "node_id": node.id,
        "headers": {"Authorization": f"Bearer {token}", "X-Workspace-ID": ws},
    }


def _enqueue(client, headers, node_id):
    created = client.post(
        f"/api/v1/learning-pages/nodes/{node_id}/build",
        json={"trigger": "on_demand"},
        headers=headers,
    )
    assert created.status_code == 202, created.text
    return created.json()


def _lease_build(build_id: str) -> DurableJob:
    with SessionLocal() as db:
        # 只取 id：把完整实体加载进 identity map 会让 claim_next 的
        # synchronize_session 去比较 naive/aware 时间（SQLite 存的是 naive）。
        job_id = db.scalar(
            select(DurableJob.id).where(DurableJob.payload["build_id"].as_string() == build_id)
        )
        assert job_id is not None
        db.expunge_all()
        job = DurableQueue(db).claim_next(kind="learning.package.build")
        assert job is not None
        assert str(job.payload.get("build_id")) == build_id, "抢到了别的测试的任务"
        return job


def test_half_written_setting_is_rejected(client, learning_node):
    """provider_id 与 model_id 必须成对；半写会让「跟随对话模型」的语义变得模糊。"""
    saved = client.put(
        f"/api/v1/settings/{LEARNING_PACKAGE_MODEL_SETTING_KEY}",
        json={"value": {"provider_id": "prov-1", "model_id": None}},
        headers=learning_node["headers"],
    )
    assert saved.status_code == 422, saved.text


def test_learning_generation_uses_the_designated_model(client, learning_node, monkeypatch):
    """接线级：设置里指定的模型必须真的走到 provider 解析处（含阶段内并发两路）。"""
    import app.providers.factory as provider_factory
    from app.services import learning_packages as lp

    captured: list[dict] = []

    def factory(db, workspace_id, settings, **kwargs):
        captured.append(dict(kwargs))
        return StubProvider()

    monkeypatch.setattr(provider_factory, "model_provider_for_workspace", factory)

    build = _enqueue(client, learning_node["headers"], learning_node["node_id"])
    job = _lease_build(build["id"])

    # 阶段 0（教学设计）——未配置时必须回落，不得凭空虚造一对 provider/model。
    terminal = lp.run_build_stage(job.id, job.lease_token, build["id"])
    with SessionLocal() as db:
        record = db.get(LearningBuild, build["id"])
    assert terminal is False, f"阶段 0 未成功：status={record.status} error={record.error}"
    assert captured == [{}], "没有配置时不得凭空虚造 provider_id/model_id"

    saved = client.put(
        f"/api/v1/settings/{LEARNING_PACKAGE_MODEL_SETTING_KEY}",
        json={"value": {"provider_id": "prov-9", "model_id": "model-9"}},
        headers=learning_node["headers"],
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["value"] == {"provider_id": "prov-9", "model_id": "model-9"}

    # 阶段 1（图文教材 ‖ 实验规则）——两路并发都必须带上指定模型。
    captured.clear()
    assert lp.run_build_stage(job.id, job.lease_token, build["id"]) is False
    assert len(captured) == 2, f"教材与实验规则应各解析一次模型：{captured}"
    assert all(
        item == {"provider_id": "prov-9", "model_id": "model-9"} for item in captured
    ), captured

    with SessionLocal() as db:
        record = db.get(LearningBuild, build["id"])
        assert record.stage == 2
        assert "lesson" in record.checkpoints and "activity" in record.checkpoints
