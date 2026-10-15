"""memory 接口补测（HTTP 层）：列表 / 创建 / CRUD / 草稿 / 策略 / profile / V2 检索。"""
from __future__ import annotations

import uuid


def test_memory_list_and_policy(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    r = client.get("/api/v1/memory", headers=headers)
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/memory/policy", headers=headers)
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/memory/views", headers=headers)
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/memory/profile", headers=headers)
    assert r.status_code == 200, r.text


def test_memory_crud_cycle(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    r = client.post(
        "/api/v1/memory",
        headers=headers,
        json={
            "title": "SQL 索引原理",
            "content": "补测记忆条目：SQL 索引原理",
            "namespace": "workspace",
            "zone": "recent",
        },
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    mid = body.get("id") or body.get("memory_id")
    assert mid, body
    r = client.get(f"/api/v1/memory/{mid}", headers=headers)
    assert r.status_code == 200, r.text
    r = client.patch(
        f"/api/v1/memory/{mid}", headers=headers, json={"content": "更新后的记忆内容"}
    )
    assert r.status_code == 200, r.text
    r = client.delete(f"/api/v1/memory/{mid}", headers=headers)
    assert r.status_code == 200, r.text


def test_memory_drafts_list(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    r = client.get("/api/v1/memory/drafts", headers=auth_headers(token, ws))
    assert r.status_code == 200, r.text


def test_memory_v2_search_validation(client, register_user, auth_headers):
    """V2 检索：无查询词应 4xx；有查询词返回 200（空结果可接受）。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    r = client.post("/api/v1/memory/search", headers=headers, json={})
    assert r.status_code == 422, r.text
    r = client.post(
        "/api/v1/memory/search",
        headers=headers,
        json={"query": "索引", "workspace_id": ws},
    )
    assert r.status_code == 200, r.text


def test_memory_architecture_status(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    r = client.get(
        "/api/v1/memory/architecture/status", headers=auth_headers(token, ws)
    )
    assert r.status_code == 200, r.text


def test_memory_context_build(client, register_user, auth_headers):
    """V2 上下文构建：无会话时返回 4xx 或明确空结果，不得 500。"""
    token, ws, _, _ = register_user()
    r = client.post(
        "/api/v1/memory/context/build",
        headers=auth_headers(token, ws),
        json={"query": "学习目标", "workspace_id": ws},
    )
    assert r.status_code in (200, 400, 404, 422), r.text
