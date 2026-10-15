"""组织内成员越权测试（S1-1/S1-2 修复固化）。

场景：A 创建组织工作区（Admin），B 以 Member 身份加入同一组织工作区。
- A 创建 FetchAuthorizationRequest / ResearchJob（付费/敏感资源）
- B 尝试 decision / resume / research 读取与审批 → 期望 403（修复已生效）
- A（Admin/owner）同操作 → 200（对照）

工作树已含 S1-1/S1-2 修复（actor/manage 校验 + owner 过滤），本套件固化这些修复。
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.core.database import SessionLocal


def _create_org_and_member(client, register_user, auth_headers):
    """建组织（A 为 Admin）+ B 以 Member 加入。返回 (ws, token_a, token_b, org_id)。

    注册会为每个用户创建独立 tenant，而组织成员必须同 tenant；
    因此把 B 的 user.tenant_id 对齐 A 后让 B 重新登录取新 token。
    """
    token_a, _, _, _ = register_user("orgA")
    token_b, _, _, _ = register_user("orgB")
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    me_b = client.get("/api/v1/auth/me", headers=auth_headers(token_b)).json()
    tenant_a = me_a["tenant_id"]
    # B 对齐 tenant 并重新登录
    from app.core.database import SessionLocal
    from sqlalchemy import text as _text

    with SessionLocal() as db:
        db.execute(
            _text("UPDATE users SET tenant_id=:t WHERE id=:uid"),
            {"t": tenant_a, "uid": me_b["id"]},
        )
        db.commit()
    login_b = client.post(
        "/api/v1/auth/login",
        json={"username": me_b["username"], "password": "ApiTest@Pass2026!x"},
    )
    assert login_b.status_code == 200, login_b.text
    token_b = login_b.json()["access_token"]
    r = client.post(
        "/api/v1/organizations",
        headers=auth_headers(token_a),
        json={"name": f"审计组织 {uuid.uuid4().hex[:6]}", "workspace_name": "成员共享空间"},
    )
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]
    # 组织工作区 id 从库中取
    with SessionLocal() as db:
        ws = db.execute(
            _text("SELECT id FROM workspaces WHERE organization_id=:oid"),
            {"oid": org_id},
        ).scalar_one()
    # Member 角色
    roles = client.get(
        f"/api/v1/organizations/{org_id}/roles", headers=auth_headers(token_a)
    ).json()
    member_role = next((x["id"] for x in roles if x["name"] == "Member"), None)
    assert member_role, roles
    r = client.post(
        f"/api/v1/organizations/{org_id}/memberships",
        headers=auth_headers(token_a),
        json={"user_id": me_b["id"], "role_id": member_role},
    )
    assert r.status_code == 201, r.text
    return ws, token_a, token_b, org_id


def _insert_fetch_auth(client, headers, ws: str, actor_id: str, status: str = "pending") -> str:
    # chat_session_id 有外键约束，先通过 API 建真实会话
    r = client.post(
        "/api/v1/sessions", headers=headers, json={"title": "审批测试会话"}
    )
    assert r.status_code in (200, 201), r.text
    session_id = r.json()["id"]
    rid = str(uuid.uuid4())
    with SessionLocal() as db:
        db.execute(
            text(
                """INSERT INTO fetch_authorization_requests
                   (id, workspace_id, chat_session_id, actor_id, tool_call_id, tool_name,
                    requested_url, hostname, status, resume_payload, created_at, updated_at)
                   VALUES (:id, :ws, :sid, :actor, 'tc-1', 'fetch_web_page',
                    'https://example.com', 'example.com', :status,
                    :payload, :now, :now)"""
            ),
            {
                "id": rid,
                "ws": ws,
                "sid": session_id,
                "actor": actor_id,
                "status": status,
                "payload": json.dumps({"request": {"url": "https://example.com"}})
                if status == "approved"
                else None,
                "now": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
            },
        )
        db.commit()
    return rid


def _insert_research(ws: str, actor_id: str) -> str:
    rid = str(uuid.uuid4())
    with SessionLocal() as db:
        db.execute(
            text(
                """INSERT INTO research_jobs
                   (id, workspace_id, created_by, question, status, provider_id, budget_cny,
                    estimated_cost_cny, actual_cost_cny, approval_status, source_scope,
                    allowed_domains, billing_snapshot, evidence_pack, created_at, updated_at)
                   VALUES (:id, :ws, :actor, 'A 的私有研究问题（敏感）', 'awaiting_approval',
                    'local_mock', 10.0, 0.0, 0.0, 'awaiting_approval', '[]', '[]', '{}', '{}', :now, :now)"""
            ),
            {
                "id": rid,
                "ws": ws,
                "actor": actor_id,
                "now": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
            },
        )
        db.commit()
    return rid


def test_member_cannot_decide_others_fetch_auth(client, register_user, auth_headers):
    """S1-1 固化：B（Member）对 A 的 fetch 审批做 decision → 403。"""
    ws, token_a, token_b, _ = _create_org_and_member(client, register_user, auth_headers)
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    rid = _insert_fetch_auth(client, auth_headers(token_a, ws), ws, me_a["id"], "pending")
    r = client.post(
        f"/api/v1/fetch-authorizations/{rid}/decision",
        headers=auth_headers(token_b, ws),
        json={"decision": "allow_once"},
    )
    assert r.status_code == 403, f"Member 代决定他人 fetch 审批应 403，实际 {r.status_code}"


def test_owner_can_decide_own_fetch_auth(client, register_user, auth_headers):
    """对照：A（owner）决定自己的 fetch 审批 → 200。"""
    ws, token_a, _, _ = _create_org_and_member(client, register_user, auth_headers)
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    rid = _insert_fetch_auth(client, auth_headers(token_a, ws), ws, me_a["id"], "pending")
    r = client.post(
        f"/api/v1/fetch-authorizations/{rid}/decision",
        headers=auth_headers(token_a, ws),
        json={"decision": "allow_once"},
    )
    assert r.status_code == 200, r.text


def test_member_cannot_resume_others_fetch_auth(client, register_user, auth_headers):
    """S1-1 固化：B 对 A 的 approved+resume_payload 请求做 resume → 403。"""
    ws, token_a, token_b, _ = _create_org_and_member(client, register_user, auth_headers)
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    rid = _insert_fetch_auth(client, auth_headers(token_a, ws), ws, me_a["id"], "approved")
    r = client.post(
        f"/api/v1/fetch-authorizations/{rid}/resume",
        headers=auth_headers(token_b, ws),
    )
    assert r.status_code == 403, f"Member 恢复他人付费抓取应 403，实际 {r.status_code}"


def test_member_cannot_read_others_research(client, register_user, auth_headers):
    """S1-2 固化：B 读取 A 的研究详情 → 403。"""
    ws, token_a, token_b, _ = _create_org_and_member(client, register_user, auth_headers)
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    rj = _insert_research(ws, me_a["id"])
    r = client.get(f"/api/v1/research/{rj}", headers=auth_headers(token_b, ws))
    assert r.status_code == 403, f"Member 读他人研究应 403，实际 {r.status_code}"


def test_member_cannot_approve_others_research(client, register_user, auth_headers):
    """S1-2 固化：B 批准 A 的付费研究 → 403。"""
    ws, token_a, token_b, _ = _create_org_and_member(client, register_user, auth_headers)
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    rj = _insert_research(ws, me_a["id"])
    r = client.post(
        f"/api/v1/research/{rj}/approve",
        headers=auth_headers(token_b, ws),
        json={"approved": True},
    )
    assert r.status_code == 403, f"Member 代批准付费研究应 403，实际 {r.status_code}"


def test_member_research_list_only_own(client, register_user, auth_headers):
    """S1-2 固化：B 的 research 列表不含 A 的 job。"""
    ws, token_a, token_b, _ = _create_org_and_member(client, register_user, auth_headers)
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    _insert_research(ws, me_a["id"])
    r = client.get("/api/v1/research", headers=auth_headers(token_b, ws))
    assert r.status_code == 200
    assert r.json() == [], f"Member 列表应只见自己的 job，实际 {r.json()}"


def test_owner_can_read_own_research(client, register_user, auth_headers):
    """对照：A 读自己的研究 → 200。"""
    ws, token_a, _, _ = _create_org_and_member(client, register_user, auth_headers)
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    rj = _insert_research(ws, me_a["id"])
    r = client.get(f"/api/v1/research/{rj}", headers=auth_headers(token_a, ws))
    assert r.status_code == 200, r.text
