"""chat 接口补测：会话 CRUD / 流式（StubModelProvider）/ 幂等 / 历史分页 / 取消路由。

对应审计发现回归：A1-1/A1-2（流式事件序）、T1-1（会话幂等）、A1-4（取消路由存在）。
"""
from __future__ import annotations

import uuid

import json


def _create_session(client, headers, title="test-session"):
    r = client.post(
        "/api/v1/sessions", headers=headers, json={"title": title}
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_create_session_and_list(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers, "s1")
    r = client.get("/api/v1/sessions", headers=headers)
    assert r.status_code == 200
    titles = [s["title"] for s in r.json()]
    assert "s1" in titles


def test_stream_message_events_sequence(client, register_user, auth_headers, stub_chat_service):
    """A1-1/A1-2 回归：流式事件序 message.accepted → part.delta → message.completed。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    r = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "你好"},
    )
    assert r.status_code == 200, r.text
    body = r.text
    types = []
    for line in body.splitlines():
        if line.startswith("data:"):
            try:
                types.append(json.loads(line[5:].strip())["type"])
            except Exception:
                pass
    assert "message.accepted" in types, types[:10]
    assert "message.started" in types, types[:10]
    assert any("delta" in t for t in types), types[:10]
    assert "message.completed" in types, types[-10:]
    # 最终回答边界：非 Agent 模式下首个文本即最终回答，answer.started 必须出现
    assert "answer.started" in types, types
    # 事件序：accepted 必须在 started 之前
    assert types.index("message.accepted") < types.index("message.started")


def test_stream_final_answer_marked(client, register_user, auth_headers, stub_chat_service):
    """最终回答 Text Part 必须带 data.kind=final_answer 且 provider_trace 记录思考耗时。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    r = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "最终回答标记测试"},
    )
    assert r.status_code == 200, r.text
    r = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers)
    data = r.json()
    assistant = [m for m in data["items"] if m.get("role") == "assistant"][-1]
    final_parts = [
        p for p in assistant.get("parts", [])
        if p.get("type") == "text" and p.get("data", {}).get("kind") == "final_answer"
    ]
    assert final_parts, assistant.get("parts")
    trace = assistant.get("provider_trace", {})
    assert "thinking_duration_ms" in trace, trace
    assert "final_answer_started_at" in trace, trace


def test_stream_message_persisted(client, register_user, auth_headers, stub_chat_service):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    r = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "持久化测试"},
    )
    assert r.status_code == 200
    r = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert "items" in data  # 分页结构
    roles = [m["role"] for m in data["items"]]
    assert "user" in roles and "assistant" in roles
    assert data["total_count"] >= 2


def test_idempotency_key_dedupes_message(client, register_user, auth_headers, stub_chat_service):
    """T1-1 正面对照：消息层 Idempotency-Key 双发只产生 1 条用户消息（实测回归）。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    key = "audit-idem-key-001"
    body = {"content": "幂等测试"}
    r1 = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers={**headers, "Idempotency-Key": key},
        json=body,
    )
    r2 = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers={**headers, "Idempotency-Key": key},
        json=body,
    )
    assert r1.status_code == 200 and r2.status_code == 200
    r = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers)
    user_msgs = [m for m in r.json()["items"] if m.get("role") == "user"]
    assert len(user_msgs) == 1, f"期望 1 条用户消息（幂等去重），实际 {len(user_msgs)}"


def test_concurrent_session_create_with_idempotency_key(client, register_user, auth_headers):
    """T1-1 修复固化：带同一 Idempotency-Key 并发创建会话 → 仅 1 条（幂等生效）。"""
    import threading

    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    key = f"audit-session-idem-{uuid.uuid4()}"
    results = []

    def create():
        results.append(
            client.post(
                "/api/v1/sessions",
                headers={**headers, "Idempotency-Key": key},
                json={"title": "dup-session-idem"},
            )
        )

    t1, t2 = threading.Thread(target=create), threading.Thread(target=create)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    # 两次返回同一会话 id
    assert results[0].json()["id"] == results[1].json()["id"]
    r = client.get("/api/v1/sessions", headers=headers)
    matches = [s for s in r.json() if s["title"] == "dup-session-idem"]
    assert len(matches) == 1, f"带 Idempotency-Key 并发创建应只有 1 条，实际 {len(matches)}"


def test_session_create_without_key_creates_separate(client, register_user, auth_headers):
    """对照观察：无 Idempotency-Key 时双发创建 2 条（符合"无键不幂等"语义，非缺陷）。"""
    import threading

    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    results = []

    def create():
        results.append(
            client.post("/api/v1/sessions", headers=headers, json={"title": "dup-no-key"})
        )

    t1, t2 = threading.Thread(target=create), threading.Thread(target=create)
    t1.start(); t2.start(); t1.join(); t2.join()
    r = client.get("/api/v1/sessions", headers=headers)
    matches = [s for s in r.json() if s["title"] == "dup-no-key"]
    assert len(matches) == 2, f"无 key 双发应创建 2 条独立会话，实际 {len(matches)}"


def test_messages_pagination_shape(client, register_user, auth_headers, stub_chat_service):
    """B1-2 回归锚点：消息列表返回分页结构（items/total_count/has_more_before）。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    for i in range(3):
        client.post(
            f"/api/v1/sessions/{sid}/messages/stream",
            headers=headers,
            json={"content": f"第{i}条"},
        )
    r = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers)
    data = r.json()
    assert set(("items", "total_count", "has_more_before", "oldest_id", "newest_id")) <= set(data)
    assert data["total_count"] >= 6  # 3 用户 + 3 助手


def test_cancel_route_accepts_pending_message(client, register_user, auth_headers, stub_chat_service):
    """A1-4 关联：取消路由存在且对已存在消息返回可解释结果（200 或明确 4xx）。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    r = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "取消测试"},
    )
    assert r.status_code == 200
    data = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()
    user_msg = [m for m in data["items"] if m.get("role") == "user"][0]
    mid = user_msg["id"]
    r = client.post(
        f"/api/v1/sessions/{sid}/messages/{mid}/cancel",
        headers=headers,
    )
    # 已完成消息取消：200（幂等标记）或 4xx（明确语义）均可，但不得 500
    assert r.status_code in (200, 400, 404, 409), r.text


def test_stream_without_content_422(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    r = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={},
    )
    assert r.status_code == 422


def test_list_session_files_endpoint(client, register_user, auth_headers):
    """统一会话文件视图端点：空会话返回 []，且权限受控。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    r = client.get(f"/api/v1/sessions/{sid}/files", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == []
    # 未知会话 → 404（ACL 门）
    r = client.get(f"/api/v1/sessions/{uuid.uuid4()}/files", headers=headers)
    assert r.status_code == 404
