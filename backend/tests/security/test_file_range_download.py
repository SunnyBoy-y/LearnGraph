from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request as StarletteRequest

from app.api.routers import files as files_router
from app.providers.local.storage import LocalObjectStorageProvider


class _FakeFileService:
    def __init__(self, storage: LocalObjectStorageProvider, record) -> None:
        self.storage = storage
        self._record = record

    def content_record(self, file_id: str):
        assert file_id == self._record.id
        return self._record


def _make_request(range_header: str | None) -> StarletteRequest:
    headers = []
    if range_header is not None:
        headers.append((b"range", range_header.encode("ascii")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/files/file-1/content",
        "raw_path": b"/api/v1/files/file-1/content",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    return StarletteRequest(scope)


async def _read_body(response) -> bytes:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, memoryview):
            chunks.append(chunk.tobytes())
        elif isinstance(chunk, bytearray):
            chunks.append(bytes(chunk))
        else:
            chunks.append(chunk)
    return b"".join(chunks)


def test_range_download_streams_partial_bytes(tmp_path: Path, monkeypatch) -> None:
    storage = LocalObjectStorageProvider(tmp_path / "objects")
    object_key = "ws/sample.bin"
    target = storage._resolve(object_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = b"abcdefghijklmnopqrstuvwxyz"
    target.write_bytes(payload)

    record = SimpleNamespace(
        id="file-1",
        size_bytes=len(payload),
        object_key=object_key,
        original_name="sample.bin",
        mime_type="application/octet-stream",
        sha256="deadbeef",
    )
    fake_service = _FakeFileService(storage, record)
    monkeypatch.setattr(files_router, "service", lambda *_args, **_kwargs: fake_service)
    monkeypatch.setattr(files_router, "_require_file_access", lambda *_args, **_kwargs: None)

    response = files_router.download_file(
        "file-1",
        _make_request("bytes=4-9"),
        db=SimpleNamespace(),
        context=SimpleNamespace(),
        settings=SimpleNamespace(),
    )
    assert response.status_code == 206
    assert response.headers["Content-Range"] == "bytes 4-9/26"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.headers["Content-Length"] == "6"
    body = asyncio.run(_read_body(response))
    assert body == b"efghij"


def test_invalid_range_returns_416(tmp_path: Path, monkeypatch) -> None:
    storage = LocalObjectStorageProvider(tmp_path / "objects")
    object_key = "ws/sample.bin"
    target = storage._resolve(object_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"0123456789")
    record = SimpleNamespace(
        id="file-1",
        size_bytes=10,
        object_key=object_key,
        original_name="sample.bin",
        mime_type="application/octet-stream",
        sha256="cafe",
    )
    fake_service = _FakeFileService(storage, record)
    monkeypatch.setattr(files_router, "service", lambda *_args, **_kwargs: fake_service)
    monkeypatch.setattr(files_router, "_require_file_access", lambda *_args, **_kwargs: None)

    response = files_router.download_file(
        "file-1",
        _make_request("bytes=99-120"),
        db=SimpleNamespace(),
        context=SimpleNamespace(),
        settings=SimpleNamespace(),
    )
    assert response.status_code == 416
