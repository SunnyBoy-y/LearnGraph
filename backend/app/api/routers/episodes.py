from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.api.memory_deps import event_store, memory_scope
from app.core.errors import AppError
from app.domain.models import ChatSession
from app.domain.schemas.memory_tasks import (
    EpisodeGenerateRequest,
    EpisodeSearchRequest,
    EpisodeView,
)
from app.services.memory_episodes import MemoryEpisodeService
from app.services.authorization import AuthorizationService


router = APIRouter(prefix="/episodes", tags=["memory-episodes"])


@router.post("/generate", response_model=EpisodeView)
def generate_episode(
    payload: EpisodeGenerateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> EpisodeView:
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.id == payload.conversation_id,
            ChatSession.workspace_id == context.workspace.id,
        )
    )
    if session is None or not AuthorizationService(
        db, context.principal
    ).can_access_resource(context.workspace, "session", session.id, "write"):
        raise AppError(404, "conversation_not_found", "Conversation was not found")
    return MemoryEpisodeService(db, event_store(db, settings)).generate(
        memory_scope(
            context,
            task_id=payload.task_id,
            conversation_id=payload.conversation_id,
        ),
        context.principal.user_id,
        payload,
    )


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
