"""端到端验证：极速模式流式图谱 = 先主干，再逐主干节点两层分层展开。

用 FakeModelProvider 返回真实结构化分块，走真实 HTTP SSE 端点：
断言事件顺序 graph.root → nodes_added(主干) → 每主干节点 nodes_added(layer1) +
nodes_added(layer2) → graph.complete，并核对落库结构的最大深度为 3 层且无孤儿。
"""
from __future__ import annotations

import os

os.environ.setdefault("LEARNGRAPH_SECRET_PROVIDER", "environment")
os.environ.setdefault("LEARNGRAPH_MASTER_KEY", "api-tests-master-key-v1")
os.environ.setdefault("LEARNGRAPH_DURABLE_QUEUE_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_MASTERY_EMBEDDED_SCHEDULER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_MEMORY_RETENTION_SCHEDULER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_MEMORY_EXTRACTION_SCHEDULER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_SANDBOX_CLEANUP_SCHEDULER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_MCP_STDIO_CLEANUP_SCHEDULER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_MEMORY_OUTBOX_WORKER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_SANDBOX_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_AUTH_RATE_LIMIT_MAX", "100000")
os.environ.setdefault("LEARNGRAPH_AUTH_RATE_LIMIT_WINDOW_SECONDS", "3600")

import json
import time
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

ROOT_TITLE = "数据库原理与应用"


class FakeModelProvider:
    """返回合法结构化分块的模型桩，用于验证主干→两层分层的流式编排。"""

    available = True
    provider_id = "fake-provider"
    model_id = "fake-model"
    remote_capability = True
    thinking_mode = "off"
    actual_reasoning_effort = None
    search_route = "disabled"

    def __init__(self) -> None:
        self.chunk_calls = 0  # 第 0 次 chunk 调用 = 主干，其后 = 分支展开

    def generate_json(self, prompt, schema_name, schema):
        if schema_name == "learngraph_graph_root":
            return {
                "title": ROOT_TITLE,
                "root": {
                    "label": ROOT_TITLE,
                    "description": "数据库系统原理与应用实践的完整学科",
                    "node_type": "root",
                    "target_weight": 50,
                    "teaching_strategy": "以数据库系统概览切入，先定义数据库边界，再给出典型例子与掌握标准。",
                },
            }
        if schema_name == "learngraph_graph_chunk":
            if self.chunk_calls == 0:
                self.chunk_calls += 1
                # 主干：3 个 layer=1 主干模块 + 主干间 prerequisite 顺序
                return {
                    "nodes": [
                        {"label": "关系模型", "description": "关系代数与关系完整性", "node_type": "concept", "target_weight": 60, "teaching_strategy": "", "layer": 1},
                        {"label": "SQL 语言", "description": "DDL/DML 与查询优化基础", "node_type": "concept", "target_weight": 55, "teaching_strategy": "", "layer": 1},
                        {"label": "事务与并发控制", "description": "ACID 与隔离级别", "node_type": "concept", "target_weight": 50, "teaching_strategy": "", "layer": 1},
                    ],
                    "edges": [
                        {"source_index": 0, "target_index": 1, "relation": "prerequisite"},
                        {"source_index": 1, "target_index": 2, "relation": "prerequisite"},
                    ],
                }
            branch = self.chunk_calls  # 1..3
            self.chunk_calls += 1
            # 每个主干节点：2 个 layer=1 直接子节点 + 2 个 layer=2 孙节点
            return {
                "nodes": [
                    {"label": f"子概念{branch}-a", "description": "直接子节点 a", "node_type": "concept", "target_weight": 50, "teaching_strategy": "", "layer": 1},
                    {"label": f"子概念{branch}-b", "description": "直接子节点 b", "node_type": "concept", "target_weight": 45, "teaching_strategy": "", "layer": 1},
                    {"label": f"孙概念{branch}-a1", "description": "孙节点 a1", "node_type": "concept", "target_weight": 40, "teaching_strategy": "", "layer": 2},
                    {"label": f"孙概念{branch}-b1", "description": "孙节点 b1", "node_type": "concept", "target_weight": 40, "teaching_strategy": "", "layer": 2},
                ],
                "edges": [
                    {"source_index": 0, "target_index": 2, "relation": "contains"},
                    {"source_index": 1, "target_index": 3, "relation": "contains"},
                ],
            }
        raise AssertionError(f"unexpected schema {schema_name}")


