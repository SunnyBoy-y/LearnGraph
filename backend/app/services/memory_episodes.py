from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.memory_event_models import (
    ConversationEpisode,
    MemoryEvent,
    MemoryScopeContext,
    new_id,
    utc_now,
)
from app.domain.memory_event_types import MemoryEventType
from app.domain.models import Message
from app.domain.schemas.memory_tasks import (
    EpisodeCloseRequest,
    EpisodeGenerateRequest,
    EpisodeSearchRequest,
    EpisodeView,
)
from app.services.episode_boundary import BoundaryInputs, boundary_reason_value, top_boundary_signal
from app.services.memory_event_store import AppendEvent, MemoryEventStore


@dataclass(frozen=True, slots=True)
class EpisodeObservationResult:
    """Outcome of a deterministic episode lifecycle observation."""

    boundary_detected: bool
    boundary_reason: str | None
    opened_episode: EpisodeView | None
    closed_episode: EpisodeView | None


class MemoryEpisodeService:
    def __init__(self, db: Session, store: MemoryEventStore) -> None:
        self.db = db
        self.store = store

    def generate(
        self, scope: MemoryScopeContext, actor_id: str, request: EpisodeGenerateRequest
    ) -> EpisodeView:
        """Compatibility path for callers that already have a complete episode.

        This intentionally retains the legacy one-shot ``episode.closed`` stream
        (version 1). New deterministic lifecycle callers use
        :meth:`observe_and_advance`, which creates an open interval first and
        closes it on the same stream.
        """
        episode_id = f"episode_{new_id()}"
        self._validate_source_message_refs(
            scope, request.conversation_id, request.source_message_refs
        )
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
            digest = self._content_hash(payload)
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

    def observe_and_advance(
        self,
        scope: MemoryScopeContext,
        actor_id: str,
        *,
        conversation_id: str,
        source_message_refs: list[str],
        inputs: BoundaryInputs,
        idempotency_key: str,
        task_id: str | None = None,
    ) -> EpisodeObservationResult:
        """Observe a durable conversation window and advance its episode lifecycle.

        The caller provides only already-persisted source message references and
        trusted rule inputs. This method performs no LLM work: it initializes an
        open interval on first observation, does nothing with no boundary, and
        atomically closes the existing interval plus opens its successor when a
        deterministic rule fires.
        """
        if not source_message_refs:
            raise AppError(
                422,
                "episode_source_refs_required",
                "Episode observation requires at least one source message reference",
            )
        self._validate_source_message_refs(scope, conversation_id, source_message_refs)
        replay_open = self._episode_for_idempotency(scope, f"{idempotency_key}:open")
        replay_close = self._episode_for_idempotency(scope, f"{idempotency_key}:close")
        if replay_open is not None and replay_close is not None:
            return EpisodeObservationResult(
                boundary_detected=True,
                boundary_reason=replay_close.boundary_reason,
                opened_episode=self._view(replay_open),
                closed_episode=self._view(replay_close),
            )
        if replay_open is not None:
            return EpisodeObservationResult(
                boundary_detected=False,
                boundary_reason=None,
                opened_episode=self._view(replay_open),
                closed_episode=None,
            )
        open_episode = self._find_open_episode(scope, conversation_id)
        signal = top_boundary_signal(inputs)

        if open_episode is None:
            opened = self._open_episode(
                scope,
                actor_id,
                conversation_id=conversation_id,
                task_id=task_id,
                source_message_refs=source_message_refs,
                boundary_reason=boundary_reason_value(inputs),
                idempotency_key=f"{idempotency_key}:open",
            )
            self.db.commit()
            return EpisodeObservationResult(
                boundary_detected=False,
                boundary_reason=None,
                opened_episode=self._view(opened),
                closed_episode=None,
            )

        if signal is None:
            return EpisodeObservationResult(
                boundary_detected=False,
                boundary_reason=None,
                opened_episode=None,
                closed_episode=None,
            )

        reason = boundary_reason_value(inputs)
        closed = self._close_episode(
            scope,
            actor_id,
            open_episode,
            source_message_refs=source_message_refs,
            boundary_reason=reason,
            idempotency_key=f"{idempotency_key}:close",
        )
        opened = self._open_episode(
            scope,
            actor_id,
            conversation_id=conversation_id,
            task_id=task_id,
            source_message_refs=source_message_refs,
            boundary_reason=reason,
            idempotency_key=f"{idempotency_key}:open",
        )
        self.db.commit()
        return EpisodeObservationResult(
            boundary_detected=True,
            boundary_reason=reason,
            opened_episode=self._view(opened),
            closed_episode=self._view(closed),
        )

    def close(
        self,
        scope: MemoryScopeContext,
        actor_id: str,
        episode_id: str,
        request: EpisodeCloseRequest,
    ) -> EpisodeView:
        """Finalize one open episode with CAS-protected structured content."""
        replay = self._episode_for_idempotency(scope, request.idempotency_key)
        if replay is not None:
            return self._view(replay)
        row = self._require_open_episode(scope, episode_id)
        self._validate_source_message_refs(
            scope, row.conversation_id, request.source_message_refs
        )
        source_refs = list(
            dict.fromkeys(row.source_message_refs_json + request.source_message_refs)
        )
        payload = {
            "episode_id": row.id,
            "conversation_id": row.conversation_id,
            "task_id": row.task_id,
            "title": request.title,
            "summary": request.summary,
            "decisions": request.decisions,
            "open_questions": request.open_questions,
            "constraints": request.constraints,
            "entities": request.entities,
            "source_message_refs": source_refs,
            "boundary_reason": row.boundary_reason,
            "status": "closed",
        }
        result = self.store.append(
            scope,
            aggregate_type="episode",
            aggregate_id=row.id,
            expected_version=request.expected_stream_version,
            event=AppendEvent(
                event_type=MemoryEventType.EPISODE_CLOSED,
                payload=payload,
                idempotency_key=request.idempotency_key,
                actor_id=actor_id,
                conversation_id=row.conversation_id,
            ),
            outbox_kinds=("index",),
        )
        if result.idempotent_replay:
            return self._view(row)
        row.stream_version = result.event.stream_version
        row.title = request.title
        row.summary = request.summary
        row.decisions_json = request.decisions
        row.open_questions_json = request.open_questions
        row.constraints_json = request.constraints
        row.entities_json = request.entities
        row.source_message_refs_json = source_refs
        row.source_event_refs_json = list(
            dict.fromkeys(row.source_event_refs_json + [result.event.event_id])
        )
        row.end_event_position = result.event.global_position
        row.ended_at = result.event.occurred_at
        row.status = "closed"
        row.content_hash = self._content_hash(payload)
        row.head_event_id = result.event.event_id
        self.db.commit()
        return self._view(row)

    def _episode_for_idempotency(
        self, scope: MemoryScopeContext, idempotency_key: str
    ) -> ConversationEpisode | None:
        event = self.db.scalar(
            select(MemoryEvent).where(
                MemoryEvent.tenant_id == scope.tenant_id,
                MemoryEvent.producer == "api",
                MemoryEvent.idempotency_key == idempotency_key,
                MemoryEvent.event_type.in_(
                    (MemoryEventType.EPISODE_OPENED, MemoryEventType.EPISODE_CLOSED)
                ),
            )
        )
        if event is None:
            return None
        return self.db.scalar(
            select(ConversationEpisode).where(
                ConversationEpisode.stream_id == event.stream_id,
                ConversationEpisode.tenant_id == scope.tenant_id,
                ConversationEpisode.workspace_id == scope.workspace_id,
                (ConversationEpisode.subject_user_id == scope.principal_user_id)
                | (ConversationEpisode.subject_user_id.is_(None)),
            )
        )

    def _require_open_episode(
        self, scope: MemoryScopeContext, episode_id: str
    ) -> ConversationEpisode:
        row = self.db.scalar(
            select(ConversationEpisode).where(
                ConversationEpisode.id == episode_id,
                ConversationEpisode.tenant_id == scope.tenant_id,
                ConversationEpisode.workspace_id == scope.workspace_id,
                (ConversationEpisode.subject_user_id == scope.principal_user_id)
                | (ConversationEpisode.subject_user_id.is_(None)),
            )
        )
        if row is None:
            raise AppError(404, "episode_not_found", "Episode was not found")
        if row.status != "open":
            raise AppError(409, "episode_not_open", "Episode is not open")
        return row

    def _validate_source_message_refs(
        self,
        scope: MemoryScopeContext,
        conversation_id: str,
        source_message_refs: list[str],
    ) -> None:
        """Require every provenance ref to be a message in this scoped conversation."""
        refs = list(dict.fromkeys(source_message_refs))
        if not refs or any(not ref.strip() for ref in refs):
            raise AppError(
                422,
                "episode_source_refs_required",
                "Episode requires non-empty source message references",
            )
        rows = self.db.scalars(
            select(Message).where(
                Message.id.in_(refs),
                Message.workspace_id == scope.workspace_id,
                Message.session_id == conversation_id,
            )
        ).all()
        found = {row.id for row in rows}
        missing = [ref for ref in refs if ref not in found]
        if missing:
            raise AppError(
                422,
                "episode_source_ref_invalid",
                "Episode source message reference is missing or outside the conversation scope",
                {"refs": missing},
            )

    def _find_open_episode(
        self, scope: MemoryScopeContext, conversation_id: str
    ) -> ConversationEpisode | None:
        return self.db.scalar(
            select(ConversationEpisode)
            .where(
                ConversationEpisode.tenant_id == scope.tenant_id,
                ConversationEpisode.workspace_id == scope.workspace_id,
                ConversationEpisode.conversation_id == conversation_id,
                (ConversationEpisode.subject_user_id == scope.principal_user_id)
                | (ConversationEpisode.subject_user_id.is_(None)),
                ConversationEpisode.status == "open",
            )
            .order_by(ConversationEpisode.started_at.desc())
        )

    def _open_episode(
        self,
        scope: MemoryScopeContext,
        actor_id: str,
        *,
        conversation_id: str,
        task_id: str | None,
        source_message_refs: list[str],
        boundary_reason: str,
        idempotency_key: str,
    ) -> ConversationEpisode:
        episode_id = f"episode_{new_id()}"
        payload = {
            "conversation_id": conversation_id,
            "task_id": task_id,
            "source_message_refs": source_message_refs,
            "boundary_reason": boundary_reason,
            "status": "open",
        }
        result = self.store.append(
            scope,
            aggregate_type="episode",
            aggregate_id=episode_id,
            expected_version=0,
            event=AppendEvent(
                event_type=MemoryEventType.EPISODE_OPENED,
                payload=payload,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                conversation_id=conversation_id,
            ),
            outbox_kinds=("index",),
        )
        existing = self.db.scalar(
            select(ConversationEpisode).where(
                ConversationEpisode.stream_id == result.event.stream_id
            )
        )
        if existing is not None:
            return existing
        row = ConversationEpisode(
            id=episode_id,
            stream_id=result.event.stream_id,
            stream_version=result.event.stream_version,
            tenant_id=scope.tenant_id,
            subject_user_id=scope.principal_user_id,
            workspace_id=scope.workspace_id,
            conversation_id=conversation_id,
            task_id=task_id,
            title="",
            summary="",
            decisions_json=[],
            open_questions_json=[],
            constraints_json=[],
            entities_json=[],
            source_message_refs_json=list(dict.fromkeys(source_message_refs)),
            source_event_refs_json=[result.event.event_id],
            start_event_position=result.event.global_position,
            end_event_position=None,
            started_at=result.event.occurred_at,
            ended_at=None,
            status="open",
            boundary_reason=boundary_reason,
            content_hash=self._content_hash(payload),
            head_event_id=result.event.event_id,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def _close_episode(
        self,
        scope: MemoryScopeContext,
        actor_id: str,
        row: ConversationEpisode,
        *,
        source_message_refs: list[str],
        boundary_reason: str,
        idempotency_key: str,
    ) -> ConversationEpisode:
        if row.status != "open":
            raise AppError(409, "episode_not_open", "Episode is not open")
        payload = {
            "episode_id": row.id,
            "conversation_id": row.conversation_id,
            "task_id": row.task_id,
            "title": row.title,
            "summary": row.summary,
            "decisions": row.decisions_json,
            "open_questions": row.open_questions_json,
            "constraints": row.constraints_json,
            "entities": row.entities_json,
            "source_message_refs": list(dict.fromkeys(row.source_message_refs_json + source_message_refs)),
            "boundary_reason": boundary_reason,
            "status": "closed",
        }
        result = self.store.append(
            scope,
            aggregate_type="episode",
            aggregate_id=row.id,
            expected_version=row.stream_version,
            event=AppendEvent(
                event_type=MemoryEventType.EPISODE_CLOSED,
                payload=payload,
                idempotency_key=idempotency_key,
                actor_id=actor_id,
                conversation_id=row.conversation_id,
            ),
            outbox_kinds=("index",),
        )
        if result.idempotent_replay:
            return row
        row.stream_version = result.event.stream_version
        row.source_message_refs_json = payload["source_message_refs"]
        row.source_event_refs_json = list(
            dict.fromkeys(row.source_event_refs_json + [result.event.event_id])
        )
        row.end_event_position = result.event.global_position
        row.ended_at = result.event.occurred_at
        row.status = "closed"
        row.boundary_reason = boundary_reason
        row.content_hash = self._content_hash(payload)
        row.head_event_id = result.event.event_id
        self.db.flush()
        return row

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
    def _content_hash(payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

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
