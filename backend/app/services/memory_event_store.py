from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.memory_event_models import (
    MemoryEvent,
    MemoryPayloadKey,
    MemoryProjectionOutbox,
    MemoryScopeContext,
    MemoryStream,
    utc_now,
)
from app.domain.memory_event_types import CURRENT_EVENT_SCHEMA_VERSIONS
from app.repositories.memory_events import MemoryEventRepository
from app.services.memory_crypto import MemoryPayloadCipher, payload_hash
from app.services.memory_sensitive_filter import SensitiveDataFilter
from app.services.memory_upcasters import EventUpcasterRegistry, upcasters


@dataclass(frozen=True, slots=True)
class AppendEvent:
    event_type: str
    payload: dict[str, Any]
    idempotency_key: str
    producer: str = "api"
    event_schema_version: int = 1
    actor_type: str = "user"
    actor_id: str = "system"
    correlation_id: str | None = None
    causation_id: str | None = None
    occurred_at: datetime | None = None
    sensitivity: str = "normal"
    metadata: dict[str, Any] | None = None
    conversation_id: str | None = None
    project_id: str | None = None
    file_id: str | None = None
    knowledge_node_id: str | None = None


@dataclass(frozen=True, slots=True)
class AppendResult:
    event: MemoryEvent
    idempotent_replay: bool