def main() -> int:
    from app.main import app
    from app.api.routers import goals as goals_router
    from app.services.goals import GoalService
    from app.core.database import SessionLocal

    failures: list[str] = []

    def fake_service_with_model(db, context, settings, **kwargs):
        return GoalService(
            db,
            context.workspace_id,
            context.principal.user_id,
            FakeModelProvider(),
        )

    goals_router.service_with_model = fake_service_with_model
    goals_router.service = fake_service_with_model

    with TestClient(app) as client:
        username = f"verify_{int(time.time() * 1000)}"
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": username, "display_name": "verify", "password": "ApiTest@Pass2026!x"},
        )
        assert resp.status_code == 201, resp.text
        token = resp.json()["access_token"]
        ws = client.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {token}"}).json()[0]["id"]
        headers = {"Authorization": f"Bearer {token}", "X-Workspace-ID": ws}

        gid = str(uuid.uuid4())
        with SessionLocal() as db:
            db.execute(
                text(
                    """INSERT INTO goals (id, workspace_id, title, raw_prompt, status, intent,
                       time_limit, target_weight, availability, preferences, desired_outcome,
                       constraints, assumptions, created_at, updated_at)
                       VALUES (:id, :ws, '数据库原理与应用', 'p', 'active', 'learn', '', 50,
                       '{"minutes_per_day": 60, "days_per_week": 3}', '{}', '掌握',
                       '{}', '[]', :now, :now)"""
                ),
                {"id": gid, "ws": ws, "now": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()},
            )
            db.commit()
        r = client.put(
            f"/api/v1/goals/{gid}/confirm",
            headers=headers,
            json={
                "title": "数据库原理与应用",
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
            body = response.read().decode("utf-8")

        events: list[tuple[str, dict]] = []
        for block in body.split("\n\n"):
            lines = [line for line in block.splitlines() if line and not line.startswith(":")]
            if not lines:
                continue
            name, data_lines = "message", []
            for line in lines:
                if line.startswith("event:"):
                    name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:") :].strip())
            if data_lines:
                events.append((name, json.loads("\n".join(data_lines))))

        names = [name for name, _ in events]
        print("事件顺序:", " → ".join(names))

        assert "graph.error" not in names, f"出现错误事件：{body[:800]}"
        assert names[0] == "graph.root", names
        assert names[-1] == "graph.complete", names
        # 主干一批 + 每主干节点两批（layer1 / layer2）
        added = [n for n in names if n == "graph.nodes_added"]
        assert added[0] == "graph.nodes_added", names
        # 3 个主干节点 → 6 个分支事件
        assert len(added) == 1 + 3 * 2, f"期望 7 个 nodes_added（1 主干 + 6 分支分层），实际 {len(added)}：{names}"

        trunk_payload = events[names.index("graph.nodes_added")][1]
        assert len(trunk_payload["nodes"]) == 3, trunk_payload
        assert all(n["node_type"] == "concept" for n in trunk_payload["nodes"])
        assert len(trunk_payload["edges"]) == 5, trunk_payload["edges"]  # 3 contains + 2 prerequisite

        # 分支事件成对：(layer1, layer2)，层内节点数分别为 2 / 2
        pair_idx = [i for i, n in enumerate(names) if n == "graph.nodes_added"][1:]
        assert len(pair_idx) == 6
        for i in range(0, 6, 2):
            l1 = events[pair_idx[i]][1]
            l2 = events[pair_idx[i + 1]][1]
            assert len(l1["nodes"]) == 2, l1["nodes"]
            assert len(l2["nodes"]) == 2, l2["nodes"]
            # layer1 事件的边只引用已发送节点（主干/本层），layer2 事件含 layer1→layer2 边
            sent_ids = {n["id"] for n in trunk_payload["nodes"]} | {n["id"] for n in l1["nodes"]}
            assert all(e["source_node_id"] in sent_ids and e["target_node_id"] in sent_ids for e in l1["edges"]), l1["edges"]
            assert any(
                e["target_node_id"] in {n["id"] for n in l2["nodes"]} for e in l2["edges"]
            ), l2["edges"]

        complete = events[names.index("graph.complete")][1]
        total_nodes = 1 + 3 + 3 * 4
        assert len(complete["nodes"]) == total_nodes, f"期望 {total_nodes} 节点，实际 {len(complete['nodes'])}"
        print(f"complete 快照：{len(complete['nodes'])} 节点 / {len(complete['edges'])} 边")

        # ---- 落库结构校验：最大深度 3（root=0 → 主干=1 → 子=2 → 孙=3），无孤儿 ----
        graph_id = complete["graph_id"]
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT id, label FROM graph_nodes WHERE graph_id = :g"
                ),
                {"g": graph_id},
            ).fetchall()
            edge_rows = db.execute(
                text(
                    "SELECT source_node_id, target_node_id FROM graph_edges WHERE graph_id = :g"
                ),
                {"g": graph_id},
            ).fetchall()
        node_ids = {r[0] for r in rows}
        assert len(node_ids) == total_nodes, f"落库节点数 {len(node_ids)} != {total_nodes}"
        children: dict[str, list[str]] = {}
        parents: dict[str, int] = {}
        for source, target in edge_rows:
            children.setdefault(source, []).append(target)
            parents[target] = parents.get(target, 0) + 1
        root_id = next(r[0] for r in rows if r[1] == ROOT_TITLE)
        depth: dict[str, int] = {root_id: 0}
        stack = [root_id]
        while stack:
            current = stack.pop()
            for child in children.get(current, []):
                if child not in depth:
                    depth[child] = depth[current] + 1
                    stack.append(child)
        max_depth = max(depth.values())
        print(f"落库最大深度：{max_depth}（期望 3：root→主干→子→孙）")
        assert max_depth == 3, f"最大深度 {max_depth} != 3"
        missing = node_ids - set(depth)
        assert not missing, f"存在无法从根到达的孤儿节点：{missing}"

    if failures:
        print("\nFAILURES:", *failures, sep="\n - ")
        return 1
    print("\nOK: 极速模式流式策略 = 先主干 → 逐主干节点两层分层展开 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
