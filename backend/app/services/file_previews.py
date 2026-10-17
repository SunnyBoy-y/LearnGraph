"""Workspace-scoped, bounded cache of offline Office PDF previews."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.file_lock import InterProcessFileLock
from app.domain.models import FileRecord
from app.providers.remote.sandbox import SandboxBackendError, SandboxBackendUnavailable
from app.services.sandbox import SandboxTaskService

OFFICE_PREVIEW_EXTENSIONS = {".doc", ".xls", ".ppt", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".rtf"}
CACHE_MAX_BYTES = 512 * 1024 * 1024
CACHE_TTL_SECONDS = 24 * 60 * 60


def preview_path(settings: Settings, workspace_id: str, file: FileRecord) -> Path:
    workspace = hashlib.sha256(workspace_id.encode()).hexdigest()
    digest = hashlib.sha256(f"office-pdf-v1:{file.id}:{file.sha256}".encode()).hexdigest()
    return settings.storage_root / ".previews" / workspace / f"{digest}.pdf"


def preview_lock(settings: Settings) -> InterProcessFileLock:
    # Serialise conversions across workers, bounding server RAM and sandbox count.
    return InterProcessFileLock(settings.storage_root / ".previews" / "conversion.lock")


def delete_file_preview(settings: Settings, workspace_id: str, file: FileRecord) -> None:
    with preview_lock(settings):
        preview_path(settings, workspace_id, file).unlink(missing_ok=True)


class FilePreviewService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str, settings: Settings):
        self.db, self.workspace_id, self.actor_id, self.settings = db, workspace_id, actor_id, settings

    def office_pdf(self, file: FileRecord) -> Path:
        if Path(file.original_name).suffix.casefold() not in OFFICE_PREVIEW_EXTENSIONS:
            raise AppError(415, "office_preview_unsupported", "This file has no Office PDF preview")
        if file.size_bytes > self.settings.max_document_parse_bytes:
            raise AppError(413, "office_preview_limit", "This file exceeds the server document conversion limit")
        path = preview_path(self.settings, self.workspace_id, file)
        with preview_lock(self.settings):
            if path.is_file():
                os.utime(path, None)
                return path
            try:
                payload = SandboxTaskService(self.db, self.workspace_id, self.actor_id, self.settings).office_pdf_preview(file)
            except SandboxBackendUnavailable as exc:
                raise AppError(503, "office_preview_unavailable", "Office 预览服务尚未就绪，请更新并启动文档转换沙箱。") from exc
            except SandboxBackendError as exc:
                raise AppError(422, "office_preview_failed", "Office 转换失败；文件可能损坏、加密，或转换沙箱需要更新。") from exc
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(payload)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            self._prune(path)
        return path

    def _prune(self, current: Path) -> None:
        candidates = sorted(current.parent.glob("*.pdf"), key=lambda path: path.stat().st_mtime)
        total = sum(path.stat().st_size for path in candidates)
        for path in candidates:
            if path == current:
                continue
            stat = path.stat()
            if total > CACHE_MAX_BYTES or time.time() - stat.st_mtime > CACHE_TTL_SECONDS:
                try:
                    path.unlink(missing_ok=True)
                    total -= stat.st_size
                except OSError:
                    pass  # A Windows reader may still hold the file open.
