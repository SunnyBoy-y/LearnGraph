"""API 测试：网页抓取审批记录列表 + 个人白名单（设置页「搜索与抓取」数据源）。

覆盖：
- GET /fetch-authorizations 返回持久化的审批记录（含 allow_always / allow_once / deny）
- 字段映射：requested_by = actor_id、decided_by / decided_at / created_at、created_at 倒序
- 普通成员列表只见自己的记录；工作区 manager 可见全部
- status 过滤
- GET/PUT /fetch-authorizations/user-policy：个人白名单读写、去重、个人隔离
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import text

from app.core.database import SessionLocal


def _create_org_and_member(client, register_user, auth_headers):
    """建组织（A 为 Admin）+ B 以 Member 加入。返回 (ws, token_a, token_b, org_id)。"""
    token_a, _, _, _ = register_user("histA")
    token_b, _, _, _ = register_user("histB")
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    me_b = client.get("/api/v1/auth/me", headers=auth_headers(token_b)).json()
    tenant_a = me_a["tenant_id"]
    with SessionLocal() as db:
        db.execute(
            text("UPDATE users SET tenant_id=:t WHERE id=:uid"),
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
        json={"name": f"审批历史组织 {uuid.uuid4().hex[:6]}", "workspace_name": "共享空间"},
    )
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]
    with SessionLocal() as db:
        ws = db.execute(
            text("SELECT id FROM workspaces WHERE organization_id=:oid"),
            {"oid": org_id},
        ).scalar_one()
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


def _new_session(client, headers, ws):
    r = client.post("/api/v1/sessions", headers=headers, json={"title": "审批历史会话"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _insert_fetch_auth(
    client,
    headers,
    ws,
    actor_id: str,
    hostname: str = "example.com",
    status: str = "pending",
    decision: str | None = None,
    decided_by: str | None = None,
    tool_call_id: str = "tc-1",
):
    """SQL 直插一条 fetch_authorization_requests 记录（chat_session 经 API 建）。"""
    session_id = _new_session(client, headers, ws)
    rid = str(uuid.uuid4())
    now = __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()
    with SessionLocal() as db:
        db.execute(
            text(
                """INSERT INTO fetch_authorization_requests
                   (id, workspace_id, chat_session_id, actor_id, tool_call_id, tool_name,
                    requested_url, hostname, status, decision, decided_by, decided_at,
                    resume_payload, created_at, updated_at)
                   VALUES (:id, :ws, :sid, :actor, :tc, 'fetch_web_page',
                    :url, :host, :status, :decision, :decided_by, :decided_at,
                    :payload, :now, :now)"""
            ),
            {
                "id": rid,
                "ws": ws,
                "sid": session_id,
                "actor": actor_id,
                "tc": tool_call_id,
                "url": f"https://{hostname}/page",
                "host": hostname,
                "status": status,
                "decision": decision,
                "decided_by": decided_by,
                "decided_at": now if decided_by else None,
                "payload": (
                    json.dumps({"request": {"url": f"https://{hostname}/page"}})
                    if status == "approved"
                    else None
                ),
                "now": now,
            },
        )
        db.commit()
    return rid


def _list(client, token, ws, **query):
    resp = client.get(
        "/api/v1/fetch-authorizations",
        params=query,
        headers={"Authorization": f"Bearer {token}", "X-Workspace-ID": ws},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_fetch_approval_history_lists_persisted_records(client, register_user, auth_headers):
    """列表返回持久化审批记录，字段映射与倒序正确。"""
    ws, token_a, _, _ = _create_org_and_member(client, register_user, auth_headers)
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    _insert_fetch_auth(
        client, auth_headers(token_a, ws), ws, me_a["id"],
        hostname="example.com", status="approved", decision="allow_always", decided_by=me_a["id"],
    )
    _insert_fetch_auth(
        client, auth_headers(token_a, ws), ws, me_a["id"],
        hostname="pending.io", status="pending",
    )
    body = _list(client, token_a, ws)
    assert body["total"] == 2
    # created_at 倒序：后插入的 pending.io 在前
    assert [item["hostname"] for item in body["items"]] == ["pending.io", "example.com"]
    approved = body["items"][1]
    assert approved["requested_by"] == me_a["id"]
    assert approved["decision"] == "allow_always"
    assert approved["decided_by"] == me_a["id"]
    assert approved["decided_at"] is not None
    assert approved["requested_url"] == "https://example.com/page"
    assert approved["status"] == "approved"


def test_fetch_approval_history_status_filter(client, register_user, auth_headers):
    ws, token_a, _, _ = _create_org_and_member(client, register_user, auth_headers)
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    _insert_fetch_auth(
        client, auth_headers(token_a, ws), ws, me_a["id"],
        hostname="allow.com", status="approved", decision="allow_once", decided_by=me_a["id"],
    )
    _insert_fetch_auth(
        client, auth_headers(token_a, ws), ws, me_a["id"],
        hostname="deny.com", status="denied", decision="deny", decided_by=me_a["id"],
    )
    body = _list(client, token_a, ws, status="approved")
    assert body["total"] == 1
    assert body["items"][0]["hostname"] == "allow.com"
    assert body["items"][0]["decision"] == "allow_once"


def test_fetch_approval_history_member_sees_only_own(client, register_user, auth_headers):
    """普通成员列表只见自己的记录；owner（manager）可见全部。"""
    ws, token_a, token_b, _ = _create_org_and_member(client, register_user, auth_headers)
    me_a = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    me_b = client.get("/api/v1/auth/me", headers=auth_headers(token_b)).json()
    _insert_fetch_auth(
        client, auth_headers(token_a, ws), ws, me_a["id"],
        hostname="owner.com", status="approved", decision="allow_once", decided_by=me_a["id"],
    )
    _insert_fetch_auth(
        client, auth_headers(token_b, ws), ws, me_b["id"],
        hostname="member.com", status="pending",
    )
    # Member：只见自己的 pending 记录
    body_b = _list(client, token_b, ws)
    assert body_b["total"] == 1
    assert [item["hostname"] for item in body_b["items"]] == ["member.com"]
    # Owner（manager）：可见全部两条
    body_a = _list(client, token_a, ws)
    assert body_a["total"] == 2
    assert {item["hostname"] for item in body_a["items"]} == {"owner.com", "member.com"}


def test_fetch_user_policy_get_put_roundtrip(client, register_user, auth_headers):
    """个人白名单读写：默认空 → PUT 写入 → GET 返回 → 清空。"""
    ws, token_a, _, _ = _create_org_and_member(client, register_user, auth_headers)
    headers = auth_headers(token_a, ws)
    r = client.get("/api/v1/fetch-authorizations/user-policy", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"allowed_domains": [], "allow_without_confirmation": False}

    r = client.put(
        "/api/v1/fetch-authorizations/user-policy",
        headers=headers,
        json={"allowed_domains": ["Example.COM", "example.com", "github.com"], "allow_without_confirmation": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 去重 + casefold
    assert body["allowed_domains"] == ["example.com", "github.com"]
    assert body["allow_without_confirmation"] is True

    again = client.get("/api/v1/fetch-authorizations/user-policy", headers=headers)
    assert again.status_code == 200
    assert again.json()["allowed_domains"] == ["example.com", "github.com"]

    r = client.put(
        "/api/v1/fetch-authorizations/user-policy",
        headers=headers,
        json={"allowed_domains": [], "allow_without_confirmation": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["allowed_domains"] == []


def test_fetch_user_policy_is_personal(client, register_user, auth_headers):
    """个人白名单按用户隔离：A 写入不影响 B。"""
    ws, token_a, token_b, _ = _create_org_and_member(client, register_user, auth_headers)
    r = client.put(
        "/api/v1/fetch-authorizations/user-policy",
        headers=auth_headers(token_a, ws),
        json={"allowed_domains": ["private.example"], "allow_without_confirmation": False},
    )
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/fetch-authorizations/user-policy", headers=auth_headers(token_b, ws))
    assert r.status_code == 200, r.text
    assert r.json()["allowed_domains"] == []
