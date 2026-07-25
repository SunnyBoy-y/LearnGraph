from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.management import (
    EffectiveMemoryPackageView,
    MemoryBindingView,
    MemoryCreateRequest,
    MemoryDraftCreateRequest,
    MemoryDraftDecisionRequest,
    MemoryDraftView,
    MemoryJournalView,
    MemoryPolicyUpdateRequest,
    MemoryPolicyView,
    MemoryProviderStatusView,
    MemoryRevisionRestoreRequest,
    MemoryRevisionView,
    MemoryTypeDefinitionView,
    MemoryUpdateRequest,
    MemoryView,
)
from app.providers.factory import memory_provider_for_workspace
from app.services.memory import MemoryService


router = APIRouter(prefix="/memory", tags=["memory"])


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> MemoryService:
    return MemoryService(
        db,
        context.workspace,
        context.principal.user_id,
        memory_provider_for_workspace(
            db,
            context.workspace,
            context.principal.user_id,
            settings,
        ),
        settings.memory_root,
    )


@router.get("", response_model=list[MemoryView])
def list_memories(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    zone: Annotated[
        Literal["hot", "recent", "topics", "archive"] | None,
        Query(),
    ] = None,
    state: Annotated[
        Literal["active", "deleted", "destroyed"],
        Query(),
    ] = "active",
    namespace: Annotated[
        Literal["workspace", "session"] | None,
        Query(),
    ] = None,
    session_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
) -> list[MemoryView]:
    return service(db, context, settings).list(
        zone=zone,
        state=state,
        namespace=namespace,
        session_id=session_id,
    )


@router.get("/types", response_model=list[MemoryTypeDefinitionView])
def list_memory_types(
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> list[MemoryTypeDefinitionView]:
    return service(db, context, settings).list_memory_types()


@router.get("/export")
def export_memories(db: DB, context: CurrentWorkspace, settings: AppSettings) -> Response:
    payload = service(db, context, settings).export_markdown()
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="learngraph-memory-export.zip"',
        },
    )


@router.get("/policy", response_model=MemoryPolicyView)
def get_memory_policy(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    session_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
) -> MemoryPolicyView:
    return service(db, context, settings).policy(session_id)


@router.put("/policy", response_model=MemoryPolicyView)
def update_memory_policy(
    payload: MemoryPolicyUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryPolicyView:
    return service(db, context, settings).update_policy(payload)


@router.get("/provider", response_model=MemoryProviderStatusView)
def get_memory_provider(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryProviderStatusView:
    return service(db, context, settings).provider_status()


@router.post("/provider/probe", response_model=MemoryProviderStatusView)
def probe_memory_provider(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryProviderStatusView:
    return service(db, context, settings).provider_status(probe=True)


@router.post("/maintenance/purge-expired")
def purge_expired_memory_content(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict[str, int]:
    return service(db, context, settings).purge_expired()


@router.get("/package", response_model=EffectiveMemoryPackageView)
def get_effective_memory_package(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    session_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    goal_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    node_id: Annotated[list[str] | None, Query()] = None,
) -> EffectiveMemoryPackageView:
    return service(db, context, settings).effective_memory_package(
        session_id=session_id,
        goal_id=goal_id,
        node_ids=list(node_id or []),
        require_provider_health=False,
        mark_access=False,
    )


@router.get("/drafts", response_model=list[MemoryDraftView])
def list_memory_drafts(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    status_filter: Annotated[
        Literal["PENDING", "COMMITTED", "REJECTED", "CANCELLED"] | None,
        Query(alias="status"),
    ] = "PENDING",
    session_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    goal_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
) -> list[MemoryDraftView]:
    return service(db, context, settings).list_drafts(
        status=status_filter,
        session_id=session_id,
        goal_id=goal_id,
    )


@router.post("/drafts", response_model=MemoryDraftView, status_code=status.HTTP_201_CREATED)
def create_memory_draft(
    payload: MemoryDraftCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryDraftView:
    return service(db, context, settings).create_draft(payload)


@router.get("/drafts/{draft_id}", response_model=MemoryDraftView)
def get_memory_draft(
    draft_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryDraftView:
    return service(db, context, settings).get_draft(draft_id)


@router.post("/drafts/{draft_id}/decision", response_model=MemoryDraftView)
def decide_memory_draft(
    draft_id: str,
    payload: MemoryDraftDecisionRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryDraftView:
    return service(db, context, settings).decide_draft(draft_id, payload)


@router.get("/goal-overview/{target_goal_id}")
def goal_memory_overview(
    target_goal_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict[str, str]:
    text = service(db, context, settings).build_goal_overview(target_goal_id)
    return {"goal_id": target_goal_id, "overview_markdown": text}


@router.post("/goal-archive/{target_goal_id}")
def archive_goal_memories(
    target_goal_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict[str, int]:
    return service(db, context, settings).archive_goal_memories(target_goal_id)


@router.post("/{memory_id}/resolution", response_model=MemoryView)
def resolve_memory(
    memory_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    resolution_status: Annotated[
        Literal["none", "active_misconception", "improving", "resolved", "recurring"],
        Query(),
    ],
    evidence_note: Annotated[str, Query(max_length=500)] = "",
) -> MemoryView:
    return service(db, context, settings).resolve_misconception(
        memory_id,
        resolution_status=resolution_status,
        evidence_note=evidence_note,
    )


@router.get("/{memory_id}/evidence")
def get_memory_evidence(
    memory_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    return service(db, context, settings).get_memory_evidence(memory_id)


@router.get("/{memory_id}", response_model=MemoryView)
def get_memory(
    memory_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryView:
    return service(db, context, settings).get(memory_id)


@router.post("", response_model=MemoryView, status_code=status.HTTP_201_CREATED)
def create_memory(
    payload: MemoryCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryView:
    return service(db, context, settings).create(payload)


@router.patch("/{memory_id}", response_model=MemoryView)
def update_memory(
    memory_id: str,
    payload: MemoryUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryView:
    return service(db, context, settings).update(memory_id, payload)


@router.get("/{memory_id}/revisions", response_model=list[MemoryRevisionView])
def list_memory_revisions(
    memory_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[MemoryRevisionView]:
    return service(db, context, settings).list_revisions(memory_id)


@router.post(
    "/{memory_id}/revisions/{revision}/restore",
    response_model=MemoryView,
)
def restore_memory_revision(
    memory_id: str,
    revision: int,
    payload: MemoryRevisionRestoreRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryView:
    return service(db, context, settings).restore_revision(memory_id, revision, payload)


@router.get("/{memory_id}/journal", response_model=list[MemoryJournalView])
def list_memory_journal(
    memory_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[MemoryJournalView]:
    return service(db, context, settings).list_journal(memory_id)


@router.get("/{memory_id}/bindings", response_model=list[MemoryBindingView])
def list_memory_bindings(
    memory_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[MemoryBindingView]:
    return service(db, context, settings).list_bindings(memory_id)


@router.delete("/{memory_id}", response_model=MemoryView)
def delete_memory(
    memory_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryView:
    return service(db, context, settings).delete(memory_id)


@router.post("/{memory_id}/restore", response_model=MemoryView)
def restore_deleted_memory(
    memory_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryView:
    return service(db, context, settings).restore_deleted(memory_id)
