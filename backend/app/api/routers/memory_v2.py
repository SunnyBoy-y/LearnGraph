from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.api.memory_deps import event_store, memory_scope
from app.core.errors import AppError
from app.domain.models import ChatSession, MemoryRecord
from app.domain.schemas.context_builds import ContextBuildRequest, ContextBuildView
from app.domain.schemas.memory_v2 import (
    MemoryEventAppendRequest,
    MemoryEventView,
    MemoryFeedbackRequest,
    MemoryForgetRequest,
)
from app.services.context_builder import ContextBuilder
from app.services.context_telemetry import ContextTelemetryWriter
from app.services.memory_commands import MemoryCommandService
from app.services.memory_cutover import MemoryCutoverService
from app.services.memory_event_ingestor import EventActor, MemoryEventIngestor
from app.services.memory_projector import MemoryProjector
from app.services.memory_retrieval import MemoryHybridRetriever
from app.services.memory_router import MemoryRouter
from app.services.authorization import AuthorizationService


router = APIRouter(prefix="/memory", tags=["memory-v2"])


def _allowed_sensitivity(payload: ContextBuildRequest) -> frozenset[str]:
    permitted = {"public", "normal", "private"}
    return frozenset(value for value in payload.allowed_sensitivity if value in permitted)


@router.get("/architecture/status")
def architecture_status(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    context.require_permission("workspace.manage")
    return MemoryCutoverService(db, settings).architecture_status()


@router.post("/maintenance/replay-validate")
def replay_validate(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    context.require_permission("workspace.manage")
    report = MemoryCutoverService(db, settings).replay_validate(context.workspace.id)
    return asdict(report)


@router.post("/export/events")
def export_event_manifest(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    context.require_permission("workspace.manage")
    return MemoryCutoverService(db, settings).export_manifest(context.workspace.id)


@router.post("/events", response_model=MemoryEventView)
def append_event(
    payload: MemoryEventAppendRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryEventView:
    context.require_permission("workspace.manage")
    scope = memory_scope(
        context,
        task_id=payload.scope.task_id,
        conversation_id=payload.conversation_id,
    )
    # Client-supplied identity fields are assertions only; authority comes from
    # bearer principal + X-Workspace-ID.
    if payload.scope.tenant_id and payload.scope.tenant_id != scope.tenant_id:
        raise AppError(403, "memory_scope_mismatch", "Event tenant does not match principal")
    if payload.scope.workspace_id and payload.scope.workspace_id != scope.workspace_id:
        raise AppError(403, "memory_scope_mismatch", "Event workspace does not match principal")
    if payload.scope.subject_user_id and payload.scope.subject_user_id != scope.principal_user_id:
        raise AppError(403, "memory_scope_mismatch", "Event subject does not match principal")
    result = MemoryEventIngestor(event_store(db, settings)).ingest(
        scope,
        EventActor("user", context.principal.user_id),
        payload,
    )
    MemoryProjector(db).apply(result.event, payload.payload)
    db.commit()
    return MemoryEventView(
        event_id=result.event.event_id,
        stream_id=result.event.stream_id,
        stream_version=result.event.stream_version,
        global_position=result.event.global_position,
        event_type=result.event.event_type,
        event_schema_version=result.event.event_schema_version,
        payload_hash=result.event.payload_hash,
        occurred_at=result.event.occurred_at,
        ingested_at=result.event.ingested_at,
        idempotent_replay=result.idempotent_replay,
    )


@router.post("/search")
def search_memory(
    payload: ContextBuildRequest,
    db: DB,
    context: CurrentWorkspace,
) -> dict:
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
    scope = memory_scope(
        context,
        task_id=payload.task_id,
        conversation_id=payload.conversation_id,
        agent_id=payload.agent_id,
        allowed_sensitivity=_allowed_sensitivity(payload),
    )
    routed = MemoryRouter(MemoryHybridRetriever(db), db=db).route(scope, payload.query)
    return {
        "route": list(routed.routes),
        "candidates": [
            {
                "target_id": item.target_id,
                "target_type": item.target_type,
                "title": item.title,
                "content": item.content,
                "score": item.score,
                "component_scores": item.component_scores,
                "source_event_id": item.source_event_id,
            }
            for item in routed.retrieval.candidates
        ],
        "conflicts": [],
        "explanations": [],
        "excluded": routed.retrieval.excluded,
        "degraded_modes": list(routed.retrieval.degraded_modes),
        "policy_version": routed.policy_version,
    }


@router.post("/context/build", response_model=ContextBuildView)
def build_context(
    payload: ContextBuildRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ContextBuildView:
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
    scope = memory_scope(
        context,
        task_id=payload.task_id,
        conversation_id=payload.conversation_id,
        agent_id=payload.agent_id,
        allowed_sensitivity=_allowed_sensitivity(payload),
    )
    built = ContextBuilder(db, MemoryRouter(MemoryHybridRetriever(db), db=db)).build(scope, payload)
    ContextTelemetryWriter(db).write(scope, payload, built.view)
    return built.view


@router.post("/{memory_id}/feedback")
def record_feedback(
    memory_id: str,
    payload: MemoryFeedbackRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    record = db.scalar(
        select(MemoryRecord).where(
            MemoryRecord.id == memory_id,
            MemoryRecord.workspace_id == context.workspace.id,
        )
    )
    if record is None:
        raise AppError(404, "memory_not_found", "Memory was not found")
    if record.subject_user_id not in {None, context.principal.user_id}:
        raise AppError(404, "memory_not_found", "Memory was not found")
    if record.subject_user_id is None or payload.feedback_type == "project_only":
        context.require_permission("workspace.manage")
    row = MemoryCommandService(db, event_store(db, settings)).feedback(
        memory_scope(context),
        memory_id,
        actor_id=context.principal.user_id,
        feedback_type=payload.feedback_type,
        payload=payload.payload,
    )
    return {"feedback_id": row.id, "applied_event_id": row.applied_event_id}


@router.post("/{memory_id}/retract")
def retract_memory(
    memory_id: str,
    payload: MemoryFeedbackRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    context.require_permission("workspace.manage")
    record = MemoryCommandService(db, event_store(db, settings)).retract(
        memory_scope(context),
        memory_id,
        actor_id=context.principal.user_id,
        reason=str(payload.payload.get("reason") or "user_retracted"),
    )
    return {"memory_id": record.id, "lifecycle_status": record.lifecycle_status}


@router.post("/{memory_id}/forget")
def forget_memory(
    memory_id: str,
    payload: MemoryForgetRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    context.require_permission("workspace.manage")
    report = MemoryCommandService(db, event_store(db, settings)).forget(
        memory_scope(context),
        memory_id,
        actor_id=context.principal.user_id,
        confirmation=payload.confirmation,
        reason=payload.reason,
    )
    return asdict(report)


@router.post("/maintenance/backfill")
def backfill_memory_projection(
    db: DB,
    context: CurrentWorkspace,
) -> dict:
    context.require_permission("workspace.manage")
    projector = MemoryProjector(db)
    count = projector.backfill_legacy(
        tenant_id=context.principal.tenant_id,
        workspace_id=context.workspace.id,
    )
    db.commit()
    parity = projector.parity_report(context.workspace.id)
    return {"backfilled": count, "parity": asdict(parity)}


@router.post("/maintenance/rebuild-search")
def rebuild_search_projection(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    """Fully replay Event Store into memory_search_documents + FTS (manage only).

    Uses envelope decryption without principal scope filters so a workspace
    manager can rebuild the whole local projection from the event log. Failure
    rolls back both structured documents and FTS rows.
    """

    context.require_permission("workspace.manage")
    store = event_store(db, settings)
    projector = MemoryProjector(db, cipher=store.cipher)
    try:
        report = projector.rebuild_search_projection()
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "event_count": report.event_count,
        "applied_count": report.applied_count,
        "skipped_forgotten": report.skipped_forgotten,
        "skipped_non_memory": report.skipped_non_memory,
        "skipped_unavailable": report.skipped_unavailable,
        "document_count": report.document_count,
        "fts_row_count": report.fts_row_count,
        "content_fingerprint": report.content_fingerprint,
        "fts_capability": report.fts_capability,
    }
