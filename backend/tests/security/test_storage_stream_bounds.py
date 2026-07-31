from __future__ import annotations

from pathlib import Path

from app.providers.local.storage import LocalObjectStorageProvider


def test_iter_bytes_respects_offset_and_length(tmp_path: Path) -> None:
    storage = LocalObjectStorageProvider(tmp_path / "objects")
    object_key = "ws/chunk.bin"
    target = storage._resolve(object_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"0123456789abcdef")

    chunks = list(storage.iter_bytes(object_key, offset=4, length=5, chunk_size=2))
    assert b"".join(chunks) == b"45678"
    assert all(len(chunk) <= 2 for chunk in chunks)


def test_object_key_cannot_escape_storage_root(tmp_path: Path) -> None:
    storage = LocalObjectStorageProvider(tmp_path / "objects")
    try:
        storage._resolve("../outside.bin")
        raised = False
    except Exception as exc:  # AppError
        raised = True
        assert "escapes" in str(exc).lower() or getattr(exc, "code", "") == "invalid_object_key"
    assert raised
