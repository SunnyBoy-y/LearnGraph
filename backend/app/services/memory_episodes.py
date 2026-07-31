from __future__ import annotations

import hashlib
import json

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain.memory_event_models import ConversationEpisode, MemoryScopeContext, new_id, utc_now
from app.domain.memory_event_types import MemoryEventType
from app.domain.schemas.memory_tasks import (
    EpisodeGenerateRequest,
    EpisodeSearchRequest,
    EpisodeView,
)
from app.services.memory_event_store import AppendEvent, MemoryEventStore


class MemoryEpisodeService:
    def __init__(self, db: Session, store: MemoryEventStore) -> None:
        self.db = db
        self.store = store

    def generate(
        self, scope: MemoryScopeContext, actor_id: str, request: EpisodeGenerateRequest
    ) -> EpisodeView:
        episode_id = f"episode_{new_id()}"
        payload = request.model_dump()
        result = self.store.append(
            scope,
            aggregate_type="episode",
            aggregate_id=episode_id,
            expected_version=0,
            event=AppendEvent(
                event_type=MemoryEventType.EPISODE_CLOSED,
                payload=payload,
                idempotency_key=request.idempotency_key,
                actor_id=actor_id,
                conversation_id=request.conversation_id,
            ),
            outbox_kinds=("index",),
        )
        existing = self.db.scalar(
            select(ConversationEpisode).where(
                ConversationEpisode.stream_id == result.event.stream_id
            )
        )
        if existing is None:
            digest = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            existing = ConversationEpisode(
                id=episode_id,
                stream_id=result.event.stream_id,
                stream_version=result.event.stream_version,
                tenant_id=scope.tenant_id,
                subject_user_id=scope.principal_user_id,
                workspace_id=scope.workspace_id,
                conversation_id=request.conversation_id,
                task_id=request.task_id,
                title=request.title,
                summary=request.summary,
                decisions_json=request.decisions,
                open_questions_json=request.open_questions,
                constraints_json=request.constraints,
                entities_json=request.entities,
                source_message_refs_json=request.source_message_refs,
                source_event_refs_json=[result.event.event_id],
                start_event_position=result.event.global_position,
                end_event_position=result.event.global_position,
                started_at=result.event.occurred_at,
                ended_at=utc_now(),
                status="closed",
                boundary_reason=request.boundary_reason,
                content_hash=digest,
                head_event_id=result.event.event_id,
            )
            self.db.add(existing)
            self.db.flush()
        self.db.commit()
        return self._view(existing)

    def search(
        self, scope: MemoryScopeContext, request: EpisodeSearchRequest
    ) -> list[EpisodeView]:
        statement = select(ConversationEpisode).where(
            ConversationEpisode.tenant_id == scope.tenant_id,
            ConversationEpisode.workspace_id == scope.workspace_id,
            (ConversationEpisode.subject_user_id == scope.principal_user_id)
            | (ConversationEpisode.subject_user_id.is_(None)),
            ConversationEpisode.status.in_(("open", "closed", "archived")),
        )
        if request.conversation_id:
            statement = statement.where(
                ConversationEpisode.conversation_id == request.conversation_id
            )
        if request.task_id:
            statement = statement.where(ConversationEpisode.task_id == request.task_id)
        if request.query.strip():
            like = f"%{request.query.strip()}%"
            statement = statement.where(
                or_(
                    ConversationEpisode.title.ilike(like),
                    ConversationEpisode.summary.ilike(like),
                )
            )
        rows = self.db.scalars(
            statement.order_by(ConversationEpisode.ended_at.desc()).limit(request.limit)
        ).all()
        return [self._view(row) for row in rows]

    @staticmethod
    def _view(row: ConversationEpisode) -> EpisodeView:
        return EpisodeView(
            episode_id=row.id,
            stream_version=row.stream_version,
            conversation_id=row.conversation_id,
            task_id=row.task_id,
            title=row.title,
            summary=row.summary,
            decisions=row.decisions_json,
            open_questions=row.open_questions_json,
            constraints=row.constraints_json,
            source_message_refs=row.source_message_refs_json,
            status=row.status,
            boundary_reason=row.boundary_reason,
        )
