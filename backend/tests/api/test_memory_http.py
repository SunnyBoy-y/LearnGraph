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


def test_memory_batch_delete_http(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    ids = []
    for title in ('batch first', 'batch second'):
        response = client.post('/api/v1/memory', headers=headers, json={'title': title, 'content': title})
        assert response.status_code in (200, 201), response.text
        ids.append(response.json()['id'])
    # A missing/foreign target must not delete the valid targets in the request.
    response = client.post('/api/v1/memory/batch-delete', headers=headers, json={'memory_ids': ids + ['missing']})
    assert response.status_code == 404, response.text
    assert {item['id'] for item in client.get('/api/v1/memory', headers=headers).json()} >= set(ids)
    response = client.post('/api/v1/memory/batch-delete', headers=headers, json={'memory_ids': ids})
    assert response.status_code == 200, response.text
    assert response.json() == {'deleted_count': 2, 'recoverable_count': 2}
    assert not set(ids) & {item['id'] for item in client.get('/api/v1/memory/views', headers=headers).json()}
    response = client.post('/api/v1/memory/batch-delete', headers=headers, json={'memory_ids': []})
    assert response.status_code == 422


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


def test_manual_extraction_only_retries_unknown_outcomes_when_explicit(client, register_user, auth_headers, monkeypatch):
    from app.api.routers import memory as memory_router

    token, ws, _, _ = register_user()
    observed = []

    def extract(*args, **kwargs):
        observed.append(kwargs)
        return {"status": "ok", "drafts_created": 0}

    monkeypatch.setattr(memory_router, "extract_session_memories", extract)
    headers = auth_headers(token, ws)
    created = client.post("/api/v1/sessions", headers=headers, json={"title": "retry extraction"})
    assert created.status_code in (200, 201), created.text
    response = client.post(
        f"/api/v1/memory/enhancement/extract/{created.json()['id']}?retry_unknown=true",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert len(observed) == 1
    assert observed[0]["force"] is True
    assert observed[0]["retry_unknown"] is True
    assert observed[0]["actor_id"]


def test_memory_context_build(client, register_user, auth_headers):
    """V2 上下文构建：无会话时返回 4xx 或明确空结果，不得 500。"""
    token, ws, _, _ = register_user()
    r = client.post(
        "/api/v1/memory/context/build",
        headers=auth_headers(token, ws),
        json={"query": "学习目标", "workspace_id": ws},
    )
    assert r.status_code in (200, 400, 404, 422), r.text


def test_memory_drafts_batch_decision_http(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    draft_ids = []
    for title in ("batch draft 1", "batch draft 2"):
        r = client.post(
            "/api/v1/memory/drafts",
            headers=headers,
            json={
                "operation": "CREATE",
                "memory_type": "learning_preference",
                "title": title,
                "content": f"{title} content",
            },
        )
        assert r.status_code == 201, r.text
        draft_ids.append(r.json()["id"])

    # Missing target rolls back atomically.
    r = client.post(
        "/api/v1/memory/drafts/batch-decision",
        headers=headers,
        json={"draft_ids": draft_ids + ["missing"], "decision": "commit", "reason": "user_confirmed"},
    )
    assert r.status_code == 404, r.text
    pending = client.get("/api/v1/memory/drafts?status=PENDING", headers=headers).json()
    assert {item["id"] for item in pending} >= set(draft_ids)

    # Batch commit all valid drafts.
    r = client.post(
        "/api/v1/memory/drafts/batch-decision",
        headers=headers,
        json={"draft_ids": draft_ids, "decision": "commit", "reason": "user_confirmed"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"processed_count": 2, "decision": "commit"}
    remaining_pending = client.get("/api/v1/memory/drafts?status=PENDING", headers=headers).json()
    assert not (set(draft_ids) & {item["id"] for item in remaining_pending})

    # Batch reject another draft.
    r = client.post(
        "/api/v1/memory/drafts",
        headers=headers,
        json={
            "operation": "CREATE",
            "memory_type": "learning_preference",
            "title": "batch draft reject",
            "content": "to reject",
        },
    )
    assert r.status_code == 201, r.text
    reject_id = r.json()["id"]
    r = client.post(
        "/api/v1/memory/drafts/batch-decision",
        headers=headers,
        json={"draft_ids": [reject_id], "decision": "reject", "reason": "outdated"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"processed_count": 1, "decision": "reject"}


def test_memory_batch_feedback_and_retract_http(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    ids = []
    for title in ("pending mem 1", "pending mem 2"):
        r = client.post(
            "/api/v1/memory",
            headers=headers,
            json={"title": title, "content": title},
        )
        assert r.status_code in (200, 201), r.text
        ids.append(r.json()["id"])

    r = client.post(
        "/api/v1/memory/batch-feedback",
        headers=headers,
        json={"memory_ids": ids + ["missing"], "feedback_type": "correct"},
    )
    assert r.status_code == 404, r.text

    r = client.post(
        "/api/v1/memory/batch-feedback",
        headers=headers,
        json={"memory_ids": ids, "feedback_type": "correct"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"processed_count": 2, "feedback_type": "correct"}

    r = client.post(
        "/api/v1/memory/batch-retract",
        headers=headers,
        json={"memory_ids": ids, "reason": "user_says_incorrect"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"processed_count": 2}
    for mid in ids:
        detail = client.get(f"/api/v1/memory/{mid}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["lifecycle_status"] == "retracted"
