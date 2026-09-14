from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.memory_event_models import (
    MemoryEvent,
    MemoryProjectionOutbox,
    MemoryScopeContext,
    MemoryStream,
)


class MemoryEventRepository:
    """Scope-safe event-store persistence. There is intentionally no naked get()."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _stream_in_scope(self, scope: MemoryScopeContext, *criteria: Any) -> MemoryStream | None:
        """Scope-filtered stream lookup (subject must be the caller's or unowned)."""

        return self.db.scalar(
            select(MemoryStream).where(
                MemoryStream.tenant_id == scope.tenant_id,
                (MemoryStream.workspace_id == scope.workspace_id)
                | (MemoryStream.workspace_id.is_(None)),
                (MemoryStream.subject_user_id == scope.principal_user_id)
                | (MemoryStream.subject_user_id.is_(None)),
                *criteria,
            )
        )

    def _stream_by_identity(self, scope: MemoryScopeContext, *criteria: Any) -> MemoryStream | None:
        """Same lookup without the recorded-subject filter (tenant/workspace kept).

        ``memory_streams`` is unique on (tenant_id, aggregate_type, aggregate_id),
        so the recorded subject decides visibility in ``_stream_in_scope`` but
        *not* which row owns the aggregate. A workspace-owned memory atom
        (``memory_records.subject_user_id`` NULL) can legitimately be written by
        different principals — the interactive path records the signed-in user
        while the ``system:memory-extraction`` sweep records the system actor —
        and then the strict lookup misses the row that already holds the unique
        key. Callers resolve through this second step so that cannot turn into a
        spurious "create": the INSERT would fail with
        ``UNIQUE constraint failed: memory_streams.tenant_id,
        memory_streams.aggregate_type, memory_streams.aggregate_id`` and abort the
        caller (observed live as "Memory extraction failed for session ...").
        Record-level access is still enforced by the command services
        (``_require_record``) before anything is appended.
        """

        return self.db.scalar(
            select(MemoryStream).where(
                MemoryStream.tenant_id == scope.tenant_id,
                (MemoryStream.workspace_id == scope.workspace_id)
                | (MemoryStream.workspace_id.is_(None)),
                *criteria,
            )
        )

    def stream_for_aggregate(
        self, scope: MemoryScopeContext, aggregate_type: str, aggregate_id: str
    ) -> MemoryStream | None:
        criteria = (
            MemoryStream.aggregate_type == aggregate_type,
            MemoryStream.aggregate_id == aggregate_id,
        )
        stream = self._stream_in_scope(scope, *criteria)
        if stream is not None:
            return stream
        return self._stream_by_identity(scope, *criteria)

    def require_stream(self, scope: MemoryScopeContext, stream_id: str) -> MemoryStream:
        criteria = (MemoryStream.id == stream_id,)
        stream = self._stream_in_scope(scope, *criteria) or self._stream_by_identity(
            scope, *criteria
        )
        if stream is None:
            raise AppError(404, "memory_stream_not_found", "Memory stream was not found")
        return stream

    def event_by_id(self, scope: MemoryScopeContext, event_id: str) -> MemoryEvent | None:
        return self.db.scalar(
            select(MemoryEvent).where(
                MemoryEvent.event_id == event_id,
                MemoryEvent.tenant_id == scope.tenant_id,
                (MemoryEvent.workspace_id == scope.workspace_id)
                | (MemoryEvent.workspace_id.is_(None)),
                (MemoryEvent.subject_user_id == scope.principal_user_id)
                | (MemoryEvent.subject_user_id.is_(None)),
            )
        )

    def event_by_idempotency(
        self, tenant_id: str, producer: str, idempotency_key: str
    ) -> MemoryEvent | None:
        return self.db.scalar(
            select(MemoryEvent).where(
                MemoryEvent.tenant_id == tenant_id,
                MemoryEvent.producer == producer,
                MemoryEvent.idempotency_key == idempotency_key,
            )
        )

    def stream_events(
        self, scope: MemoryScopeContext, stream_id: str, *, after_version: int = 0
    ) -> Sequence[MemoryEvent]:
        self.require_stream(scope, stream_id)
        return self.db.scalars(
            select(MemoryEvent)
            .where(
                MemoryEvent.stream_id == stream_id,
                MemoryEvent.stream_version > after_version,
            )
            .order_by(MemoryEvent.stream_version)
        ).all()

    def events_after(
        self, scope: MemoryScopeContext, global_position: int, *, limit: int = 500
    ) -> Sequence[MemoryEvent]:
        return self.db.scalars(
            select(MemoryEvent)
            .where(
                MemoryEvent.tenant_id == scope.tenant_id,
                MemoryEvent.global_position > global_position,
                (MemoryEvent.workspace_id == scope.workspace_id)
                | (MemoryEvent.workspace_id.is_(None)),
                (MemoryEvent.subject_user_id == scope.principal_user_id)
                | (MemoryEvent.subject_user_id.is_(None)),
            )
            .order_by(MemoryEvent.global_position)
            .limit(limit)
        ).all()


class MemoryOutboxRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, item: MemoryProjectionOutbox) -> MemoryProjectionOutbox:
        self.db.add(item)
        self.db.flush()
        return item
