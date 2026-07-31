from __future__ import annotations

from collections.abc import Sequence

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

    def stream_for_aggregate(
        self, scope: MemoryScopeContext, aggregate_type: str, aggregate_id: str
    ) -> MemoryStream | None:
        return self.db.scalar(
            select(MemoryStream).where(
                MemoryStream.tenant_id == scope.tenant_id,
                MemoryStream.aggregate_type == aggregate_type,
                MemoryStream.aggregate_id == aggregate_id,
                (MemoryStream.workspace_id == scope.workspace_id)
                | (MemoryStream.workspace_id.is_(None)),
                (MemoryStream.subject_user_id == scope.principal_user_id)
                | (MemoryStream.subject_user_id.is_(None)),
            )
        )

    def require_stream(self, scope: MemoryScopeContext, stream_id: str) -> MemoryStream:
        stream = self.db.scalar(
            select(MemoryStream).where(
                MemoryStream.id == stream_id,
                MemoryStream.tenant_id == scope.tenant_id,
                (MemoryStream.workspace_id == scope.workspace_id)
                | (MemoryStream.workspace_id.is_(None)),
                (MemoryStream.subject_user_id == scope.principal_user_id)
                | (MemoryStream.subject_user_id.is_(None)),
            )
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
