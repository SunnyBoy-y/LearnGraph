"""带附件发送的回归：preflight 必须能解析附件策略函数。

真实故障（2026-09-14，容器 learngraph-app-1 日志实证）：
``ChatService._validate_context`` 调用了 ``is_special_binary_attachment``，
但该符号没有从 ``app.services.chat_attachment_policy`` 导入。Python 只在运行到
这一行时才解析名字，所以它不影响 import、不影响启动、不影响无附件对话，只在
「这次发送带了附件」时抛 ``NameError``。

现象链：
  POST /api/v1/files                      -> 201（上传本身没问题）
  POST /api/v1/sessions/{id}/messages/stream -> 500 Internal Server Error
  前端 -> 「连接中断，正在重连（n/5）」+「HTTP 500 · http_error · Internal Server Error」

本用例只钉住不改的性质：带附件的发送请求不允许以 500 收场（4xx 是业务拒绝，允许）。
"""

from __future__ import annotations

import io


def _create_session(client, headers, title="attachment-session"):
    r = client.post("/api/v1/sessions", headers=headers, json={"title": title})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _upload(client, headers, name="note.txt", content=b"hello attachment"):
    r = client.post(
        "/api/v1/files",
        headers=headers,
        files={"file": (name, io.BytesIO(content), "text/plain")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_stream_with_single_attachment_is_not_500(
    client, register_user, auth_headers, stub_chat_service
):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    file_id = _upload(client, headers)

    r = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "看下这个附件", "file_ids": [file_id]},
    )
    assert r.status_code != 500, r.text
    assert "NameError" not in r.text


def test_stream_with_multiple_attachments_is_not_500(
    client, register_user, auth_headers, stub_chat_service
):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)
    first = _upload(client, headers, name="a.txt", content=b"first attachment")
    second = _upload(client, headers, name="b.txt", content=b"second attachment")

    r = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "两个附件一起看", "file_ids": [first, second]},
    )
    assert r.status_code != 500, r.text
    assert "NameError" not in r.text
