"""files 接口补测：上传 / 列表 / 内容 / 分块 / 解析能力 / 删除。"""
from __future__ import annotations

import io

import pytest
from sqlalchemy import select


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


def test_document_dispatch_and_job_commit_together(client, register_user, auth_headers, monkeypatch):
    from app.core.database import SessionLocal
    from app.domain.models import DocumentJob, DurableJob
    from app.services import durable_queue

    token, workspace, _, _ = register_user("atomic_parse")
    headers = {**auth_headers(token, workspace), "Idempotency-Key": "atomic-parse"}
    uploaded = client.post("/api/v1/files", headers=headers, files={"file": ("parse.txt", b"Body", "text/plain")})
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["id"]
    url = f"/api/v1/files/{file_id}/parse"

    def fail_dispatch(*args, **kwargs):
        raise RuntimeError("document dispatch failure")

    with monkeypatch.context() as patches:
        patches.setattr(durable_queue, "enqueue_document_job", fail_dispatch)
        with pytest.raises(RuntimeError, match="document dispatch failure"):
            client.post(url, headers=headers)
    with SessionLocal() as db:
        assert db.scalar(select(DocumentJob).where(DocumentJob.file_id == file_id)) is None

    requested = client.post(url, headers=headers)
    assert requested.status_code == 202, requested.text
    job_id = requested.json()["id"]
    repeat = client.post(url, headers=headers)
    assert repeat.status_code == 202 and repeat.json()["id"] == job_id
    with SessionLocal() as db:
        dispatch, = db.scalars(select(DurableJob).where(DurableJob.workspace_id == workspace, DurableJob.kind == "document.parse_index")).all()
        assert dispatch.payload["document_job_id"] == job_id


def test_legacy_orphan_and_document_failure_are_visible_to_queue(client, register_user, auth_headers, monkeypatch):
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.domain.models import DocumentJob, DurableJob, Workspace
    from app.domain.schemas.files import DocumentJobCreate
    from app.services import document_learning, durable_queue

    token, workspace, _, _ = register_user("orphan_parse")
    headers = auth_headers(token, workspace)
    uploaded = client.post("/api/v1/files", headers=headers, files={"file": ("legacy.txt", b"Body", "text/plain")})
    assert uploaded.status_code == 201, uploaded.text
    with SessionLocal() as db:
        owner = db.get(Workspace, workspace).owner_user_id
        service = document_learning.DocumentLearningService(db, workspace, owner, get_settings())
        job, _ = service.create_job(uploaded.json()["id"], DocumentJobCreate(), None)
        job_id = job.id
    assert document_learning.reconcile_document_dispatches() >= 1
    assert document_learning.reconcile_document_dispatches() == 0

    def fail_parse(*args, **kwargs):
        raise document_learning.DocumentParseError("injected parse failure")

    monkeypatch.setattr(document_learning, "parse_document", fail_parse)
    while durable_queue.run_one_durable_job("test", kind="document.parse_index"):
        pass
    with SessionLocal() as db:
        assert db.get(DocumentJob, job_id).status == "failed"
        dispatch, = db.scalars(select(DurableJob).where(DurableJob.workspace_id == workspace, DurableJob.kind == "document.parse_index")).all()
        assert dispatch.status == "failed" and dispatch.last_error == "document_parse_failed"
    metrics = client.get("/api/v1/task-reliability", headers=headers)
    assert metrics.status_code == 200, metrics.text
    assert metrics.json()["durable_jobs"]["document.parse_index"]["final_failure_rate"] == 1


def test_document_worker_can_reclaim_a_running_parse(client, register_user, auth_headers):
    from datetime import timedelta

    from app.core.database import SessionLocal
    from app.domain.models import DocumentJob, DurableJob
    from app.services.durable_queue import DurableQueue, run_one_durable_job, utc_now

    token, workspace, _, _ = register_user("recover_parse")
    headers = auth_headers(token, workspace)
    uploaded = client.post("/api/v1/files", headers=headers, files={"file": ("recovery.txt", b"Recovery body", "text/plain")})
    assert uploaded.status_code == 201, uploaded.text
    response = client.post(f"/api/v1/files/{uploaded.json()['id']}/parse", headers=headers)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    with SessionLocal() as db:
        dispatch = db.scalar(select(DurableJob).where(DurableJob.workspace_id == workspace, DurableJob.kind == "document.parse_index"))
        dispatch.available_at = utc_now() - timedelta(minutes=2)
        db.commit()
        original = DurableQueue(db, worker_id="stopped", lease_seconds=1).claim_next(
            kind="document.parse_index", now=utc_now() - timedelta(minutes=1),
        )
        assert original.id == dispatch.id
        job = db.get(DocumentJob, job_id)
        job.status = "running"
        db.commit()
    assert run_one_durable_job("replacement", kind="document.parse_index")
    with SessionLocal() as db:
        assert db.get(DocumentJob, job_id).status == "completed"
