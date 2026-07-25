from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import FileRecord, ImageGenerationTask, utc_now
from app.domain.schemas.files import FileReferenceCreate
from app.providers.storage_factory import object_storage_provider
from app.repositories.audit import AuditRepository
from app.repositories.domain import FileRepository, ImageGenerationTaskRepository
from app.services.file_references import FileReferenceService


_IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class ImageGenerationService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.tasks = ImageGenerationTaskRepository(db, workspace_id)
        self.files = FileRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)
        self.storage = object_storage_provider(db, workspace_id, settings)

    def get(self, task_id: str) -> ImageGenerationTask:
        return self.tasks.require(task_id, "image generation task")

    def create(
        self,
        *,
        session_id: str,
        message_id: str,
        message_version_id: str,
        source_message_id: str,
        provider_id: str,
        model_id: str,
        prompt: str,
        commit: bool = True,
    ) -> ImageGenerationTask:
        summary = " ".join(prompt.split())[:240]
        task = self.tasks.add(
            ImageGenerationTask(
                workspace_id=self.workspace_id,
                session_id=session_id,
                message_id=message_id,
                message_version_id=message_version_id,
                source_message_id=source_message_id,
                provider_id=provider_id,
                model_id=model_id,
                prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                prompt_summary=summary,
                status="queued",
                progress_mode="indeterminate",
                provider_trace={
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "remote_capability": True,
                },
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="image.generation.queued",
            resource_type="image_generation_task",
            resource_id=task.id,
            details={"message_id": message_id, "provider_id": provider_id},
        )
        if commit:
            self.db.commit()
            self.db.refresh(task)
        else:
            self.db.flush()
        return task

    def mark_running(self, task: ImageGenerationTask) -> None:
        task.status = "running"
        self.db.commit()

    def store_image(
        self,
        task: ImageGenerationTask,
        payload: bytes,
        mime_type: str,
        *,
        partial_index: int | None,
        completed: bool,
        provider_trace: dict | None = None,
    ) -> FileRecord:
        extension = _IMAGE_EXTENSIONS.get(mime_type)
        if extension is None:
            raise AppError(
                502,
                "image_generation_mime_unsupported",
                "The image provider returned an unsupported media type",
                {"mime_type": mime_type},
            )
        if not payload:
            raise AppError(
                502,
                "image_generation_empty_payload",
                "The image provider returned an empty image",
            )

        stored = self._store_bytes(
            f"generated-{task.id}{extension}", payload
        )
        old_object_key: str | None = None
        if task.file_id:
            record = self.files.require(task.file_id, "generated image")
            old_object_key = record.object_key
            record.object_key = stored.object_key
            record.mime_type = mime_type
            record.size_bytes = stored.size_bytes
            record.sha256 = stored.sha256
            record.storage_status = "stored"
        else:
            record = self.files.add(
                FileRecord(
                    workspace_id=self.workspace_id,
                    original_name=f"generated-{task.id}{extension}",
                    object_key=stored.object_key,
                    mime_type=mime_type,
                    size_bytes=stored.size_bytes,
                    sha256=stored.sha256,
                    storage_status="stored",
                    parse_capability="optional_processor",
                    parse_status="not_requested",
                )
            )
            self.db.flush()
            task.file_id = record.id
            FileReferenceService(self.db, self.workspace_id).add(
                record.id,
                FileReferenceCreate(
                    target_type="message",
                    target_id=task.message_id,
                    relation="generated_image",
                    metadata={"generation_id": task.id},
                ),
            )

        task.partial_index = partial_index
        task.progress_mode = (
            "partial_preview" if partial_index is not None else "indeterminate"
        )
        if provider_trace:
            task.provider_trace = {**(task.provider_trace or {}), **provider_trace}
        if completed:
            task.status = "completed"
            task.completed_at = utc_now()
        else:
            task.status = "running"
        self.db.commit()
        self.db.refresh(record)
        if old_object_key and old_object_key != stored.object_key:
            try:
                self.storage.delete(old_object_key)
            except Exception:
                self.audit.record(
                    actor_id=self.actor_id,
                    action="image.preview_cleanup_pending",
                    resource_type="file",
                    resource_id=record.id,
                    outcome="failed",
                )
                self.db.commit()
        return record

    def fail(self, task: ImageGenerationTask, code: str, message: str) -> None:
        task.status = "failed"
        task.error_code = code[:80]
        task.error_message = message[:2_000]
        task.completed_at = utc_now()
        self.audit.record(
            actor_id=self.actor_id,
            action="image.generation.failed",
            resource_type="image_generation_task",
            resource_id=task.id,
            outcome="failed",
            details={"code": task.error_code},
        )
        self.db.commit()

    def cancel(self, task_id: str) -> ImageGenerationTask:
        task = self.get(task_id)
        if task.status in {"completed", "failed", "cancelled"}:
            return task
        task.cancel_requested = True
        task.status = "cancelled"
        task.completed_at = utc_now()
        self.audit.record(
            actor_id=self.actor_id,
            action="image.generation.cancelled",
            resource_type="image_generation_task",
            resource_id=task.id,
        )
        self.db.commit()
        self.db.refresh(task)
        return task

    def _store_bytes(self, original_name: str, payload: bytes):
        async def chunks() -> AsyncIterator[bytes]:
            yield payload

        async def store():
            return await self.storage.store(
                self.workspace_id,
                Path(original_name).name,
                chunks(),
                self.settings.max_upload_bytes,
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(store())
        raise RuntimeError(
            "ImageGenerationService must persist artifacts from a synchronous worker"
        )
