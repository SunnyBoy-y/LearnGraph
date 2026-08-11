from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import hashlib
import json
import logging
import time
from typing import Any

from fastapi import UploadFile

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import (
    Evidence,
    AudioTranscription,
    FileRecord,
    FileReference,
    FileTextChunk,
    Goal,
    MemoryEvidence,
    MemoryProfileSnapshot,
    MemoryRecord,
    Message,
    MessagePartRecord,
    MessageVersion,
    SourceRecord,
    utc_now,
)
from app.domain.schemas.files import (
    AudioTranscriptionAsyncCreate,
    AudioTranscriptionCreate,
    FileBatchDeleteImpact,
    FileBatchDeleteResponse,
    FileReferenceCreate,
)
from app.domain.schemas.workflow import DeleteImpact, ImpactItem
from app.providers.storage_factory import object_storage_provider
from app.providers.factory import transcription_provider_for_workspace
from app.providers.ports.transcription import TranscriptionResult
from app.providers.remote.transcription import TranscriptionProviderError
from app.repositories.audit import AuditRepository
from app.repositories.domain import (
    FileReferenceRepository,
    FileRepository,
    FileTextChunkRepository,
)
from app.services.document_parsers import (
    BUILT_IN_DOCUMENT_EXTENSIONS as _BUILT_IN_DOCUMENT_EXTENSIONS,
    ISOLATED_DOCUMENT_EXTENSIONS as _ISOLATED_DOCUMENT_EXTENSIONS,
    LOCAL_TEXT_EXTENSIONS as _LOCAL_TEXT_EXTENSIONS,
    parser_capabilities,
)
from app.services.file_references import FileReferenceService
from app.services.billing import BillingService
from app.services.dictation import is_realtime_transcription_model


logger = logging.getLogger(__name__)


from app.services.chat_attachment_policy import (
    AUDIO_EXTENSIONS as _AUDIO_EXTENSIONS,
    OPTIONAL_IMAGE_EXTENSIONS as _OPTIONAL_IMAGE_EXTENSIONS,
)

LOCAL_TEXT_EXTENSIONS = set(_LOCAL_TEXT_EXTENSIONS)
RECOGNIZED_PROCESSOR_EXTENSIONS = {
    ".ppt",
    *LOCAL_TEXT_EXTENSIONS,
    *_BUILT_IN_DOCUMENT_EXTENSIONS,
    *_ISOLATED_DOCUMENT_EXTENSIONS,
    *_OPTIONAL_IMAGE_EXTENSIONS,
}
BUILT_IN_DOCUMENT_EXTENSIONS = set(_BUILT_IN_DOCUMENT_EXTENSIONS)
ISOLATED_DOCUMENT_EXTENSIONS = set(_ISOLATED_DOCUMENT_EXTENSIONS)
OPTIONAL_PROCESSOR_EXTENSIONS = set(_OPTIONAL_IMAGE_EXTENSIONS)
AUDIO_EXTENSIONS = set(_AUDIO_EXTENSIONS)


