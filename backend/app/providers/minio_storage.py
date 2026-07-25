from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from tempfile import SpooledTemporaryFile
from uuid import uuid4

from app.core.errors import AppError
from app.providers.local.storage import safe_filename
from app.providers.ports.storage import StoredObject


class MinioObjectStorageProvider:
    """Real S3-compatible MinIO adapter; construction requires complete config."""

    def __init__(self) -> None:
        endpoint = os.getenv("LEARNGRAPH_MINIO_ENDPOINT", "").strip()
        access_key = os.getenv("LEARNGRAPH_MINIO_ACCESS_KEY", "").strip()
        secret_key = os.getenv("LEARNGRAPH_MINIO_SECRET_KEY", "").strip()
        self.bucket = os.getenv("LEARNGRAPH_MINIO_BUCKET", "").strip()
        if not all((endpoint, access_key, secret_key, self.bucket)):
            raise AppError(503, "minio_unavailable", "MinIO configuration is incomplete")
        try:
            from minio import Minio
        except (ImportError, ModuleNotFoundError) as exc:
            raise AppError(503, "minio_driver_missing", "Install the infrastructure extra to use MinIO") from exc
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=os.getenv("LEARNGRAPH_MINIO_SECURE", "true").casefold() not in {"0", "false", "no"},
        )
        if not self.client.bucket_exists(self.bucket):
            raise AppError(503, "minio_bucket_unavailable", "Configured private MinIO bucket does not exist")

    async def store(
        self,
        workspace_id: str,
        original_name: str,
        chunks: AsyncIterator[bytes],
        max_bytes: int,
    ) -> StoredObject:
        object_key = f"tenants/{safe_filename(workspace_id)}/uploads/{uuid4()}-{safe_filename(original_name)}"
        digest = hashlib.sha256()
        size = 0
        with SpooledTemporaryFile(max_size=8 * 1024 * 1024) as payload:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise AppError(413, "file_too_large", f"File exceeds {max_bytes} bytes")
                digest.update(chunk)
                payload.write(chunk)
            payload.seek(0)
            self.client.put_object(
                self.bucket,
                object_key,
                payload,
                size,
                content_type="application/octet-stream",
                metadata={"sha256": digest.hexdigest(), "workspace-id": workspace_id},
            )
        return StoredObject(object_key=object_key, size_bytes=size, sha256=digest.hexdigest())

    def delete(self, object_key: str) -> None:
        self.client.remove_object(self.bucket, object_key)

    def read_bytes(self, object_key: str, limit_bytes: int = 50 * 1024 * 1024) -> bytes:
        stat = self.client.stat_object(self.bucket, object_key)
        if stat.size > limit_bytes:
            raise AppError(422, "document_parse_limit", "File is too large for the configured document parser")
        response = self.client.get_object(self.bucket, object_key)
        try:
            return response.read(limit_bytes + 1)
        finally:
            response.close()
            response.release_conn()

    def read_text(self, object_key: str, limit_bytes: int = 2 * 1024 * 1024) -> str:
        return self.read_bytes(object_key, limit_bytes).decode("utf-8")

