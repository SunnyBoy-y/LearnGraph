"""goals / graphs 接口补测：目标确认/规划、图谱节点 PATCH/合并查询（模型依赖的成功路径标注缺测）。"""
from __future__ import annotations

import uuid

from sqlalchemy import text


def _insert_goal_via_sql(ws: str) -> str:
    """在测试库直接插入合法 goal（绕开需要模型的 clarify 成功路径）。"""
    from app.core.database import SessionLocal
    from app.domain.models import Goal

    now = __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()
    with SessionLocal() as db:
        goal = Goal(
            id=str(uuid.uuid4()),
            workspace_id=ws,
            title="补测目标",
            raw_prompt="补测目标 prompt",
            status="active",
            intent="learn",
            time_limit="30天",
            target_weight=1.0,
            availability={"minutes_per_day": 60, "days_per_week": 3},
            preferences={},
            desired_outcome="掌握",
            constraints={},
            assumptions=[],
        )
        db.add(goal)
        db.commit()
        return goal.id


def _insert_graph_via_sql(ws: str, goal_id: str) -> tuple[str, str]:
    from app.core.database import SessionLocal
    from app.domain.models import Graph, GraphNode

    now = __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()
    graph_id = str(uuid.uuid4())
    node_id = str(uuid.uuid4())
    with SessionLocal() as db:
        graph = Graph(
            id=graph_id, goal_id=goal_id, title="补测图谱", status="active",
            revision=1, workspace_id=ws,
        )
        db.add(graph)
        db.flush()
        node = GraphNode(
            id=node_id, graph_id=graph_id, workspace_id=ws, label="根节点",
            node_type="knowledge", target_weight=1.0, mastery_stars=0,
            retrieval_state="fresh", evidence_state="robust", attention_state="focused",
            teaching_strategy="",
        )
        db.add(node)
        db.commit()
    return graph_id, node_id