class FileService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str, settings: Settings) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.files = FileRepository(db, workspace_id)
        self.chunks = FileTextChunkRepository(db, workspace_id)
        self.references = FileReferenceRepository(db, workspace_id)
        self.reference_service = FileReferenceService(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)
        self.storage = object_storage_provider(db, workspace_id, settings)

    def list(self) -> list[FileRecord]:
        """List all workspace files, newest first.

        The materials library paginates on the client; the repository default
        limit of 100 would silently hide later uploads once a workspace grows.
        """

        return list(
            self.db.scalars(
                self.files.query().order_by(
                    FileRecord.created_at.desc(),
                    FileRecord.id.desc(),
                )
            ).all()
        )

    def search(
        self,
        *,
        q: str | None = None,
        limit: int = 20,
    ) -> list[FileRecord]:
        """Fuzzy match files by original_name for @ mention pickers.

        Matching is case-insensitive substring on the display name only. Limit
        is capped so chat composer's typeahead cannot pull the whole library.
        """

        query = (q or "").strip()
        capped = max(1, min(int(limit or 20), 50))
        statement = self.files.query().order_by(
            FileRecord.created_at.desc(),
            FileRecord.id.desc(),
        )
        if query:
            # SQLite LIKE is case-insensitive for ASCII; lower both sides for
            # broader client locales without pulling every row into Python.
            statement = statement.where(
                func.lower(FileRecord.original_name).like(f"%{query.casefold()}%")
            )
        return list(self.db.scalars(statement.limit(capped)).all())

    def lookup_by_name_and_hash(
        self,
        *,
        original_name: str,
        sha256: str,
    ) -> FileRecord | None:
        """Exact name + content hash match for upload reuse without re-store."""

        name = Path(original_name or "").name[:255]
        digest = (sha256 or "").strip().casefold()
        if not name or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise AppError(
                422,
                "invalid_file_lookup",
                "lookup requires original_name and a 64-char lowercase hex sha256",
            )
        return self.db.scalar(
            select(FileRecord).where(
                FileRecord.workspace_id == self.workspace_id,
                FileRecord.original_name == name,
                FileRecord.sha256 == digest,
            )
        )

    def total_storage_bytes(self) -> int:
        """Sum of logical FileRecord sizes in this workspace (UI occupancy)."""

        rows = self.list()
        return int(sum(int(item.size_bytes or 0) for item in rows))

    @staticmethod
    def _unique_original_name(
        db: Session,
        workspace_id: str,
        original_name: str,
        sha256: str,
    ) -> str | None:
        """Return None when an identical name+hash already exists (caller reuses it).

        Otherwise return a collision-free display name:
        - same name + different hash → stem (1).ext, stem (2).ext, …
        - different name → keep as-is even when hash matches another file
        """

        name = Path(original_name).name[:255] or "upload.bin"
        exact = db.scalar(
            select(FileRecord).where(
                FileRecord.workspace_id == workspace_id,
                FileRecord.original_name == name,
                FileRecord.sha256 == sha256,
            )
        )
        if exact is not None:
            return None
        clash = db.scalar(
            select(FileRecord).where(
                FileRecord.workspace_id == workspace_id,
                FileRecord.original_name == name,
            )
        )
        if clash is None:
            return name
        path = Path(name)
        stem = path.stem or "file"
        suffix = path.suffix
        for index in range(1, 10_000):
            candidate = f"{stem} ({index}){suffix}"
            if len(candidate) > 255:
                budget = max(1, 240 - len(suffix) - len(str(index)) - 3)
                candidate = f"{stem[:budget]} ({index}){suffix}"
            taken = db.scalar(
                select(FileRecord).where(
                    FileRecord.workspace_id == workspace_id,
                    FileRecord.original_name == candidate,
                )
            )
            if taken is None:
                return candidate
            if taken.sha256 == sha256:
                # Same content already stored under the numbered name.
                return None
        return f"{stem}-{sha256[:8]}{suffix}"[:255]

    def list_chunks(self, file_id: str) -> list[FileTextChunk]:
        self.files.require(file_id, "file")
        return list(
            self.db.scalars(
                self.chunks.query()
                .where(
                    FileTextChunk.file_id == file_id,
                    FileTextChunk.lifecycle_status == "active",
                )
                .order_by(FileTextChunk.ordinal)
            ).all()
        )

    def content_record(self, file_id: str) -> FileRecord:
        return self.files.require(file_id, "file")

    def content(self, file_id: str) -> tuple[FileRecord, bytes]:
        record = self.files.require(file_id, "file")
        return record, self.storage.read_bytes(
            record.object_key,
            limit_bytes=self.settings.max_upload_bytes,
        )

    def capabilities(self):
        from app.services.sandbox_bootstrap import backend_for_settings

        sandbox = backend_for_settings(self.settings).probe()
        legacy_doc_available = (
            sandbox.available and "legacy_doc_extract" in sandbox.capabilities
        )
        return parser_capabilities(
            legacy_doc_available=legacy_doc_available,
            legacy_doc_reason=sandbox.reason,
        )

    def list_references(self, file_id: str) -> list[FileReference]:
        return self.reference_service.list_for_file(file_id)

    def list_transcriptions(self, file_id: str) -> list[AudioTranscription]:
        self.files.require(file_id, "file")
        return list(
            self.db.scalars(
                select(AudioTranscription)
                .where(
                    AudioTranscription.workspace_id == self.workspace_id,
                    AudioTranscription.file_id == file_id,
                )
                .order_by(AudioTranscription.created_at.desc())
            ).all()
        )

    def _persist_transcription_failure(
        self,
        *,
        transcription_id: str,
        file_id: str,
        provider_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        self.db.rollback()
        try:
            transcription = self.db.scalar(
                select(AudioTranscription).where(
                    AudioTranscription.workspace_id == self.workspace_id,
                    AudioTranscription.id == transcription_id,
                )
            )
            if transcription is None or transcription.status == "completed":
                return
            transcription.status = "failed"
            transcription.error_code = error_code
            transcription.error_message = error_message[:4_000]
            transcription.completed_at = utc_now()
            self.audit.record(
                actor_id=self.actor_id,
                action="file.transcription.failed",
                resource_type="audio_transcription",
                resource_id=transcription_id,
                outcome="failed",
                details={
                    "file_id": file_id,
                    "provider_id": provider_id,
                    "error_code": error_code,
                },
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Failed to persist terminal state for audio transcription %s",
                transcription_id,
            )

    def transcribe(
        self,
        file_id: str,
        payload: AudioTranscriptionCreate,
        idempotency_key: str,
    ) -> AudioTranscription:
        record = self.files.require(file_id, "file")
        extension = Path(record.original_name).suffix.casefold()
        if not record.mime_type.casefold().startswith("audio/") and extension not in AUDIO_EXTENSIONS:
            raise AppError(415, "audio_required", "Only stored audio files can be transcribed")
        key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        request_payload = {
            "file_id": file_id,
            "file_sha256": record.sha256,
            **payload.model_dump(),
        }
        request_hash = hashlib.sha256(
            json.dumps(request_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        existing = self.db.scalar(
            select(AudioTranscription).where(
                AudioTranscription.workspace_id == self.workspace_id,
                AudioTranscription.idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError(409, "idempotency_key_reused", "Idempotency-Key was reused for a different transcription")
            return existing
        provider = transcription_provider_for_workspace(
            self.db,
            self.workspace_id,
            self.settings,
            provider_id=payload.provider_id,
            model_id=payload.model_id,
            purpose="stored",
        )
        if provider is None:
            raise AppError(503, "transcription_provider_unavailable", "No enabled remote ASR Provider matches this request")
        if is_realtime_transcription_model(provider.model_id):
            raise AppError(
                409,
                "stored_transcription_model_required",
                "The configured ASR model is realtime-only. Stored audio transcription requires a non-realtime model (DashScope qwen3-asr-flash 走 input_audio 通道；paraformer-v2 走录音文件识别)。",
                {
                    "provider_id": provider.provider_id,
                    "model_id": provider.model_id,
                    "configured_transport": "realtime_websocket",
                    "required_transport": "http_transcription",
                },
            )
        billing = BillingService(self.db, self.workspace_id, self.actor_id)
        quote = billing.preflight_model_call(
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            feature="audio_transcription",
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            remote_capability=True,
        )
        transcription = AudioTranscription(
            workspace_id=self.workspace_id,
            file_id=file_id,
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            language=payload.language,
            status="running",
            idempotency_key_hash=key_hash,
            request_hash=request_hash,
            created_by=self.actor_id,
        )
        try:
            self.db.add(transcription)
            self.db.flush()
            transcription_id = transcription.id
            self.audit.record(
                actor_id=self.actor_id,
                action="file.transcription.started",
                resource_type="audio_transcription",
                resource_id=transcription_id,
                details={"file_id": file_id, "provider_id": provider.provider_id, "model_id": provider.model_id},
            )
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            logger.exception("Failed to persist the start of an audio transcription")
            raise AppError(
                500,
                "transcription_start_failed",
                "The audio transcription could not be started",
            ) from exc

        started = time.monotonic()
        try:
            audio = self.storage.read_bytes(
                record.object_key,
                limit_bytes=self.settings.max_upload_bytes,
            )
            result = provider.transcribe(
                filename=record.original_name,
                mime_type=record.mime_type,
                content=audio,
                language=payload.language,
            )
            transcription.status = "completed"
            transcription.transcript = result.text
            transcription.language = result.language or payload.language
            transcription.duration_seconds = result.duration_seconds
            transcription.provider_request_id = result.request_id
            latency_ms = int((time.monotonic() - started) * 1000)
            transcription.provider_trace = {
                "provider_id": provider.provider_id,
                "model_id": provider.model_id,
                "remote_capability": True,
                "request_id": result.request_id,
                "latency_ms": latency_ms,
                "usage": result.usage,
            }
            usage = billing.record_usage(
                quote,
                input_tokens=int(result.usage.get("input_tokens") or 0),
                output_tokens=int(result.usage.get("output_tokens") or 0),
                attempt=1,
                latency_ms=latency_ms,
                usage_reported=bool(result.usage),
            )
            self.db.flush()
            transcription.provider_trace = {
                **transcription.provider_trace,
                "usage_event_id": usage.id,
            }
            transcription.completed_at = utc_now()
            self.audit.record(
                actor_id=self.actor_id,
                action="file.transcription.completed",
                resource_type="audio_transcription",
                resource_id=transcription_id,
                details={"file_id": file_id, "provider_id": provider.provider_id, "request_id": result.request_id},
            )
            self.db.commit()
            self.db.refresh(transcription)
            return transcription
        except TranscriptionProviderError as exc:
            self._persist_transcription_failure(
                transcription_id=transcription_id,
                file_id=file_id,
                provider_id=provider.provider_id,
                error_code="transcription_provider_failed",
                error_message=str(exc),
            )
            raise AppError(
                502,
                "transcription_provider_failed",
                str(exc),
                {"transcription_id": transcription_id},
            ) from exc
        except AppError as exc:
            self._persist_transcription_failure(
                transcription_id=transcription_id,
                file_id=file_id,
                provider_id=provider.provider_id,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise AppError(
                exc.status_code,
                exc.code,
                exc.message,
                {**exc.details, "transcription_id": transcription_id},
            ) from exc
        except Exception as exc:
            logger.exception("Audio transcription %s failed", transcription_id)
            self._persist_transcription_failure(
                transcription_id=transcription_id,
                file_id=file_id,
                provider_id=provider.provider_id,
                error_code="transcription_failed",
                error_message="The audio transcription failed unexpectedly",
            )
            raise AppError(
                500,
                "transcription_failed",
                "The audio transcription failed unexpectedly",
                {"transcription_id": transcription_id},
            ) from exc

    def transcribe_file_async(
        self,
        file_id: str,
        payload: AudioTranscriptionAsyncCreate,
        idempotency_key: str,
        *,
        max_wait_seconds: float = 60.0,
    ) -> AudioTranscription:
        """DashScope 录音文件识别：提交公网音频 URL，轮询至完成或超时。

        音频字节必须可通过 ``source_url`` 被 DashScope 访问（OSS/分享链接）。
        超时后记录保持 ``processing`` 并携带 provider task id，
        由 ``poll_file_transcription`` 续查。
        """
        record = self.files.require(file_id, "file")
        extension = Path(record.original_name).suffix.casefold()
        if not record.mime_type.casefold().startswith("audio/") and extension not in AUDIO_EXTENSIONS:
            raise AppError(415, "audio_required", "Only stored audio files can be transcribed")
        source_url = (payload.source_url or "").strip()
        if not source_url.startswith(("http://", "https://")):
            raise AppError(422, "public_audio_url_required", "Async recording transcription requires a public http(s) audio URL")
        key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        request_payload = {
            "file_id": file_id,
            "file_sha256": record.sha256,
            "source_url": source_url,
            **payload.model_dump(exclude={"source_url"}),
        }
        request_hash = hashlib.sha256(
            json.dumps(request_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        existing = self.db.scalar(
            select(AudioTranscription).where(
                AudioTranscription.workspace_id == self.workspace_id,
                AudioTranscription.idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError(409, "idempotency_key_reused", "Idempotency-Key was reused for a different transcription")
            return existing
        provider = transcription_provider_for_workspace(
            self.db,
            self.workspace_id,
            self.settings,
            provider_id=payload.provider_id,
            model_id=payload.model_id,
            purpose="stored_async",
        )
        if provider is None:
            raise AppError(503, "transcription_provider_unavailable", "No enabled DashScope async ASR Provider matches this request")
        if not getattr(provider, "supports_async", False):
            raise AppError(422, "async_transcription_unsupported", "This Provider/model does not support async recording transcription (use paraformer-v2 or sensevoice-v1)")
        billing = BillingService(self.db, self.workspace_id, self.actor_id)
        quote = billing.preflight_model_call(
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            feature="audio_transcription",
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            remote_capability=True,
        )
        transcription = AudioTranscription(
            workspace_id=self.workspace_id,
            file_id=file_id,
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            language=payload.language,
            status="processing",
            idempotency_key_hash=key_hash,
            request_hash=request_hash,
            created_by=self.actor_id,
        )
        try:
            self.db.add(transcription)
            self.db.flush()
            transcription_id = transcription.id
            self.audit.record(
                actor_id=self.actor_id,
                action="file.transcription.async_submitted",
                resource_type="audio_transcription",
                resource_id=transcription_id,
                details={"file_id": file_id, "provider_id": provider.provider_id, "model_id": provider.model_id},
            )
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            logger.exception("Failed to persist the start of an async audio transcription")
            raise AppError(500, "transcription_start_failed", "The async audio transcription could not be started") from exc

        started = time.monotonic()
        try:
            task_id = provider.submit(source_url=source_url, language=payload.language)
            transcription.provider_request_id = task_id
            transcription.provider_trace = {
                "provider_id": provider.provider_id,
                "model_id": provider.model_id,
                "async_mode": True,
                "task_id": task_id,
                "source_url": source_url,
            }
            self.db.commit()
        except TranscriptionProviderError as exc:
            self._persist_transcription_failure(
                transcription_id=transcription.id,
                file_id=file_id,
                provider_id=provider.provider_id,
                error_code="transcription_submit_failed",
                error_message=str(exc),
            )
            raise AppError(502, "transcription_submit_failed", str(exc), {"transcription_id": transcription.id}) from exc
        try:
            result = provider.wait_for_result(task_id, max_wait_seconds=max_wait_seconds)
        except TranscriptionProviderError as exc:
            if f"task_id={task_id}" in str(exc):
                # 仍在运行：保留 processing 状态供轮询续查。
                self.db.refresh(transcription)
                return transcription
            self._persist_transcription_failure(
                transcription_id=transcription.id,
                file_id=file_id,
                provider_id=provider.provider_id,
                error_code="transcription_provider_failed",
                error_message=str(exc),
            )
            raise AppError(502, "transcription_provider_failed", str(exc), {"transcription_id": transcription.id}) from exc
        self._finalize_async_transcription(transcription, provider, billing, quote, result, started, task_id)
        self.db.refresh(transcription)
        return transcription

    def poll_file_transcription(
        self,
        transcription_id: str,
        *,
        max_wait_seconds: float = 120.0,
    ) -> AudioTranscription:
        """轮询续查一条进行中的异步录音识别任务，直至完成/失败或超时。"""
        transcription = self.db.scalar(
            select(AudioTranscription).where(
                AudioTranscription.id == transcription_id,
                AudioTranscription.workspace_id == self.workspace_id,
            )
        )
        if transcription is None:
            raise AppError(404, "transcription_not_found", "The transcription was not found")
        if transcription.status != "processing" or not transcription.provider_request_id:
            return transcription
        provider = transcription_provider_for_workspace(
            self.db,
            self.workspace_id,
            self.settings,
            provider_id=transcription.provider_id,
            model_id=transcription.model_id,
            purpose="stored_async",
        )
        if provider is None or not getattr(provider, "supports_async", False):
            raise AppError(503, "transcription_provider_unavailable", "The async ASR Provider is no longer available")
        task_id = transcription.provider_request_id
        try:
            result = provider.wait_for_result(task_id, max_wait_seconds=max_wait_seconds)
        except TranscriptionProviderError as exc:
            if f"task_id={task_id}" in str(exc):
                return transcription
            self._persist_transcription_failure(
                transcription_id=transcription.id,
                file_id=transcription.file_id,
                provider_id=provider.provider_id,
                error_code="transcription_provider_failed",
                error_message=str(exc),
            )
            raise AppError(502, "transcription_provider_failed", str(exc), {"transcription_id": transcription.id}) from exc
        billing = BillingService(self.db, self.workspace_id, self.actor_id)
        quote = billing.preflight_model_call(
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            feature="audio_transcription",
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            remote_capability=True,
        )
        started = time.monotonic()
        self._finalize_async_transcription(transcription, provider, billing, quote, result, started, task_id)
        self.db.refresh(transcription)
        return transcription

    def _finalize_async_transcription(
        self,
        transcription: AudioTranscription,
        provider: object,
        billing: BillingService,
        quote: object,
        result: TranscriptionResult,
        started: float,
        task_id: str,
    ) -> None:
        transcription.status = "completed"
        transcription.transcript = result.text
        transcription.duration_seconds = result.duration_seconds
        transcription.provider_request_id = result.request_id or task_id
        latency_ms = int((time.monotonic() - started) * 1000)
        transcription.provider_trace = {
            **transcription.provider_trace,
            "async_mode": True,
            "task_id": task_id,
            "latency_ms": latency_ms,
            "usage": result.usage,
        }
        usage = billing.record_usage(
            quote,
            input_tokens=int(result.usage.get("input_tokens") or 0),
            output_tokens=int(result.usage.get("output_tokens") or 0),
            attempt=1,
            latency_ms=latency_ms,
            usage_reported=bool(result.usage),
        )
        self.db.flush()
        transcription.provider_trace = {
            **transcription.provider_trace,
            "usage_event_id": usage.id,
        }
        transcription.completed_at = utc_now()
        self.audit.record(
            actor_id=self.actor_id,
            action="file.transcription.async_completed",
            resource_type="audio_transcription",
            resource_id=transcription.id,
            details={"file_id": transcription.file_id, "provider_id": provider.provider_id, "task_id": task_id},
        )
        self.db.commit()

    def add_reference(self, file_id: str, payload: FileReferenceCreate) -> FileReference:
        reference = self.reference_service.add(file_id, payload)
        self.audit.record(
            actor_id=self.actor_id,
            action="file.reference_created",
            resource_type="file",
            resource_id=file_id,
            details={
                "reference_id": reference.id,
                "target_type": reference.target_type,
                "target_id": reference.target_id,
                "relation": reference.relation,
                "locator": reference.locator,
            },
        )
        self.db.commit()
        self.db.refresh(reference)
        return reference

    async def upload(self, upload: UploadFile) -> FileRecord:
        original_name = upload.filename or "upload.bin"

        # Workspace storage quota (aggregate, not per-file): reject before
        # streaming when the workspace is already at/over budget. The quota
        # check is best-effort (a concurrent writer can race past it); the
        # per-file ceiling remains the hard cap enforced by storage.store.
        quota_bytes = self.settings.workspace_storage_quota_bytes
        if quota_bytes and quota_bytes > 0:
            used = self.db.scalar(
                select(func.coalesce(func.sum(FileRecord.size_bytes), 0)).where(
                    FileRecord.workspace_id == self.workspace_id,
                    FileRecord.lifecycle_status != "deleted",
                )
            ) or 0
            if used >= quota_bytes:
                raise AppError(
                    413,
                    "workspace_storage_quota_exceeded",
                    "This workspace has reached its storage quota; delete files or raise the quota",
                )

        async def chunks() -> AsyncIterator[bytes]:
            while chunk := await upload.read(1024 * 1024):
                yield chunk

        stored = await self.storage.store(
            self.workspace_id,
            original_name,
            chunks(),
            self.settings.max_upload_bytes,
        )
        # Dedup: identical name + hash → return the first copy only.
        existing_same = self.db.scalar(
            select(FileRecord).where(
                FileRecord.workspace_id == self.workspace_id,
                FileRecord.sha256 == stored.sha256,
                FileRecord.original_name == Path(original_name).name[:255],
            )
        )
        if existing_same is not None:
            self.audit.record(
                actor_id=self.actor_id,
                action="file.upload.deduped",
                resource_type="file",
                resource_id=existing_same.id,
                details={
                    "size_bytes": existing_same.size_bytes,
                    "sha256": existing_same.sha256,
                    "reason": "same_name_and_hash",
                },
            )
            self.db.commit()
            self.db.refresh(existing_same)
            return existing_same

        resolved_name = self._unique_original_name(
            self.db,
            self.workspace_id,
            original_name,
            stored.sha256,
        )
        if resolved_name is None:
            # Race: another writer just inserted the same name+hash.
            raced = self.db.scalar(
                select(FileRecord).where(
                    FileRecord.workspace_id == self.workspace_id,
                    FileRecord.sha256 == stored.sha256,
                    FileRecord.original_name == Path(original_name).name[:255],
                )
            )
            if raced is not None:
                self.db.commit()
                return raced
            resolved_name = Path(original_name).name[:255]

        extension = Path(resolved_name).suffix.casefold()
        if extension in LOCAL_TEXT_EXTENSIONS:
            capability = "local_text"
        elif extension in BUILT_IN_DOCUMENT_EXTENSIONS:
            capability = "built_in_document"
        elif extension in ISOLATED_DOCUMENT_EXTENSIONS:
            capability = "isolated_converter_required"
        elif extension in OPTIONAL_PROCESSOR_EXTENSIONS:
            capability = "optional_processor"
        elif extension in RECOGNIZED_PROCESSOR_EXTENSIONS:
            capability = "isolated_converter_required"
        else:
            capability = "attachment_only"
        record = self.files.add(
            FileRecord(
                workspace_id=self.workspace_id,
                original_name=resolved_name,
                object_key=stored.object_key,
                mime_type=upload.content_type or "application/octet-stream",
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                storage_status="stored",
                parse_capability=capability,
                parse_status="not_requested",
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="file.upload",
            resource_type="file",
            resource_id=record.id,
            details={
                "size_bytes": record.size_bytes,
                "parse_capability": capability,
                "sha256": record.sha256,
                "original_name": record.original_name,
            },
        )
        self.db.commit()
        self.db.refresh(record)
        return record

    def parse(self, file_id: str) -> FileRecord:
        # Keep the compatibility endpoint, but route it through the same
        # versioned, durable parse/index implementation as document jobs.
        from app.domain.schemas.files import DocumentJobCreate
        from app.services.document_learning import DocumentLearningService

        document_service = DocumentLearningService(
            self.db,
            self.workspace_id,
            self.actor_id,
            self.settings,
        )
        job, _ = document_service.create_job(file_id, DocumentJobCreate(), None)
        document_service.execute_job(job.id, raise_errors=True)
        record = self.files.require(file_id, "file")
        self.audit.record(
            actor_id=self.actor_id,
            action="file.parse",
            resource_type="file",
            resource_id=record.id,
            details={"document_job_id": job.id, "document_revision_id": job.document_revision_id},
        )
        self.db.commit()
        self.db.refresh(record)
        return record

    @staticmethod
    def _contains_file_id(value: Any, file_id: str) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "file_id" and item == file_id:
                    return True
                if key == "file_ids" and isinstance(item, list) and file_id in item:
                    return True
                if FileService._contains_file_id(item, file_id):
                    return True
        elif isinstance(value, list):
            return any(FileService._contains_file_id(item, file_id) for item in value)
        return False

    @staticmethod
    def _detach_top_level_file_id(
        value: dict[str, Any],
        file_id: str,
        snapshot: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        def strip(item: Any) -> tuple[Any, bool]:
            if isinstance(item, dict):
                cleaned: dict[str, Any] = {}
                item_changed = False
                for key, nested in item.items():
                    if key == "file_id" and nested == file_id:
                        item_changed = True
                        continue
                    if key == "file_ids" and isinstance(nested, list) and file_id in nested:
                        cleaned[key] = [entry for entry in nested if entry != file_id]
                        item_changed = True
                        continue
                    cleaned_nested, nested_changed = strip(nested)
                    cleaned[key] = cleaned_nested
                    item_changed = item_changed or nested_changed
                return cleaned, item_changed
            if isinstance(item, list):
                cleaned_items: list[Any] = []
                item_changed = False
                for nested in item:
                    cleaned_nested, nested_changed = strip(nested)
                    cleaned_items.append(cleaned_nested)
                    item_changed = item_changed or nested_changed
                return cleaned_items, item_changed
            return item, False

        updated, changed = strip(dict(value or {}))
        if changed:
            deleted_refs = list(updated.get("deleted_file_refs") or [])
            if not any(item.get("file_id") == file_id for item in deleted_refs if isinstance(item, dict)):
                deleted_refs.append(snapshot)
            updated["deleted_file_refs"] = deleted_refs
        return updated, changed

    def _reference_targets(self, file_id: str) -> tuple[dict[str, set[str]], list[FileReference]]:
        targets: dict[str, set[str]] = {}

        def add(target_type: str, target_id: str) -> None:
            targets.setdefault(target_type, set()).add(target_id)

        explicit = list(
            self.db.scalars(
                self.references.query().where(FileReference.file_id == file_id)
            ).all()
        )
        for reference in explicit:
            add(reference.target_type, reference.target_id)

        for goal in self.db.scalars(
            select(Goal).where(Goal.workspace_id == self.workspace_id)
        ):
            if self._contains_file_id(goal.constraints or {}, file_id):
                add("goal", goal.id)

        part_rows = self.db.execute(
            select(MessagePartRecord, MessageVersion.message_id)
            .join(MessageVersion, MessageVersion.id == MessagePartRecord.message_version_id)
            .where(
                MessagePartRecord.workspace_id == self.workspace_id,
                MessageVersion.workspace_id == self.workspace_id,
            )
        ).all()
        for part, message_id in part_rows:
            if self._contains_file_id(part.data or {}, file_id):
                add("message", message_id)
        for message in self.db.scalars(
            select(Message).where(Message.workspace_id == self.workspace_id)
        ):
            if self._contains_file_id(message.parts or [], file_id):
                add("message", message.id)

        for evidence in self.db.scalars(
            select(Evidence).where(Evidence.workspace_id == self.workspace_id)
        ):
            if self._contains_file_id(evidence.metadata_json or {}, file_id):
                add("evidence", evidence.id)
        for source in self.db.scalars(
            select(SourceRecord).where(SourceRecord.workspace_id == self.workspace_id)
        ):
            if self._contains_file_id(source.metadata_json or {}, file_id):
                add("source", source.id)
        return targets, explicit

    def delete_impact(self, file_id: str) -> DeleteImpact:
        record = self.files.require(file_id, "file")
        targets, explicit = self._reference_targets(file_id)
        chunk_count = len(
            list(
                self.db.scalars(
                    self.chunks.query().where(FileTextChunk.file_id == record.id)
                ).all()
            )
        )
        impacts = [
            ImpactItem(resource_type="file_text_chunk", count=chunk_count, action="delete"),
            ImpactItem(resource_type="file_reference", count=len(explicit), action="delete"),
        ]
        for target_type in (
            "project",
            "goal",
            "graph",
            "node",
            "session",
            "message",
            "evidence",
            "source",
            "source_link",
        ):
            action = "preserve_history" if target_type in {"message", "evidence"} else "detach"
            impacts.append(
                ImpactItem(
                    resource_type=target_type,
                    count=len(targets.get(target_type, set())),
                    action=action,
                )
            )
        return DeleteImpact(
            resource_type="file",
            resource_id=record.id,
            title=record.original_name,
            impacts=impacts,
            confirmation_text=record.original_name,
        )

    @staticmethod
    def _has_domain_references(impact: DeleteImpact) -> bool:
        ignored = {"file_text_chunk", "file_reference"}
        return any(item.count for item in impact.impacts if item.resource_type not in ignored)

    def _detach_json_references(self, record: FileRecord) -> None:
        snapshot = {
            "file_id": record.id,
            "original_name": record.original_name,
            "sha256": record.sha256,
            "deleted_at": utc_now().isoformat(),
        }
        for goal in self.db.scalars(
            select(Goal).where(Goal.workspace_id == self.workspace_id)
        ):
            updated, changed = self._detach_top_level_file_id(
                dict(goal.constraints or {}), record.id, snapshot
            )
            if changed:
                goal.constraints = updated
        for part in self.db.scalars(
            select(MessagePartRecord).where(
                MessagePartRecord.workspace_id == self.workspace_id
            )
        ):
            updated, changed = self._detach_top_level_file_id(
                dict(part.data or {}), record.id, snapshot
            )
            if changed:
                part.data = updated
        for message in self.db.scalars(
            select(Message).where(Message.workspace_id == self.workspace_id)
        ):
            parts: list[dict[str, Any]] = []
            changed = False
            for part in message.parts or []:
                part_snapshot = dict(part)
                data, data_changed = self._detach_top_level_file_id(
                    dict(part_snapshot.get("data") or {}), record.id, snapshot
                )
                if data_changed:
                    part_snapshot["data"] = data
                    changed = True
                parts.append(part_snapshot)
            if changed:
                message.parts = parts
        for evidence in self.db.scalars(
            select(Evidence).where(Evidence.workspace_id == self.workspace_id)
        ):
            updated, changed = self._detach_top_level_file_id(
                dict(evidence.metadata_json or {}), record.id, snapshot
            )
            if changed:
                evidence.metadata_json = updated
        for source in self.db.scalars(
            select(SourceRecord).where(SourceRecord.workspace_id == self.workspace_id)
        ):
            updated, changed = self._detach_top_level_file_id(
                dict(source.metadata_json or {}), record.id, snapshot
            )
            if changed:
                source.metadata_json = updated

    def _delete(self, record: FileRecord, impact: DeleteImpact) -> None:
        self._delete_without_commit(record, impact)
        self.db.commit()

    def _delete_without_commit(self, record: FileRecord, impact: DeleteImpact) -> None:
        """Same cleanup as `_delete` but leaves the final commit to the caller."""

        self._detach_json_references(record)
        now = utc_now()
        file_evidence = list(
            self.db.scalars(
                select(MemoryEvidence).where(
                    MemoryEvidence.workspace_id == self.workspace_id,
                    MemoryEvidence.file_id == record.id,
                    MemoryEvidence.deleted_at.is_(None),
                )
            ).all()
        )
        file_evidence_ids = {item.id for item in file_evidence}
        for item in file_evidence:
            item.profile_eligible = False
            item.eligibility_reason = "source_file_deleted"
            item.deleted_at = now
        invalidated_atoms: list[str] = []
        if file_evidence_ids:
            active_evidence_ids = set(
                self.db.scalars(
                    select(MemoryEvidence.id).where(
                        MemoryEvidence.workspace_id == self.workspace_id,
                        MemoryEvidence.profile_eligible.is_(True),
                        MemoryEvidence.deleted_at.is_(None),
                    )
                ).all()
            )
            atoms = list(
                self.db.scalars(
                    select(MemoryRecord).where(
                        MemoryRecord.workspace_id == self.workspace_id,
                        MemoryRecord.state == "active",
                        MemoryRecord.ledger_status == "active",
                    )
                ).all()
            )
            for atom in atoms:
                evidence_ids = set(atom.evidence_ids or [])
                if not evidence_ids.intersection(file_evidence_ids):
                    continue
                if evidence_ids.intersection(active_evidence_ids):
                    # An independent user confirmation survives file deletion.
                    atom.evidence_ids = sorted(
                        evidence_ids.difference(file_evidence_ids)
                    )
                    continue
                atom.ledger_status = "retracted"
                atom.temporal_status = "expired"
                atom.summary_eligibility = "excluded"
                structured = dict(atom.structured_payload or {})
                structured.update(
                    {
                        "ledger_status": "retracted",
                        "temporal_status": "expired",
                        "summary_eligibility": "excluded",
                        "retraction_reason": "sole_source_file_deleted",
                    }
                )
                atom.structured_payload = structured
                invalidated_atoms.append(atom.id)
            if invalidated_atoms:
                for snapshot in self.db.scalars(
                    select(MemoryProfileSnapshot).where(
                        MemoryProfileSnapshot.workspace_id
                        == self.workspace_id,
                        MemoryProfileSnapshot.status == "ready",
                    )
                ).all():
                    snapshot.status = "stale"
                    snapshot.stale_reason = "source_file_deleted"
        references = list(
            self.db.scalars(
                self.references.query().where(FileReference.file_id == record.id)
            ).all()
        )
        reference_snapshots = [
            {
                "reference_id": reference.id,
                "target_type": reference.target_type,
                "target_id": reference.target_id,
                "relation": reference.relation,
                "locator": reference.locator,
            }
            for reference in references
        ]
        for reference in references:
            self.references.delete(reference)
        self.storage.delete(record.object_key)
        self.db.execute(
            text(
                "DELETE FROM document_chunks_fts "
                "WHERE workspace_id = :workspace_id AND file_id = :file_id"
            ),
            {"workspace_id": self.workspace_id, "file_id": record.id},
        )
        for chunk in self.db.scalars(
            self.chunks.query().where(FileTextChunk.file_id == record.id)
        ).all():
            self.chunks.delete(chunk)
        self.audit.record(
            actor_id=self.actor_id,
            action="file.delete",
            resource_type="file",
            resource_id=record.id,
            details={
                "impacts": [item.model_dump() for item in impact.impacts],
                "detached_references": reference_snapshots,
                "memory_evidence_invalidated": sorted(file_evidence_ids),
                "memory_atoms_retracted": invalidated_atoms,
            },
        )
        self.files.delete(record)

    def delete(self, file_id: str) -> DeleteImpact:
        record = self.files.require(file_id, "file")
        impact = self.delete_impact(file_id)
        if self._has_domain_references(impact):
            raise AppError(
                409,
                "file_has_references",
                "Referenced files require impact review and explicit confirmation before deletion",
                {
                    "file_id": file_id,
                    "confirmation_required": True,
                    "impacts": [item.model_dump() for item in impact.impacts],
                },
            )
        self._delete(record, impact)
        return impact

    def delete_confirmed(self, file_id: str, confirmation: str) -> DeleteImpact:
        record = self.files.require(file_id, "file")
        impact = self.delete_impact(file_id)
        if confirmation != impact.confirmation_text:
            raise AppError(
                409,
                "confirmation_mismatch",
                "Confirmation text does not match the file name",
            )
        self._delete(record, impact)
        return impact

    def _files(self, file_ids: list[str]) -> list[FileRecord]:
        items = list(
            self.db.scalars(
                select(FileRecord).where(
                    FileRecord.workspace_id == self.workspace_id,
                    FileRecord.id.in_(file_ids),
                )
            )
        )
        by_id = {item.id: item for item in items}
        if len(by_id) != len(file_ids):
            # Missing and foreign-workspace IDs intentionally share one response so
            # callers cannot use a batch preflight as a cross-workspace oracle.
            raise AppError(
                404,
                "file_not_found",
                "One or more files were not found",
            )
        return [by_id[file_id] for file_id in file_ids]

    def _file_batch_digest(self, file_ids: list[str]) -> str:
        canonical = "\0".join(sorted(file_ids))
        return hashlib.sha256(
            f"{self.workspace_id}\0{canonical}".encode("utf-8")
        ).hexdigest()[:16]

    def _file_batch_confirmation(self, file_ids: list[str]) -> str:
        return f"delete-files:{len(file_ids)}:{self._file_batch_digest(file_ids)}"

    @staticmethod
    def _merge_impacts(impacts: list[ImpactItem]) -> list[ImpactItem]:
        totals: dict[tuple[str, str], int] = {}
        order: list[tuple[str, str]] = []
        for item in impacts:
            key = (item.resource_type, item.action)
            if key not in totals:
                totals[key] = 0
                order.append(key)
            totals[key] += int(item.count or 0)
        return [
            ImpactItem(resource_type=resource_type, count=totals[key], action=action)
            for key in order
            for resource_type, action in [key]
        ]

    def batch_delete_impact(self, file_ids: list[str]) -> FileBatchDeleteImpact:
        records = self._files(file_ids)
        merged = self._merge_impacts(
            [
                impact_item
                for file_id in file_ids
                for impact_item in self.delete_impact(file_id).impacts
            ]
        )
        # Batch preflight always lists the selected files themselves.
        selected_files = ImpactItem(
            resource_type="file",
            count=len(records),
            action="delete",
        )
        impacts = [selected_files, *merged]
        digest = self._file_batch_digest(file_ids)
        return FileBatchDeleteImpact(
            resource_type="file_batch",
            resource_id=f"batch-{digest}",
            title=f"{len(file_ids)} 个文件",
            confirmation_text=self._file_batch_confirmation(file_ids),
            file_ids=file_ids,
            impacts=impacts,
        )

    def delete_batch(
        self,
        file_ids: list[str],
        confirmation: str,
    ) -> FileBatchDeleteResponse:
        impact = self.batch_delete_impact(file_ids)
        if confirmation != impact.confirmation_text:
            raise AppError(
                409,
                "confirmation_mismatch",
                "Confirmation token does not match the selected files",
            )
        # Delete without intermediate commits; each single-file path commits.
        # Re-implement a multi-file path that commits once at the end.
        records = self._files(file_ids)
        for record in records:
            single_impact = self.delete_impact(record.id)
            self._delete_without_commit(record, single_impact)
        self.audit.record(
            actor_id=self.actor_id,
            action="file.batch_delete",
            resource_type="file_batch",
            resource_id=impact.resource_id,
            details={
                "file_ids": file_ids,
                "deleted_count": len(file_ids),
                "impacts": [item.model_dump() for item in impact.impacts],
            },
        )
        self.db.commit()
        return FileBatchDeleteResponse(
            status="deleted",
            deleted_file_ids=file_ids,
            deleted_count=len(file_ids),
            impacts=impact.impacts,
        )

    @staticmethod
    def _chunk_text(content: str, size: int = 4_000) -> list[str]:
        if len(content) <= size:
            return [content]
        chunks: list[str] = []
        remaining = content
        while remaining:
            split_at = remaining.rfind("\n", 0, size)
            if split_at < size // 2:
                split_at = size
            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].lstrip()
        return [chunk for chunk in chunks if chunk]
