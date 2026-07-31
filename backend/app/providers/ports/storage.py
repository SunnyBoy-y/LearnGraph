from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredObject:
    object_key: str
    size_bytes: int
    sha256: str


class ObjectStoragePort(Protocol):
    async def store(
        self,
        workspace_id: str,
        original_name: str,
        chunks: AsyncIterator[bytes],
        max_bytes: int,
    ) -> StoredObject: ...

    def delete(self, object_key: str) -> None: ...

    def iter_bytes(
        self,
        object_key: str,
        *,
        offset: int = 0,
        length: int | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]: ...

    def read_bytes(self, object_key: str, limit_bytes: int = 50 * 1024 * 1024) -> bytes: ...

    def read_text(self, object_key: str, limit_bytes: int = 2 * 1024 * 1024) -> str: ...
