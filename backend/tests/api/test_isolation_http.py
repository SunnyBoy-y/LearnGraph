"""隔离与越权补测（S1-1/S1-2/S 系列回归）：
- 用户 B 无法使用用户 A 的工作区（跨租户/跨用户隔离）
- 用户 A 的数据对用户 B 不可见
- 无权限访问他人资源返回 403/404 而非 200
"""
from __future__ import annotations

import uuid


def test_cross_user_workspace_isolation(client, register_user, auth_headers):
    """B 用 A 的 workspace id → 必须 403/404，不得看到 A 的数据。"""
    token_a, ws_a, _, _ = register_user("userA")
    token_b, _, _, _ = register_user("userB")
    r = client.get(
        "/api/v1/goals", headers=auth_headers(token_b, ws_a)
    )
    assert r.status_code in (403, 404), (
        f"B 使用 A 的 workspace 应 403/404，实际 {r.status_code}"
    )


def test_cross_user_resource_404(client, register_user, auth_headers):
    """B 直接访问 A 的 goal id → 404（资源不可见）。"""
    from sqlalchemy import text

    token_a, ws_a, _, _ = register_user("userA")
    token_b, _, _, _ = register_user("userB")
    # A 建一个 goal
    r = client.post(
        "/api/v1/goals/clarify",
        headers=auth_headers(token_a, ws_a),
        json={"prompt": "A 的私有目标"},
    )
    # clarify 需要模型——用 SQL 直插合法 goal 更稳
    from app.core.database import SessionLocal
    from app.domain.models import Goal

    now = __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()
    gid = str(uuid.uuid4())
    with SessionLocal() as db:
        db.add(Goal(
            id=gid, workspace_id=ws_a, title="A私有", raw_prompt="p",
            status="active", intent="learn", time_limit="", target_weight=1.0,
            availability="anytime", preferences={}, desired_outcome="",
            constraints={}, assumptions=[],
        ))
        db.commit()
    # B 访问 A 的 goal
    r = client.get(f"/api/v1/goals/{gid}", headers=auth_headers(token_b, ws_a))
    assert r.status_code in (403, 404), f"越权访问应 403/404，实际 {r.status_code}"


def test_research_created_by_isolation(client, register_user, auth_headers):
    """S1-2 回归锚点：研究任务创建后，另一用户（同工作区场景外）不得读取。
    当前实现若返回 200 即暴露越权 —— 用 xfail(strict) 记录缺口。"""
    import pytest

    token_a, ws_a, _, _ = register_user("userA")
    token_b, _, _, _ = register_user("userB")
    # 研究创建需要 search/deep-research provider；不可达时可能 503。
    # 本测试聚焦：B 对 A 工作区的研究列表访问被拒绝（隔离层面）。
    r = client.get("/api/v1/research", headers=auth_headers(token_b, ws_a))
    assert r.status_code in (403, 404), (
        f"S1-2 相关：B 读取 A 工作区研究列表应被拒，实际 {r.status_code}（若 200 为越权）"
    )


def test_egress_approvals_isolation(client, register_user, auth_headers):
    """B 访问 A 工作区的出网审批列表 → 403/404。"""
    token_a, ws_a, _, _ = register_user("userA")
    token_b, _, _, _ = register_user("userB")
    r = client.get("/api/v1/egress-approvals", headers=auth_headers(token_b, ws_a))
    assert r.status_code in (403, 404), r.status_code


def test_no_workspace_permission_on_other_tenant_data(client, register_user, auth_headers):
    """B 完全不带 workspace 访问 A 的资源 → 422（缺头）或 403。"""
    token_a, ws_a, _, _ = register_user("userA")
    token_b, _, _, _ = register_user("userB")
    r = client.get(
        "/api/v1/memory", headers=auth_headers(token_b)
    )
    assert r.status_code in (403, 422), r.status_code
