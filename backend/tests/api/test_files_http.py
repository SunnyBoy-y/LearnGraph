"""files 接口补测：上传 / 列表 / 内容 / 分块 / 解析能力 / 删除。"""
from __future__ import annotations

import io


def test_parser_capabilities(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    r = client.get(
        "/api/v1/files/parser-capabilities", headers=auth_headers(token, ws)
    )
    assert r.status_code == 200, r.text


def test_upload_and_list(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    r = client.post(
        "/api/v1/files",
        headers=headers,
        files={"file": ("hello.txt", io.BytesIO(b"hello world content"), "text/plain")},
    )
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    r = client.get("/api/v1/files", headers=headers)
    assert r.status_code == 200
    assert any(f["id"] == fid for f in r.json())


def test_file_content_and_chunks(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    r = client.post(
        "/api/v1/files",
        headers=headers,
        files={"file": ("note.txt", io.BytesIO(b"database index basics"), "text/plain")},
    )
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    r = client.get(f"/api/v1/files/{fid}/content", headers=headers)
    assert r.status_code == 200, r.text
    r = client.get(f"/api/v1/files/{fid}/chunks", headers=headers)
    assert r.status_code == 200, r.text


def test_upload_empty_file_accepted(client, register_user, auth_headers):
    """观察项记录：空文件当前被接受（201，size 0，不触发解析）。
    若产品要求拒绝空文件，此测试应在修复后改为断言 4xx。"""
    token, ws, _, _ = register_user()
    r = client.post(
        "/api/v1/files",
        headers=auth_headers(token, ws),
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["size_bytes"] == 0


def test_storage_summary(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    r = client.get(
        "/api/v1/files/storage-summary", headers=auth_headers(token, ws)
    )
    assert r.status_code == 200, r.text


def test_file_delete(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    r = client.post(
        "/api/v1/files",
        headers=headers,
        files={"file": ("del.txt", io.BytesIO(b"delete me"), "text/plain")},
    )
    fid = r.json()["id"]
    r = client.post(f"/api/v1/files/{fid}/delete", headers=headers, json={"confirmation_text": "del.txt"})
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/files", headers=headers)
    assert all(f["id"] != fid for f in r.json())
