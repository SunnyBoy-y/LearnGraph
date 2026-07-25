from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from app.core.errors import AppError
from app.providers.ports.storage import StoredObject


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(value: str) -> str:
    name = Path(value).name
    cleaned = _SAFE_NAME.sub("_", name).strip("._")
    return cleaned[:120] or "upload.bin"


class LocalObjectStorageProvider:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, object_key: str) -> Path:
        candidate = (self.root / object_key).resolve()
        if self.root not in candidate.parents:
            raise AppError(400, "invalid_object_key", "Object key escapes storage root")
        return candidate

    async def store(
        self,
        workspace_id: str,
        original_name: str,
        chunks: AsyncIterator[bytes],
        max_bytes: int,
    ) -> StoredObject:
        object_key = f"{safe_filename(workspace_id)}/{uuid4()}-{safe_filename(original_name)}"
        target = self._resolve(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with target.open("xb") as output:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > max_bytes:
                        raise AppError(413, "file_too_large", f"File exceeds {max_bytes} bytes")
                    digest.update(chunk)
                    output.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return StoredObject(object_key=object_key, size_bytes=size, sha256=digest.hexdigest())

    def delete(self, object_key: str) -> None:
        self._resolve(object_key).unlink(missing_ok=True)

    def read_text(self, object_key: str, limit_bytes: int = 2 * 1024 * 1024) -> str:
        path = self._resolve(object_key)
        if path.stat().st_size > limit_bytes:
            raise AppError(422, "text_parse_limit", "Text file is too large for the MVP parser")
        return path.read_text(encoding="utf-8")

    def read_bytes(self, object_key: str, limit_bytes: int = 50 * 1024 * 1024) -> bytes:
        path = self._resolve(object_key)
        if path.stat().st_size > limit_bytes:
            raise AppError(422, "document_parse_limit", "File is too large for the configured document parser")
        return path.read_bytes()
