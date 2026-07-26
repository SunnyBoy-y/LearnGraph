from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import (
    ImageGenerationTask,
    Message,
    MessageControl,
    MessagePartRecord,
    MessageStreamEvent,
    MessageSubmission,
    MessageVersion,
    ProviderAttempt,
    UsageEvent,
)
from app.domain.schemas.chat import MessageCreateRequest, SSEEventEnvelope
from app.providers.ports.image_generation import (
    ImageGenerationProviderPort,
    ImageGenerationRequest,
)
from app.repositories.audit import AuditRepository
from app.repositories.domain import (
    MessagePartRepository,
    MessageRepository,
    MessageStreamEventRepository,
    MessageSubmissionRepository,
    MessageVersionRepository,
    SessionRepository,
)
from app.services.image_generations import ImageGenerationService


_DRAW_COMMAND = re.compile(r"^@绘图(?:\s+|$)")
_TERMINAL = {"completed", "failed", "cancelled"}


class _ImageCancellationRequested(Exception):
    pass


class ImageChatService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
        image_provider: ImageGenerationProviderPort,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.image_provider = image_provider
        self.sessions = SessionRepository(db, workspace_id)
        self.messages = MessageRepository(db, workspace_id)
        self.versions = MessageVersionRepository(db, workspace_id)
        self.parts = MessagePartRepository(db, workspace_id)
        self.events = MessageStreamEventRepository(db, workspace_id)
        self.submissions = MessageSubmissionRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)
        self.images = ImageGenerationService(
            db, workspace_id, actor_id, settings
        )

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _part(
        part_id: str,
        status: str,
        content: str,
        data: dict,
    ) -> dict:
        return {
            "id": part_id,
            "type": "image",
            "status": status,
            "content": content,
            "data": data,
        }

    def _envelope(self, record: MessageStreamEvent) -> SSEEventEnvelope:
        payload = record.payload or {}
        return SSEEventEnvelope(
            event_id=record.id,
            sequence=record.sequence,
            session_id=record.session_id,
            message_id=record.message_id,
            message_version_id=record.message_version_id,
            part_id=record.part_id,
            type=record.event_type,
            created_at=self._utc(record.created_at),
            payload=payload,
            event=record.event_type,
            part=payload.get("part"),
            status=payload.get("status"),
            provider_trace=payload.get("provider_trace"),
        )

    @staticmethod
    def _encode(envelope: SSEEventEnvelope) -> str:
        data = envelope.model_dump(mode="json")
        return (
            f"id: {envelope.event_id}\n"
            f"event: {envelope.event}\n"
            f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        )

    def _append_event(
        self,
        *,
        session_id: str,
        message_id: str,
        version_id: str,
        part_id: str | None,
        sequence: int,
        event_type: str,
        payload: dict,
    ) -> SSEEventEnvelope:
        record = self.events.add(
            MessageStreamEvent(
                workspace_id=self.workspace_id,
                session_id=session_id,
                message_id=message_id,
                message_version_id=version_id,
                part_id=part_id,
                sequence=sequence,
                event_type=event_type,
                payload=payload,
            )
        )
        self.db.commit()
        return self._envelope(record)

    def _request_hash(self, payload: MessageCreateRequest) -> str:
        return self._hash(
            json.dumps(
                payload.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    def _events_after(
        self,
        submission: MessageSubmission,
        last_event_id: str | None,
    ) -> list[SSEEventEnvelope]:
        after_sequence = 0
        if last_event_id:
            cursor = self.events.get(last_event_id)
            if (
                cursor is None
                or cursor.message_version_id != submission.message_version_id
            ):
                raise AppError(
                    404,
                    "last_event_not_found",
                    "The replay cursor does not belong to this image generation",
                )
            after_sequence = cursor.sequence
        rows = self.db.scalars(
            self.events.query()
            .where(
                MessageStreamEvent.message_version_id
                == submission.message_version_id,
                MessageStreamEvent.sequence > after_sequence,
            )
            .order_by(MessageStreamEvent.sequence)
        ).all()
        return [self._envelope(row) for row in rows]

    def _replay(
        self,
        submission: MessageSubmission,
        request_hash: str,
        last_event_id: str | None,
    ) -> Iterable[str]:
        if submission.request_hash != request_hash:
            raise AppError(
                409,
                "idempotency_key_reused",
                "The Idempotency-Key was already used with a different request",
            )

        def stream() -> Iterable[str]:
            cursor = last_event_id
            deadline = time.monotonic() + 30
            while True:
                envelopes = self._events_after(submission, cursor)
                for envelope in envelopes:
                    cursor = envelope.event_id
                    yield self._encode(envelope)
                self.db.rollback()
                self.db.refresh(submission)
                if submission.status in _TERMINAL:
                    return
                if time.monotonic() >= deadline:
                    return
                time.sleep(0.05)

        return stream()

    @staticmethod
    def _prompt(content: str) -> str:
        prompt = _DRAW_COMMAND.sub("", content, count=1).strip()
        if not prompt:
            raise AppError(
                422,
                "image_prompt_required",
                "@绘图 后需要提供图片描述",
            )
        return prompt

    @staticmethod
    def _validate_mode(payload: MessageCreateRequest) -> None:
        if (
            payload.node_ids
            or payload.file_ids
            or payload.document_selection is not None
            or payload.selection_context is not None
            or payload.web_search
            or payload.search_route != "disabled"
            or payload.agent_mode
            or payload.graph_action != "none"
        ):
            raise AppError(
                422,
                "image_mode_combination_unsupported",
                "Image generation currently accepts a text prompt without search, Agent tools, files, nodes, or graph actions",
            )

    def _validate_parent_message(
        self,
        session_id: str,
        payload: MessageCreateRequest,
    ) -> None:
        if payload.parent_message_id is None:
            return
        parent = self.messages.require(payload.parent_message_id, "parent message")
        if parent.session_id != session_id:
            raise AppError(
                404,
                "parent_message_not_in_session",
                "Parent message does not belong to this session",
            )

    def preflight_create_stream(
        self,
        session_id: str,
        payload: MessageCreateRequest,
        *,
        idempotency_key: str | None,
        last_event_id: str | None,
    ) -> None:
        session = self.sessions.require(session_id, "session")
        if session.status == "closed":
            raise AppError(
                409,
                "session_closed",
                "Closed sessions cannot accept new messages",
            )
        self._validate_parent_message(session_id, payload)
        self._validate_mode(payload)
        self._prompt(payload.content)
        if not getattr(self.image_provider, "available", True):
            raise AppError(
                503,
                "image_provider_unavailable",
                getattr(
                    self.image_provider,
                    "reason",
                    "No usable image generation Provider is configured",
                ),
                {"provider_id": self.image_provider.provider_id},
            )
        if not self.image_provider.remote_capability:
            raise AppError(
                503,
                "remote_image_provider_required",
                "Image generation requires a real remote Provider",
            )
        normalized_key = idempotency_key.strip() if idempotency_key else None
        if idempotency_key is not None and not normalized_key:
            raise AppError(
                422,
                "invalid_idempotency_key",
                "Idempotency-Key cannot be blank",
            )
        if len(normalized_key or "") > 128:
            raise AppError(
                422,
                "invalid_idempotency_key",
                "Idempotency-Key is too long",
            )
        if last_event_id and not normalized_key:
            raise AppError(
                400,
                "idempotency_key_required",
                "Last-Event-ID replay requires the original Idempotency-Key",
            )

    def create_stream(
        self,
        session_id: str,
        payload: MessageCreateRequest,
        *,
        idempotency_key: str | None,
        last_event_id: str | None,
    ) -> Iterable[str]:
        self.preflight_create_stream(
            session_id,
            payload,
            idempotency_key=idempotency_key,
            last_event_id=last_event_id,
        )
        prompt = self._prompt(payload.content)

        normalized_key = idempotency_key.strip() if idempotency_key else None
        request_hash = self._request_hash(payload)
        key_hash = self._hash(normalized_key) if normalized_key else None
        if key_hash:
            existing = self.db.scalar(
                self.submissions.query().where(
                    MessageSubmission.session_id == session_id,
                    MessageSubmission.idempotency_key_hash == key_hash,
                )
            )
            if existing is not None:
                return self._replay(existing, request_hash, last_event_id)
            if last_event_id:
                raise AppError(
                    404,
                    "submission_not_found",
                    "No image generation exists for this replay cursor",
                )

        user_part_id = str(uuid4())
        user = self.messages.add(
            Message(
                workspace_id=self.workspace_id,
                session_id=session_id,
                parent_message_id=payload.parent_message_id,
                role="user",
                status="completed",
                content=payload.content,
                parts=[
                    {
                        "id": user_part_id,
                        "type": "text",
                        "status": "completed",
                        "content": payload.content,
                        "data": {},
                    }
                ],
            )
        )
        user_version = self.versions.add(
            MessageVersion(
                workspace_id=self.workspace_id,
                message_id=user.id,
                version=1,
                status="completed",
            )
        )
        self.parts.add(
            MessagePartRecord(
                id=user_part_id,
                workspace_id=self.workspace_id,
                message_version_id=user_version.id,
                ordinal=0,
                part_type="text",
                status="completed",
                content=payload.content,
            )
        )
        self.db.flush()

        image_part_id = str(uuid4())
        trace = {
            "provider_id": self.image_provider.provider_id,
            "provider_type": self.image_provider.provider_type,
            "model_id": self.image_provider.model_id,
            "remote_capability": True,
            "generation_mode": "image",
            "cost_status": "unpriced",
        }
        assistant = self.messages.add(
            Message(
                workspace_id=self.workspace_id,
                session_id=session_id,
                parent_message_id=user.id,
                role="assistant",
                status="streaming",
                content="",
                parts=[],
                provider_trace=trace,
            )
        )
        version = self.versions.add(
            MessageVersion(
                workspace_id=self.workspace_id,
                message_id=assistant.id,
                version=1,
                status="streaming",
                provider_trace=trace,
            )
        )
        self.db.flush()
        task = self.images.create(
            session_id=session_id,
            message_id=assistant.id,
            message_version_id=version.id,
            source_message_id=user.id,
            provider_id=self.image_provider.provider_id,
            model_id=self.image_provider.model_id,
            prompt=prompt,
            commit=False,
        )
        image_data = {
            "generation_id": task.id,
            "provider_id": task.provider_id,
            "model_id": task.model_id,
            "title": "正在绘图",
            "alt": task.prompt_summary,
            "aspect_ratio": "1 / 1",
            "progress_mode": "indeterminate",
            "preview_revision": 0,
        }
        image_record = self.parts.add(
            MessagePartRecord(
                id=image_part_id,
                workspace_id=self.workspace_id,
                message_version_id=version.id,
                ordinal=0,
                part_type="image",
                status="pending",
                content=task.prompt_summary,
                data=image_data,
            )
        )
        assistant.parts = [
            self._part(
                image_record.id,
                "pending",
                image_record.content,
                image_data,
            )
        ]
        self.db.add(
            MessageControl(
                workspace_id=self.workspace_id,
                message_version_id=version.id,
            )
        )
        submission = None
        if key_hash:
            submission = self.submissions.add(
                MessageSubmission(
                    workspace_id=self.workspace_id,
                    session_id=session_id,
                    idempotency_key_hash=key_hash,
                    request_hash=request_hash,
                    user_message_id=user.id,
                    assistant_message_id=assistant.id,
                    message_version_id=version.id,
                    status="streaming",
                )
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="message.stream_image",
            resource_type="message",
            resource_id=assistant.id,
            details={
                "generation_id": task.id,
                "provider_id": task.provider_id,
                "remote_capability": True,
            },
        )
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            if key_hash:
                existing = self.db.scalar(
                    self.submissions.query().where(
                        MessageSubmission.session_id == session_id,
                        MessageSubmission.idempotency_key_hash == key_hash,
                    )
                )
                if existing is not None:
                    return self._replay(existing, request_hash, last_event_id)
            raise

        sequence = 1
        initial_events: list[SSEEventEnvelope] = []
        for event_type, event_payload, part_id in (
            (
                "message.accepted",
                {"status": "accepted", "user_message_id": user.id},
                None,
            ),
            (
                "message.started",
                {"status": "streaming", "user_message_id": user.id},
                None,
            ),
            (
                "image.generation.queued",
                {
                    "part": self._part(
                        image_record.id,
                        "pending",
                        image_record.content,
                        image_data,
                    )
                },
                image_record.id,
            ),
        ):
            initial_events.append(
                self._append_event(
                    session_id=session_id,
                    message_id=assistant.id,
                    version_id=version.id,
                    part_id=part_id,
                    sequence=sequence,
                    event_type=event_type,
                    payload=event_payload,
                )
            )
            sequence += 1

        def cancelled() -> bool:
            control = self.db.scalar(
                select(MessageControl)
                .where(MessageControl.message_version_id == version.id)
                .execution_options(populate_existing=True)
            )
            self.db.refresh(task)
            return bool(
                (control and control.cancel_requested) or task.cancel_requested
            )

        def stream() -> Iterable[str]:
            nonlocal sequence, trace
            for event in initial_events:
                yield self._encode(event)
            if cancelled():
                self.images.cancel(task.id)
                image_record.status = "failed"
                version.status = "cancelled"
                assistant.status = "cancelled"
                assistant.parts = [
                    self._part(
                        image_record.id,
                        "failed",
                        image_record.content,
                        image_data,
                    )
                ]
                if submission is not None:
                    submission.status = "cancelled"
                image_cancelled = self._append_event(
                    session_id=session_id,
                    message_id=assistant.id,
                    version_id=version.id,
                    part_id=image_record.id,
                    sequence=sequence,
                    event_type="image.generation.failed",
                    payload={
                        "status": "cancelled",
                        "part": self._part(
                            image_record.id,
                            "failed",
                            image_record.content,
                            image_data,
                        ),
                    },
                )
                sequence += 1
                cancelled_event = self._append_event(
                    session_id=session_id,
                    message_id=assistant.id,
                    version_id=version.id,
                    part_id=None,
                    sequence=sequence,
                    event_type="message.cancelled",
                    payload={"status": "cancelled"},
                )
                yield self._encode(image_cancelled)
                yield self._encode(cancelled_event)
                return
            attempt = ProviderAttempt(
                workspace_id=self.workspace_id,
                session_id=session_id,
                message_version_id=version.id,
                attempt_no=1,
                provider_id=task.provider_id,
                model_id=task.model_id,
                status="running",
            )
            self.db.add(attempt)
            self.images.mark_running(task)
            image_record.status = "streaming"
            started_part = self._part(
                image_record.id,
                "streaming",
                image_record.content,
                image_data,
            )
            started = self._append_event(
                session_id=session_id,
                message_id=assistant.id,
                version_id=version.id,
                part_id=image_record.id,
                sequence=sequence,
                event_type="image.generation.started",
                payload={"part": started_part},
            )
            sequence += 1
            yield self._encode(started)

            provider_stream = self.image_provider.stream_generate(
                ImageGenerationRequest(prompt=prompt, partial_images=2)
            )
            started_at = time.monotonic()
            completed = False
            usage: dict = {}
            try:
                for provider_event in provider_stream:
                    if cancelled():
                        raise _ImageCancellationRequested()
                    if not attempt.received_first_token:
                        attempt.received_first_token = True
                        trace["first_image_ms"] = int(
                            (time.monotonic() - started_at) * 1000
                        )
                    usage = dict(provider_event.usage or usage)
                    is_final = provider_event.type == "completed"
                    file = self.images.store_image(
                        task,
                        provider_event.image_bytes,
                        provider_event.mime_type,
                        partial_index=provider_event.partial_index,
                        completed=is_final,
                        provider_trace={
                            "remote_request_id": getattr(
                                self.image_provider, "last_request_id", None
                            ),
                            "first_image_ms": trace.get("first_image_ms"),
                        },
                    )
                    image_data.update(
                        {
                            "file_id": file.id,
                            "mime_type": file.mime_type,
                            "progress_mode": (
                                "partial_preview"
                                if provider_event.partial_index is not None
                                else "indeterminate"
                            ),
                            "partial_index": provider_event.partial_index,
                            "preview_revision": int(
                                image_data.get("preview_revision") or 0
                            )
                            + 1,
                            "title": "图片已生成" if is_final else "正在生成预览",
                        }
                    )
                    image_record.status = "completed" if is_final else "streaming"
                    image_record.data = dict(image_data)
                    assistant.parts = [
                        self._part(
                            image_record.id,
                            image_record.status,
                            image_record.content,
                            image_data,
                        )
                    ]
                    envelope = self._append_event(
                        session_id=session_id,
                        message_id=assistant.id,
                        version_id=version.id,
                        part_id=image_record.id,
                        sequence=sequence,
                        event_type=(
                            "image.generation.completed"
                            if is_final
                            else "image.generation.preview"
                        ),
                        payload={
                            "part": self._part(
                                image_record.id,
                                image_record.status,
                                image_record.content,
                                image_data,
                            )
                        },
                    )
                    sequence += 1
                    yield self._encode(envelope)
                    if is_final:
                        completed = True
                if not completed:
                    raise AppError(
                        502,
                        "image_generation_incomplete",
                        "The image Provider stream ended without a final image",
                    )
                latency_ms = int((time.monotonic() - started_at) * 1000)
                attempt.status = "completed"
                attempt.remote_request_id = getattr(
                    self.image_provider, "last_request_id", None
                )
                self.db.add(
                    UsageEvent(
                        workspace_id=self.workspace_id,
                        provider_id=task.provider_id,
                        model_id=task.model_id,
                        feature="image_generation",
                        input_tokens=int(usage.get("input_tokens") or 0),
                        output_tokens=int(usage.get("output_tokens") or 0),
                        total_tokens=int(usage.get("total_tokens") or 0),
                        attempt=1,
                        cost_status="unpriced",
                        latency_ms=latency_ms,
                    )
                )
                trace = {
                    **trace,
                    "remote_request_id": attempt.remote_request_id,
                    "latency_ms": latency_ms,
                    "image_count": 1,
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    "total_tokens": int(usage.get("total_tokens") or 0),
                }
                assistant.status = "completed"
                assistant.provider_trace = trace
                version.status = "completed"
                version.provider_trace = trace
                if submission is not None:
                    submission.status = "completed"
                completed_event = self._append_event(
                    session_id=session_id,
                    message_id=assistant.id,
                    version_id=version.id,
                    part_id=None,
                    sequence=sequence,
                    event_type="message.completed",
                    payload={"status": "completed", "provider_trace": trace},
                )
                yield self._encode(completed_event)
            except _ImageCancellationRequested:
                attempt.status = "cancelled"
                self.images.cancel(task.id)
                image_record.status = "failed"
                version.status = "cancelled"
                assistant.status = "cancelled"
                assistant.parts = [
                    self._part(
                        image_record.id,
                        "failed",
                        image_record.content,
                        image_data,
                    )
                ]
                if submission is not None:
                    submission.status = "cancelled"
                image_cancelled = self._append_event(
                    session_id=session_id,
                    message_id=assistant.id,
                    version_id=version.id,
                    part_id=image_record.id,
                    sequence=sequence,
                    event_type="image.generation.failed",
                    payload={
                        "status": "cancelled",
                        "part": self._part(
                            image_record.id,
                            "failed",
                            image_record.content,
                            image_data,
                        ),
                    },
                )
                sequence += 1
                yield self._encode(image_cancelled)
                cancelled_event = self._append_event(
                    session_id=session_id,
                    message_id=assistant.id,
                    version_id=version.id,
                    part_id=None,
                    sequence=sequence,
                    event_type="message.cancelled",
                    payload={"status": "cancelled"},
                )
                yield self._encode(cancelled_event)
            except GeneratorExit:
                attempt.status = "cancelled"
                self.images.cancel(task.id)
                image_record.status = "failed"
                version.status = "cancelled"
                assistant.status = "cancelled"
                assistant.parts = [
                    self._part(
                        image_record.id,
                        "failed",
                        image_record.content,
                        image_data,
                    )
                ]
                if submission is not None:
                    submission.status = "cancelled"
                self._append_event(
                    session_id=session_id,
                    message_id=assistant.id,
                    version_id=version.id,
                    part_id=image_record.id,
                    sequence=sequence,
                    event_type="image.generation.failed",
                    payload={
                        "status": "cancelled",
                        "part": self._part(
                            image_record.id,
                            "failed",
                            image_record.content,
                            image_data,
                        ),
                    },
                )
                sequence += 1
                self._append_event(
                    session_id=session_id,
                    message_id=assistant.id,
                    version_id=version.id,
                    part_id=None,
                    sequence=sequence,
                    event_type="message.cancelled",
                    payload={"status": "cancelled"},
                )
                raise
            except Exception as exc:
                self.db.refresh(version)
                if version.status == "cancelled":
                    if attempt.status == "running":
                        attempt.status = "cancelled"
                    self.db.commit()
                    return
                attempt.status = "failed"
                attempt.error_type = type(exc).__name__
                error_detail = " ".join(str(exc).split()).strip()[:300]
                error_message = (
                    f"图片生成失败：{error_detail}"
                    if error_detail
                    else "图片生成失败，未写入伪造结果。"
                )
                self.images.fail(
                    task,
                    "image_generation_failed",
                    error_detail
                    or "The image Provider failed before returning a final image",
                )
                image_data["title"] = error_detail or "图片生成失败"
                image_record.status = "failed"
                image_record.data = dict(image_data)
                version.status = "failed"
                assistant.status = "failed"
                if submission is not None:
                    submission.status = "failed"
                assistant.parts = [
                    self._part(
                        image_record.id,
                        "failed",
                        image_record.content,
                        image_data,
                    )
                ]
                image_failed = self._append_event(
                    session_id=session_id,
                    message_id=assistant.id,
                    version_id=version.id,
                    part_id=image_record.id,
                    sequence=sequence,
                    event_type="image.generation.failed",
                    payload={
                        "status": "failed",
                        "error": {
                            "code": "image_generation_failed",
                            "message": error_message,
                        },
                        "part": self._part(
                            image_record.id,
                            "failed",
                            image_record.content,
                            image_data,
                        ),
                    },
                )
                sequence += 1
                yield self._encode(image_failed)
                failed = self._append_event(
                    session_id=session_id,
                    message_id=assistant.id,
                    version_id=version.id,
                    part_id=None,
                    sequence=sequence,
                    event_type="message.failed",
                    payload={
                        "status": "failed",
                        "error": {
                            "code": "image_generation_failed",
                            "message": error_message,
                        },
                    },
                )
                yield self._encode(failed)
            finally:
                close = getattr(provider_stream, "close", None)
                if callable(close):
                    close()

        return stream()