def test_goals_list_empty(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    r = client.get("/api/v1/goals", headers=auth_headers(token, ws))
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_goal_confirm_and_planning(client, register_user, auth_headers):
    """目标确认 + 规划 PATCH（不依赖模型）。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    gid = _insert_goal_via_sql(ws)
    r = client.put(
        f"/api/v1/goals/{gid}/confirm",
        headers=headers,
        json={
            "title": "补测目标",
            "intent": "learn",
            "time_limit": "30天",
            "target_weight": 50,
            "availability": {"minutes_per_day": 60, "days_per_week": 3},
            "preferences": {},
            "desired_outcome": "掌握",
            "constraints": {},
            "assumptions": [],
        },
    )
    assert r.status_code == 200, r.text
    r = client.patch(
        f"/api/v1/goals/{gid}/planning",
        headers=headers,
        json={"target_weight": 60, "availability": {"minutes_per_day": 45}},
    )
    assert r.status_code == 200, r.text


def test_goal_confirm_with_corrupt_availability_returns_4xx_not_500(client, register_user, auth_headers):
    """T1-3 回归锚点：availability 为字符串（脏数据）时 confirm 应 4xx，不得 500。
    当前实现 ValueError → 500（已复现）；xfail(strict) 记录缺口，修复后转 XPASS。"""
    import pytest

    from sqlalchemy import text

    from app.core.database import SessionLocal

    token, ws, _, _ = register_user()
    # 直接构造 availability=字符串 的脏 goal
    gid = str(uuid.uuid4())
    now = __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()
    with SessionLocal() as db:
        db.execute(
            text(
                """INSERT INTO goals (id, workspace_id, title, raw_prompt, status, intent,
                   time_limit, target_weight, availability, preferences, desired_outcome,
                   constraints, assumptions, created_at, updated_at)
                   VALUES (:id, :ws, '脏数据目标', 'p', 'active', 'learn', '', 1.0,
                   'anytime', '{}', '', '{}', '[]', :now, :now)"""
            ),
            {"id": gid, "ws": ws, "now": now},
        )
        db.commit()
    r = client.put(
        f"/api/v1/goals/{gid}/confirm",
        headers=auth_headers(token, ws),
        json={
            "title": "脏数据目标",
            "intent": "learn",
            "target_weight": 50,
            "availability": {"minutes_per_day": 60},
            "preferences": {},
            "desired_outcome": "",
            "constraints": {},
            "assumptions": [],
        },
    )
    # T1-3 修复固化：脏 availability 不再导致 500（LenientJSON 读行容错 +
    # goals.py 类型守卫），confirm 正常返回。
    assert r.status_code == 200, f"脏 availability confirm 应 200，实际 {r.status_code}: {r.text[:200]}"


def test_goal_delete_impact(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    gid = _insert_goal_via_sql(ws)
    r = client.get(
        f"/api/v1/goals/{gid}/delete-impact", headers=auth_headers(token, ws)
    )
    assert r.status_code == 200, r.text


def test_graph_list_and_get(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    gid = _insert_goal_via_sql(ws)
    graph_id, node_id = _insert_graph_via_sql(ws, gid)
    r = client.get("/api/v1/graphs", headers=headers)
    assert r.status_code == 200
    assert any(g["id"] == graph_id for g in r.json())
    r = client.get(f"/api/v1/graphs/{graph_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["title"] == "补测图谱"


def test_graph_node_patch(client, register_user, auth_headers):
    """图谱节点 PATCH（修订式保存回归，D4 正面）。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    gid = _insert_goal_via_sql(ws)
    graph_id, node_id = _insert_graph_via_sql(ws, gid)
    r = client.patch(
        f"/api/v1/graphs/{graph_id}/nodes/{node_id}",
        headers=headers,
        json={"label": "更新后标签", "expected_revision": 1},
    )
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "更新后标签"


def test_graph_node_questions_not_found(client, register_user, auth_headers):
    """节点问题查询：合法空数据应 200（含空列表），而非 500。"""
    token, ws, _, _ = register_user()
    gid = _insert_goal_via_sql(ws)
    graph_id, node_id = _insert_graph_via_sql(ws, gid)
    r = client.get(
        f"/api/v1/graphs/{graph_id}/nodes/{node_id}/questions",
        headers=auth_headers(token, ws),
    )
    assert r.status_code == 200, r.text


def test_goal_graph_candidate_stream_root_first(client, register_user, auth_headers, stub_goal_service):
    """流式候选图谱：根节点先于 complete 事件到达（极速模式，不依赖真实模型）。

    校验 SSE 顺序：graph.root → graph.complete；root 事件带 graph_id 与根节点。
    """
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    gid = _insert_goal_via_sql(ws)
    # 流式生成要求 Goal 已确认
    r = client.put(
        f"/api/v1/goals/{gid}/confirm",
        headers=headers,
        json={
            "title": "补测目标",
            "intent": "learn",
            "time_limit": "30天",
            "target_weight": 50,
            "availability": {"minutes_per_day": 60, "days_per_week": 3},
            "preferences": {},
            "desired_outcome": "掌握",
            "constraints": {},
            "assumptions": [],
        },
    )
    assert r.status_code == 200, r.text

    with client.stream(
        "POST",
        f"/api/v1/goals/{gid}/candidate-graph/stream",
        headers={**headers, "Accept": "text/event-stream"},
        json={"mode": "fast"},
    ) as response:
        assert response.status_code == 200, response.text
        assert "text/event-stream" in response.headers.get("content-type", "")
        body = response.read().decode("utf-8")

    events: list[tuple[str, str]] = []
    for block in body.split("\n\n"):
        lines = [line for line in block.splitlines() if line and not line.startswith(":")]
        if not lines:
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in lines:
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if not data_lines:
            continue
        events.append((event_name, "\n".join(data_lines)))

    names = [name for name, _ in events]
    assert "graph.root" in names, f"缺少根预览事件：{names}"
    assert "graph.complete" in names, f"缺少 complete 事件：{names}"
    root_index = names.index("graph.root")
    complete_index = names.index("graph.complete")
    assert root_index < complete_index, "根预览必须先于 complete 到达"

    import json as _json

    root_payload = _json.loads(events[root_index][1])
    assert root_payload.get("graph_id"), root_payload
    assert root_payload.get("root") and root_payload["root"].get("label"), root_payload

    complete_payload = _json.loads(events[complete_index][1])
    assert complete_payload.get("graph_id") == root_payload.get("graph_id")
    assert complete_payload.get("nodes"), complete_payload
    assert complete_payload["status"] == "candidate", complete_payload


def test_goal_graph_candidate_stream_requires_confirmed(client, register_user, auth_headers):
    """流式候选图谱：未确认 Goal 应 409，不产生图谱。"""
    token, ws, _, _ = register_user()
    gid = _insert_goal_via_sql(ws)
    # _insert_goal_via_sql 默认 status="active"，不属于 confirmed/candidate_ready
    with client.stream(
        "POST",
        f"/api/v1/goals/{gid}/candidate-graph/stream",
        headers=auth_headers(token, ws),
        json={"mode": "fast"},
    ) as response:
        body = response.read().decode("utf-8")
    assert "graph.error" in body, f"预期错误事件，实际：{body[:300]}"


def test_goals_list_tolerates_legacy_weekly_hours(client, register_user, auth_headers):
    """回归锚点：availability 含旧版字段 weekly_hours=null 时 /goals 不得 500。

    旧 schema 曾写入 availability.weekly_hours；严格 GoalAvailability 校验会
    extra_forbidden → GET /api/v1/goals 全表 500。读取路径必须在验证前剔除
    未知键与 null 值。
    """
    from app.core.database import SessionLocal

    token, ws, _, _ = register_user()
    gid = str(uuid.uuid4())
    now = __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()
    with SessionLocal() as db:
        db.execute(
            text(
                """INSERT INTO goals (id, workspace_id, title, raw_prompt, status, intent,
                   time_limit, target_weight, availability, preferences, desired_outcome,
                   constraints, assumptions, created_at, updated_at)
                   VALUES (:id, :ws, '遗留字段目标', 'p', 'active', 'learn', '', 50,
                   :availability, :preferences, '', '{}', '[]', :now, :now)"""
            ),
            {
                "id": gid,
                "ws": ws,
                "availability": '{"minutes_per_day": 60, "weekly_hours": null}',
                "preferences": '{"preferred_action_types": null, "session_minutes": 30}',
                "now": now,
            },
        )
        db.commit()
    r = client.get("/api/v1/goals", headers=auth_headers(token, ws))
    assert r.status_code == 200, r.text
    items = r.json()
    assert any(item["id"] == gid for item in items), items
    legacy = next(item for item in items if item["id"] == gid)
    assert legacy["availability"] == {"minutes_per_day": 60}, legacy["availability"]
    assert legacy["preferences"] == {"session_minutes": 30}, legacy["preferences"]


def test_goal_confirm_sanitizes_legacy_availability(client, register_user, auth_headers):
    """写入路径：confirm 合并旧 availability 后不得再把 weekly_hours 写回。"""
    from app.core.database import SessionLocal
    from app.domain.models import Goal

    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    gid = _insert_goal_via_sql(ws)
    # 先塞入旧版字段
    with SessionLocal() as db:
        goal = db.get(Goal, gid)
        goal.availability = {"minutes_per_day": 45, "weekly_hours": None}
        db.commit()
    r = client.put(
        f"/api/v1/goals/{gid}/confirm",
        headers=headers,
        json={
            "title": "补测目标",
            "intent": "learn",
            "time_limit": "30天",
            "target_weight": 50,
            "availability": {"minutes_per_day": 60, "days_per_week": 3},
            "preferences": {},
            "desired_outcome": "掌握",
            "constraints": {},
            "assumptions": [],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["availability"] == {"minutes_per_day": 60, "days_per_week": 3}, r.text


def test_graph_merges_list(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    r = client.get("/api/v1/graphs/merges", headers=auth_headers(token, ws))
    assert r.status_code == 200


class _BranchConcurrencyTracker:
    """跨并发 worker 共享的计数状态（provider 实例各自独立但共享 tracker）。"""

    def __init__(self) -> None:
        import threading

        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.branch_calls = 0


class _ConcurrentBranchProvider:
    """在每次分支展开调用内休眠的桩 provider：4 个分支并发时必然重叠。

    ``generate_json`` 按 schema_name/提示词区分根节点、主干与分支；分支调用
    进入并发计数窗口并 sleep 0.35s，用于断言并发生成真实发生。usage 字段
    与真实 provider 对齐，供记账路径走通。
    """

    available = True
    provider_id = "concurrent-stub"
    model_id = "concurrent-stub-model"
    remote_capability = True
    thinking_mode = "off"
    actual_reasoning_effort = None
    search_route = "disabled"
    context_window_tokens = 8192
    max_output_tokens = 2048
    last_usage: dict = {}
    last_request_id = None

    def __init__(self, tracker: _BranchConcurrencyTracker) -> None:
        self.tracker = tracker

    @staticmethod
    def _node(label: str, node_type: str = "concept", layer: int = 1) -> dict:
        return {
            "label": label,
            "description": f"「{label}」的说明",
            "node_type": node_type,
            "target_weight": 50,
            "teaching_strategy": f"以百科词条方式讲解「{label}」",
            "layer": layer,
        }

    def generate_json(self, prompt: str, schema_name: str, schema: dict) -> dict:
        import re
        import time

        if schema_name == "learngraph_graph_root":
            return {"title": "测试学科", "root": self._node("测试学科", "root", 1)}
        if "主干" in prompt and "两层分层" not in prompt:
            return {
                "nodes": [self._node(f"主干模块{i}", "concept", 1) for i in range(1, 5)],
                "edges": [],
            }
        # 分支展开：进入并发计数窗口并休眠，保证多个 worker 真正重叠。
        match = re.search(r"主干节点：(\S+?)\s+—", prompt)
        branch = match.group(1) if match else "分支"
        with self.tracker.lock:
            self.tracker.branch_calls += 1
            self.tracker.active += 1
            self.tracker.max_active = max(self.tracker.max_active, self.tracker.active)
        try:
            time.sleep(0.35)
            return {
                "nodes": [
                    self._node(f"{branch}·L1甲", "concept", 1),
                    self._node(f"{branch}·L1乙", "practice", 1),
                    self._node(f"{branch}·L2孙", "concept", 2),
                ],
                "edges": [{"source_index": 0, "target_index": 2, "relation": "contains"}],
            }
        finally:
            with self.tracker.lock:
                self.tracker.active -= 1


def test_goal_graph_candidate_stream_parallel_branches(client, register_user, auth_headers, monkeypatch):
    """流式候选图谱：分支展开并发生成（思考模式，4 个主干）。

    校验三点：① 分支 model 调用真正并发（峰值并发 >= 2）；② SSE 事件顺序仍
    为 root → trunk → 分支层批 → complete；③ complete 快照包含主干与分支节点。
    """
    import json as _json

    from app.api.routers import goals as goals_router
    from app.services.goals import GoalService

    tracker = _BranchConcurrencyTracker()

    def factory(db, context, settings, **kwargs):
        return GoalService(
            db,
            context.workspace_id,
            context.principal.user_id,
            _ConcurrentBranchProvider(tracker),
            provider_factory=lambda: _ConcurrentBranchProvider(tracker),
        )

    monkeypatch.setattr(goals_router, "service_with_model", factory)

    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    gid = _insert_goal_via_sql(ws)
    r = client.put(
        f"/api/v1/goals/{gid}/confirm",
        headers=headers,
        json={
            "title": "补测目标",
            "intent": "learn",
            "time_limit": "30天",
            "target_weight": 50,
            "availability": {"minutes_per_day": 60, "days_per_week": 3},
            "preferences": {},
            "desired_outcome": "掌握",
            "constraints": {},
            "assumptions": [],
        },
    )
    assert r.status_code == 200, r.text

    with client.stream(
        "POST",
        f"/api/v1/goals/{gid}/candidate-graph/stream",
        headers={**headers, "Accept": "text/event-stream"},
        json={"mode": "thinking"},
    ) as response:
        assert response.status_code == 200, response.text
        body = response.read().decode("utf-8")

    events: list[tuple[str, str]] = []
    for block in body.split("\n\n"):
        lines = [line for line in block.splitlines() if line and not line.startswith(":")]
        if not lines:
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in lines:
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if not data_lines:
            continue
        events.append((event_name, "\n".join(data_lines)))

    names = [name for name, _ in events]
    assert names[0] == "graph.root", names
    assert "graph.complete" in names, names
    root_i = names.index("graph.root")
    trunk_i = names.index("graph.nodes_added")
    complete_i = names.index("graph.complete")
    assert root_i < trunk_i < complete_i, names
    batch_count = sum(1 for name in names if name == "graph.nodes_added")
    # 主干 1 批 + 4 分支 × 每分支 2 层（任一分支缺层也不应少于 5 批）
    assert batch_count >= 5, names

    assert tracker.branch_calls == 4, f"分支调用数异常：{tracker.branch_calls}"
    assert tracker.max_active >= 2, f"分支未并发生成：峰值并发 {tracker.max_active}"

    complete_payload = _json.loads(events[complete_i][1])
    labels = [node["label"] for node in complete_payload["nodes"]]
    assert "主干模块1" in labels, labels
    assert any(label.endswith("·L1甲") for label in labels), labels
    assert any(label.endswith("·L2孙") for label in labels), labels
