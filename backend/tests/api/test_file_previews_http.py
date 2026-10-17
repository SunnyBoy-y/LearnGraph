from __future__ import annotations

import os
from pathlib import Path

import pytest

PDF = b"%PDF-1.4\n" + b"preview-content" * 32


def upload(client, headers, name="legacy.doc", payload=b"legacy", mime="application/msword"):
    response = client.post("/api/v1/files", headers=headers, files={"file": (name, payload, mime)})
    assert response.status_code == 201, response.text
    return response.json()


def test_office_preview_ranges_cache_and_workspace_isolation(client, register_user, auth_headers, monkeypatch):
    from app.services.sandbox import SandboxTaskService
    calls = []
    monkeypatch.setattr(SandboxTaskService, "office_pdf_preview", lambda self, file: calls.append(file.id) or PDF)
    token, workspace, _, _ = register_user("preview")
    headers = auth_headers(token, workspace)
    file = upload(client, headers)
    url = f"/api/v1/files/{file['id']}/preview.pdf"
    first = client.get(url, headers={**headers, "Range": "bytes=0-15"})
    assert first.status_code == 206, first.text
    assert first.content == PDF[:16]
    assert first.headers["content-range"] == f"bytes 0-15/{len(PDF)}"
    assert client.get(url, headers=headers).content == PDF
    assert calls == [file["id"]]
    other_token, other_workspace, _, _ = register_user("otherpreview")
    assert client.get(url, headers=auth_headers(other_token, other_workspace)).status_code in {403, 404}
    assert calls == [file["id"]]
    delete = client.post(f"/api/v1/files/{file['id']}/delete", headers=headers, json={"confirmation_text": "legacy.doc"})
    assert delete.status_code == 200, delete.text
    assert client.get(url, headers=headers).status_code == 404


def test_file_metadata_and_scoped_stream_ticket(client, register_user, auth_headers):
    token, workspace, _, _ = register_user("stream")
    headers = auth_headers(token, workspace)
    file = upload(client, headers, "video.mp4", b"0123456789", "video/mp4")
    metadata = client.get(f"/api/v1/files/{file['id']}", headers=headers)
    assert metadata.status_code == 200 and metadata.json()["size_bytes"] == 10
    ticket = client.get(f"/api/v1/files/{file['id']}/stream-ticket", headers=headers).json()["ticket"]
    assert token not in ticket
    stream_url = f"/api/v1/files/{file['id']}/stream?ticket={ticket}"
    response = client.get(stream_url, headers={"Range": "bytes=2-5"})
    assert response.status_code == 206 and response.content == b"2345"
    other = upload(client, headers, "other.mp4", b"other", "video/mp4")
    assert client.get(f"/api/v1/files/{other['id']}/stream?ticket={ticket}").status_code == 401
    assert client.get("/api/v1/files", headers=auth_headers(ticket, workspace)).status_code == 401
    assert client.post("/api/v1/auth/logout", headers=headers).status_code in {200, 204}
    assert client.get(stream_url).status_code == 401


def test_stream_ticket_expires(client, register_user, auth_headers, monkeypatch):
    from app.services import file_stream_tickets
    token, workspace, _, _ = register_user("expirestream")
    headers = auth_headers(token, workspace)
    file = upload(client, headers, "audio.mp3", b"audio", "audio/mpeg")
    ticket = client.get(f"/api/v1/files/{file['id']}/stream-ticket", headers=headers).json()["ticket"]
    now = file_stream_tickets.time.time()
    monkeypatch.setattr(file_stream_tickets.time, "time", lambda: now + 3601)
    assert client.get(f"/api/v1/files/{file['id']}/stream?ticket={ticket}").status_code == 401


def test_converter_unavailable_is_explicit(client, register_user, auth_headers, monkeypatch):
    from app.services.sandbox import SandboxTaskService
    from app.providers.remote.sandbox import SandboxBackendUnavailable
    def unavailable(self, file):
        raise SandboxBackendUnavailable("not installed")
    monkeypatch.setattr(SandboxTaskService, "office_pdf_preview", unavailable)
    token, workspace, _, _ = register_user("noconverter")
    headers = auth_headers(token, workspace)
    file = upload(client, headers)
    result = client.get(f"/api/v1/files/{file['id']}/preview.pdf", headers=headers)
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "office_preview_unavailable"


@pytest.mark.integration
@pytest.mark.parametrize("extension, mime, expected", [
    ("doc", "application/msword", "Reader audit Word baseline"),
    ("xls", "application/vnd.ms-excel", "Reader audit Excel baseline"),
    ("ppt", "application/vnd.ms-powerpoint", "Reader audit PPT baseline"),
])
def test_real_office_conversion_in_offline_sandbox(client, register_user, auth_headers, monkeypatch, extension, mime, expected):
    image = os.environ.get("LEARNGRAPH_TEST_READER_IMAGE")
    if not image:
        pytest.skip("Set LEARNGRAPH_TEST_READER_IMAGE to the built sandbox image digest")
    from app.core.config import get_settings
    from app.providers.remote.sandbox import DockerSandboxBackend
    from app.providers.sandbox_registry import get_sandbox_backend_registry
    settings = get_settings()
    monkeypatch.setattr(settings, "sandbox_image", image)
    backend = DockerSandboxBackend(enabled=True, image_ref=image)
    monkeypatch.setattr(get_sandbox_backend_registry(), "default", lambda settings: backend)
    token, workspace, _, _ = register_user("realoffice")
    headers = auth_headers(token, workspace)
    source = Path(__file__).parents[1] / "fixtures" / "readers" / f"normal.{extension}"
    payload = source.read_bytes()
    assert payload.startswith(bytes.fromhex("d0cf11e0a1b11ae1"))
    file = upload(client, headers, source.name, payload, mime)
    response = client.get(f"/api/v1/files/{file['id']}/preview.pdf", headers=headers)
    assert response.status_code == 200, response.text
    import io
    from pypdf import PdfReader
    assert expected in " ".join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)
