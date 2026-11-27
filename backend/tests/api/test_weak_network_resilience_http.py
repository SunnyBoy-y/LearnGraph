"""Weak-network resilience HTTP & transport tests: GZip, SSE keepalive, chunked resumable upload."""
from __future__ import annotations

import hashlib
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from app.api.routers.chat import SSE_KEEPALIVE_COMMENT, _detached_sse_transport
from app.main import SafeGZipMiddleware


def test_safe_gzip_middleware_compresses_json_and_skips_sse():
    mini = FastAPI()
    mini.add_middleware(SafeGZipMiddleware, minimum_size=512, compresslevel=5)

    @mini.get("/big-json")
    def big_json():
        return JSONResponse({"items": ["x" * 120 for _ in range(40)]})

    @mini.get("/small-json")
    def small_json():
        return JSONResponse({"ok": True})

    @mini.get("/events")
    def events():
        def gen():
            yield ": ready\n\n"
            yield f"data: {'a' * 2048}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-store, no-transform"},
        )

    client = TestClient(mini)

    r_big = client.get("/big-json", headers={"Accept-Encoding": "gzip"})
    assert r_big.status_code == 200
    assert r_big.headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in r_big.headers.get("vary", "").lower()
    assert len(r_big.json()["items"]) == 40

    r_identity = client.get("/big-json", headers={"Accept-Encoding": "identity"})
    assert r_identity.status_code == 200
    assert r_identity.headers.get("content-encoding") is None

    r_small = client.get("/small-json", headers={"Accept-Encoding": "gzip"})
    assert r_small.status_code == 200
    assert r_small.headers.get("content-encoding") is None

    r_sse = client.get("/events", headers={"Accept-Encoding": "gzip"})
    assert r_sse.status_code == 200
    assert r_sse.headers.get("content-encoding") is None
    assert "data: " in r_sse.text


def test_detached_sse_transport_emits_keepalive_during_silence():
    def slow_producer():
        time.sleep(0.18)
        yield "event: ping\ndata: {\"ok\":true}\n\n"

    chunks = list(
        _detached_sse_transport(
            slow_producer,
            session_id="sess-keepalive",
            thread_name="test-sse-keepalive",
            keepalive_interval_seconds=0.05,
        )
    )
    assert SSE_KEEPALIVE_COMMENT in chunks
    assert any("event: ping" in c for c in chunks)


def test_chunked_upload_resume_complete_and_dedup(client, register_user, auth_headers):
    token, ws, _, _ = register_user("chunk_upload_user")
    headers = auth_headers(token, ws)

    payload = (b"LearnGraph weak-network resumable upload payload.\n" * 4000)
    total_size = len(payload)
    chunk_size = 64 * 1024
    digest = hashlib.sha256(payload).hexdigest()

    init_resp = client.post(
        "/api/v1/files/uploads/init",
        headers=headers,
        json={
            "filename": "notes.txt",
            "total_size": total_size,
            "chunk_size": chunk_size,
            "content_type": "text/plain",
            "source": "library_upload",
            "sha256": digest,
        },
    )
    assert init_resp.status_code == 201, init_resp.text
    session = init_resp.json()
    assert session["already_exists"] is False
    upload_id = session["upload_id"]
    total_chunks = session["total_chunks"]
    assert total_chunks >= 3

    # Upload chunk 0, then check resume status endpoint.
    part0 = payload[0:chunk_size]
    p0 = client.put(
        f"/api/v1/files/uploads/{upload_id}/chunks/0",
        headers={**headers, "Content-Type": "application/octet-stream"},
        content=part0,
    )
    assert p0.status_code == 200, p0.text
    assert p0.json()["uploaded_chunks"] == [0]

    # Completing early with missing chunks must fail with 400.
    early = client.post(f"/api/v1/files/uploads/{upload_id}/complete", headers=headers)
    assert early.status_code == 400

    status_resp = client.get(f"/api/v1/files/uploads/{upload_id}", headers=headers)
    assert status_resp.status_code == 200, status_resp.text
    assert status_resp.json()["uploaded_chunks"] == [0]

    # Upload remaining chunks (including idempotent re-upload of chunk 0).
    for idx in range(total_chunks):
        start = idx * chunk_size
        end = min(total_size, start + chunk_size)
        resp = client.put(
            f"/api/v1/files/uploads/{upload_id}/chunks/{idx}",
            headers={**headers, "Content-Type": "application/octet-stream"},
            content=payload[start:end],
        )
        assert resp.status_code == 200, resp.text

    complete = client.post(f"/api/v1/files/uploads/{upload_id}/complete", headers=headers)
    assert complete.status_code == 201, complete.text
    file_view = complete.json()
    assert file_view["original_name"] == "notes.txt"
    assert file_view["size_bytes"] == total_size
    assert file_view["sha256"] == digest

    downloaded = client.get(f"/api/v1/files/{file_view['id']}/content", headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.content == payload

    # Re-initializing with same name + sha256 short-circuits via dedup.
    dedup = client.post(
        "/api/v1/files/uploads/init",
        headers=headers,
        json={
            "filename": "notes.txt",
            "total_size": total_size,
            "chunk_size": chunk_size,
            "content_type": "text/plain",
            "source": "library_upload",
            "sha256": digest,
        },
    )
    assert dedup.status_code == 201, dedup.text
    dedup_body = dedup.json()
    assert dedup_body["already_exists"] is True
    assert dedup_body["existing_file"]["id"] == file_view["id"]


def test_chunked_upload_abort_and_checksum_mismatch(client, register_user, auth_headers):
    token, ws, _, _ = register_user("chunk_upload_abort")
    headers = auth_headers(token, ws)

    data = b"A" * (70 * 1024)
    wrong_sha = "0" * 64
    init_resp = client.post(
        "/api/v1/files/uploads/init",
        headers=headers,
        json={
            "filename": "bad.txt",
            "total_size": len(data),
            "chunk_size": 64 * 1024,
            "content_type": "text/plain",
            "sha256": wrong_sha,
        },
    )
    assert init_resp.status_code == 201
    upload_id = init_resp.json()["upload_id"]
    for idx, chunk in enumerate((data[: 64 * 1024], data[64 * 1024 :])):
        res = client.put(
            f"/api/v1/files/uploads/{upload_id}/chunks/{idx}",
            headers=headers,
            content=chunk,
        )
        assert res.status_code == 200

    mismatch = client.post(f"/api/v1/files/uploads/{upload_id}/complete", headers=headers)
    assert mismatch.status_code == 400

    abort_resp = client.delete(f"/api/v1/files/uploads/{upload_id}", headers=headers)
    assert abort_resp.status_code == 200
    gone = client.get(f"/api/v1/files/uploads/{upload_id}", headers=headers)
    assert gone.status_code == 404
