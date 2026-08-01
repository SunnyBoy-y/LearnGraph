from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.api.memory_deps import event_store, memory_scope
from app.core.errors import AppError
from app.domain.models import ChatSession
from app.domain.schemas.memory_tasks import (
    EpisodeCloseRequest,
    EpisodeGenerateRequest,
    EpisodeObservationView,
    EpisodeObserveRequest,
    EpisodeSearchRequest,
    EpisodeView,
)
from app.services.memory_episodes import MemoryEpisodeService
from app.services.authorization import AuthorizationService
from app.services.episode_boundary import BoundaryInputs


router = APIRouter(prefix="/episodes", tags=["memory-episodes"])


def _require_episode_enabled(settings: AppSettings) -> None:
    if not settings.memory_task_episode_enabled:
        raise AppError(
            404,
            "memory_episode_feature_disabled",
            "Episode lifecycle is disabled",
        )


def _require_conversation_access(
    db: DB, context: CurrentWorkspace, conversation_id: str, permission: str
) -> None:
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.id == conversation_id,
            ChatSession.workspace_id == context.workspace.id,
        )
    )
    if session is None or not AuthorizationService(
        db, context.principal
    ).can_access_resource(context.workspace, "session", session.id, permission):
        raise AppError(404, "conversation_not_found", "Conversation was not found")


@router.post("/generate", response_model=EpisodeView)
def generate_episode(
    payload: EpisodeGenerateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> EpisodeView:
    _require_episode_enabled(settings)
    _require_conversation_access(db, context, payload.conversation_id, "write")
    return MemoryEpisodeService(db, event_store(db, settings)).generate(
        memory_scope(
            context,
            task_id=payload.task_id,
            conversation_id=payload.conversation_id,
        ),
        context.principal.user_id,
        payload,
    )


@router.post("/observe", response_model=EpisodeObservationView)
def observe_episode(
    payload: EpisodeObserveRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> EpisodeObservationView:
    _require_episode_enabled(settings)
    _require_conversation_access(db, context, payload.conversation_id, "write")
    result = MemoryEpisodeService(db, event_store(db, settings)).observe_and_advance(
        memory_scope(
            context,
            task_id=payload.task_id,
            conversation_id=payload.conversation_id,
        ),
        context.principal.user_id,
        conversation_id=payload.conversation_id,
        source_message_refs=payload.source_message_refs,
        inputs=BoundaryInputs(
            explicit_topic_switch=payload.explicit_topic_switch,
            task_stage_completed=payload.task_stage_completed,
            conversation_closed=payload.conversation_closed,
        ),
        idempotency_key=payload.idempotency_key,
        task_id=payload.task_id,
    )
    return EpisodeObservationView(
        boundary_detected=result.boundary_detected,
        boundary_reason=result.boundary_reason,
        opened_episode=result.opened_episode,
        closed_episode=result.closed_episode,
    )


@router.post("/{episode_id}/close", response_model=EpisodeView)
def close_episode(
    episode_id: str,
    payload: EpisodeCloseRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> EpisodeView:
    _require_episode_enabled(settings)
    service = MemoryEpisodeService(db, event_store(db, settings))
    # The service repeats scope filtering to avoid bare-ID enumeration; looking
    # up the scoped open row here obtains its conversation for route ACL.
    row = service._require_open_episode(memory_scope(context), episode_id)
    _require_conversation_access(db, context, row.conversation_id, "write")
    return service.close(memory_scope(context, task_id=row.task_id), context.principal.user_id, episode_id, payload)


@router.post("/search", response_model=list[EpisodeView])
def search_episodes(
    payload: EpisodeSearchRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[EpisodeView]:
    if payload.conversation_id:
        session = db.scalar(
            select(ChatSession).where(
                ChatSession.id == payload.conversation_id,
                ChatSession.workspace_id == context.workspace.id,
            )
        )
        if session is None or not AuthorizationService(
            db, context.principal
        ).can_access_resource(context.workspace, "session", session.id, "read"):
            raise AppError(404, "conversation_not_found", "Conversation was not found")
    return MemoryEpisodeService(db, event_store(db, settings)).search(
        memory_scope(
            context,
            task_id=payload.task_id,
            conversation_id=payload.conversation_id,
        ),
        payload,
    )
