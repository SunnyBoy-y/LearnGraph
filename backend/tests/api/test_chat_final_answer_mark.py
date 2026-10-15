"""“最终答案边界”必须在回答开始时就持久化到 part 的 data.kind。

回归背景：`data.kind = "final_answer"` 原先只在收尾补写，刷新 / 中途接入 /
浏览器重放时正文会被重新判成思维链里的过程叙述（前端 chat-message-parts
直接用 `data.kind` 重建边界）。这里断言：

1. `answer.started` 的 payload 携带该 part 的 data 标记；
2. 紧随其后的第一个文本 `part.delta` 也携带同一份 data（在线客户端立刻生效）；
3. 流结束后 part 快照里的 kind 仍在（持久化）。
"""
from __future__ import annotations

import json


def _events(body: str) -> list[dict]:
    events: list[dict] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            events.append(json.loads(line[5:].strip()))
        except Exception:
            continue
    return events


def _text_deltas(events: list[dict]) -> list[dict]:
    return [
        event
        for event in events
        if event.get("type") == "part.delta"
        and ((event.get("payload") or {}).get("part") or {}).get("type") == "text"
    ]


def _create_session(client, headers, title="final-answer-mark"):
    response = client.post(
        "/api/v1/sessions", headers=headers, json={"title": title}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def test_answer_started_payload_carries_final_answer_mark(
    client, register_user, auth_headers, stub_chat_service
):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    response = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "标记时机测试"},
    )
    assert response.status_code == 200, response.text
    events = _events(response.text)
    started = next(
        event for event in events if event.get("type") == "answer.started"
    )
    payload = started.get("payload") or {}
    assert (payload.get("data") or {}).get("kind") == "final_answer", payload

    # 在线客户端不必依赖 answer.started 的解析：第一个文本 delta 自带该标记，
    # 且必须排在 answer.started 之后（边界先到，正文随后）。
    deltas = _text_deltas(events)
    assert deltas, events
    first_part = deltas[0]["payload"]["part"]
    assert (first_part.get("data") or {}).get("kind") == "final_answer", first_part
    assert events.index(started) < events.index(deltas[0])


def test_answer_mark_survives_stream_end(
    client, register_user, auth_headers, stub_chat_service
):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    response = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "持久化标记测试"},
    )
    assert response.status_code == 200, response.text
    listing = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers)
    assert listing.status_code == 200, listing.text
    assistant = [
        item
        for item in listing.json()["items"]
        if item.get("role") == "assistant"
    ][-1]
    marked = [
        part
        for part in assistant.get("parts", [])
        if part.get("type") == "text"
        and (part.get("data") or {}).get("kind") == "final_answer"
    ]
    assert marked, assistant.get("parts")