class MemoryEventStore:
    def __init__(
        self,
        db: Session,
        cipher: MemoryPayloadCipher,
        *,
        upcaster_registry: EventUpcasterRegistry = upcasters,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.events = MemoryEventRepository(db)
        self.upcasters = upcaster_registry
        self.sensitive_filter = SensitiveDataFilter()

    def append(
        self,
        scope: MemoryScopeContext,
        *,
        aggregate_type: str,
        aggregate_id: str,
        expected_version: int | None,
        event: AppendEvent,
        outbox_kinds: tuple[str, ...] = (),
    ) -> AppendResult:
        current_schema = CURRENT_EVENT_SCHEMA_VERSIONS.get(event.event_type)
        if current_schema is not None and event.event_schema_version != current_schema:
            raise AppError(
                422,
                "memory_event_schema_version_invalid",
                "Event must be appended using the current schema version",
                {"event_type": event.event_type, "current_version": current_schema},
            )
        self.sensitive_filter.require_safe(event.payload)
        supplied_hash = payload_hash(event.payload)
        previous = self.events.event_by_idempotency(
            scope.tenant_id, event.producer, event.idempotency_key
        )
        if previous is not None:
            if previous.payload_hash != supplied_hash or previous.event_type != event.event_type:
                raise AppError(
                    409,
                    "memory_event_idempotency_conflict",
                    "The idempotency key was already used for a different event",
                )
            return AppendResult(previous, True)

        stream = self.events.stream_for_aggregate(scope, aggregate_type, aggregate_id)
        if stream is None:
            if expected_version not in (None, 0):
                raise self._version_conflict(aggregate_id, expected_version, 0)
            stream = MemoryStream(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                current_version=0,
                tenant_id=scope.tenant_id,
                subject_user_id=scope.principal_user_id,
                workspace_id=scope.workspace_id,
                task_id=scope.task_id,
            )
            try:
                # A concurrent creator may win the aggregate unique key. Keep
                # that race inside a SAVEPOINT so the caller's outer UoW and
                # unrelated pending writes are never rolled back.
                with self.db.begin_nested():
                    self.db.add(stream)
                    self.db.flush()
            except IntegrityError:
                stream = self.events.stream_for_aggregate(scope, aggregate_type, aggregate_id)
                if stream is None:
                    raise
        current_version = stream.current_version
        if expected_version is not None and expected_version != current_version:
            raise self._version_conflict(aggregate_id, expected_version, current_version)

        encrypted = self.cipher.encrypt(event.payload)
        key = MemoryPayloadKey(
            stream_id=stream.id,
            wrapped_dek=encrypted.wrapped_dek,
            algorithm=encrypted.algorithm,
            key_version=self.cipher.key_version,
        )
        self.db.add(key)
        self.db.flush()
        next_version = current_version + 1
        stored = MemoryEvent(
            stream_id=stream.id,
            stream_version=next_version,
            event_type=event.event_type,
            event_schema_version=event.event_schema_version,
            producer=event.producer,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            tenant_id=scope.tenant_id,
            subject_user_id=scope.principal_user_id,
            workspace_id=scope.workspace_id,
            task_id=scope.task_id,
            conversation_id=event.conversation_id or scope.conversation_id,
            project_id=event.project_id or scope.project_id,
            file_id=event.file_id,
            knowledge_node_id=event.knowledge_node_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            idempotency_key=event.idempotency_key,
            occurred_at=event.occurred_at or utc_now(),
            sensitivity=event.sensitivity,
            payload_ciphertext=encrypted.ciphertext,
            payload_key_id=key.id,
            payload_hash=encrypted.plaintext_hash,
            metadata_json=dict(event.metadata or {}),
        )
        self.db.add(stored)
        cas = self.db.execute(
            update(MemoryStream)
            .where(MemoryStream.id == stream.id, MemoryStream.current_version == current_version)
            .values(current_version=next_version, updated_at=utc_now())
            .execution_options(synchronize_session=False)
        )
        if cas.rowcount != 1:
            raise self._version_conflict(aggregate_id, current_version, current_version + 1)
        self.db.flush()
        key.event_id = stored.event_id
        stream.current_version = next_version
        stream.updated_at = utc_now()
        for projection_kind in outbox_kinds:
            self.db.add(
                MemoryProjectionOutbox(
                    event_id=stored.event_id,
                    tenant_id=scope.tenant_id,
                    workspace_id=scope.workspace_id,
                    projection_kind=projection_kind,
                    aggregate_id=aggregate_id,
                    dedupe_key=f"{stored.event_id}:{projection_kind}",
                    payload_json={
                        "stream_id": stream.id,
                        "stream_version": next_version,
                        "event_type": event.event_type,
                    },
                )
            )
        self.db.flush()
        return AppendResult(stored, False)

    def read_payload(self, scope: MemoryScopeContext, event_id: str) -> dict[str, Any]:
        stored = self.events.event_by_id(scope, event_id)
        if stored is None:
            raise AppError(404, "memory_event_not_found", "Memory event was not found")
        key = self.db.get(MemoryPayloadKey, stored.payload_key_id) if stored.payload_key_id else None
        if key is None or key.status == "destroyed":
            raise AppError(410, "memory_event_payload_forgotten", "Event payload was forgotten")
        payload = self.cipher.decrypt(stored.payload_ciphertext, key.wrapped_dek)
        version, payload = self.upcasters.upcast(
            stored.event_type, stored.event_schema_version, payload
        )
        if version < stored.event_schema_version:
            raise RuntimeError("upcaster moved an event backwards")
        return payload

    def destroy_stream_payloads(
        self, scope: MemoryScopeContext, stream_id: str, *, actor_id: str, reason: str
    ) -> int:
        stream = self.events.require_stream(scope, stream_id)
        now = utc_now()
        rows = self.db.scalars(
            select(MemoryPayloadKey).where(MemoryPayloadKey.stream_id == stream.id)
        ).all()
        destroyed = 0
        for key in rows:
            if key.status == "destroyed":
                continue
            key.wrapped_dek = None
            key.status = "destroyed"
            key.destroyed_at = now
            key.destroyed_by = actor_id
            key.reason = reason[:240]
            destroyed += 1
        stream.status = "forgotten"
        stream.payload_key_id = None
        self.db.flush()
        return destroyed

    def replay(
        self,
        scope: MemoryScopeContext,
        projector: Any,
        *,
        after_position: int = 0,
        limit: int = 500,
    ) -> int:
        position = after_position
        for event in self.events.events_after(scope, after_position, limit=limit):
            payload = self.read_payload(scope, event.event_id)
            projector.apply(event, payload)
            position = event.global_position
        return position

    @staticmethod
    def _version_conflict(aggregate_id: str, expected: int, current: int) -> AppError:
        return AppError(
            409,
            "memory_stream_version_conflict",
            "The memory stream changed before this command could be applied",
            {
                "aggregate_id": aggregate_id,
                "expected_stream_version": expected,
                "current_stream_version": current,
            },
        )
