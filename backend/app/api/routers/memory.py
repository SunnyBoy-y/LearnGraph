from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.models import ContextSummary
from app.domain.schemas.management import (
    ContextSummaryView,
    EffectiveMemoryPackageView,
    MemoryBindingView,
    MemoryCreateRequest,
    MemoryDraftCreateRequest,
    MemoryDraftDecisionRequest,
    MemoryDraftView,
    MemoryEmbeddingSettingsView,
    MemoryEnhancementUpdateRequest,
    MemoryEnhancementView,
    MemoryExtractionSettingsView,
    MemoryJournalView,
    MemoryPolicyUpdateRequest,
    MemoryPolicyView,
    MemoryProfileIntentRequest,
    MemoryProfileIntentResult,
    MemoryProfileView,
    MemoryProviderStatusView,
    MemoryRevisionRestoreRequest,
    MemoryRevisionView,
    MemorySummarizationSettingsView,
    MemoryTypeDefinitionView,
    MemoryUpdateRequest,
    MemoryView,
)
from app.providers.factory import memory_provider_for_workspace
from app.services.memory import MemoryService
from app.services.memory_enhancement import (
    embedding_index_status,
    extract_session_memories,
    load_enhancement_config,
    prune_stale_embeddings,
    reindex_memory_embeddings,
    save_enhancement_config,
    summarize_session_context,
)
from app.services.memory_profile import (
    MemoryProfileService,
    reconcile_workspace_temporal_atoms,
)


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


def profile_service(
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> MemoryProfileService:
    return MemoryProfileService(
        db,
        context.workspace,
        context.principal.user_id,
        settings,
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
    include_content: Annotated[bool, Query()] = False,
) -> list[MemoryView]:
    return service(db, context, settings).list(
        zone=zone,
        state=state,
        namespace=namespace,
        session_id=session_id,
        include_content=include_content,
    )


@router.get("/types", response_model=list[MemoryTypeDefinitionView])
def list_memory_types(
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> list[MemoryTypeDefinitionView]:
    return service(db, context, settings).list_memory_types()


@router.get("/views", response_model=list[MemoryView])
def list_memory_views(
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
    include_content: Annotated[bool, Query()] = False,
) -> list[MemoryView]:
    return service(db, context, settings).list_views(
        zone=zone,
        state=state,
        include_content=include_content,
    )


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


@router.post("/maintenance/migrate-provider")
def migrate_memory_provider_generation(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, int]:
    return service(db, context, settings).migrate_provider_generation(limit=limit)


@router.post("/maintenance/reconcile-time")
def reconcile_memory_time(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict[str, int]:
    return reconcile_workspace_temporal_atoms(
        db,
        context.workspace,
        settings,
    )


@router.post("/maintenance/reconcile-zones")
def reconcile_memory_zones(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict[str, int]:
    """Recompute cold/hot layering for v1 records and v2 search projections."""

    report = service(db, context, settings).reconcile_zones()
    return {
        "reviewed": report.reviewed,
        "changed": report.changed,
        "archived": report.archived,
        "hot": report.hot,
        "recent": report.recent,
        "topics": report.topics,
    }


@router.post("/maintenance/migrate-atoms")
def migrate_legacy_memory_atoms(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, int]:
    return profile_service(db, context, settings).migrate_legacy_atoms(
        limit=limit
    )


@router.get("/profile", response_model=MemoryProfileView)
def get_memory_profile(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryProfileView:
    return profile_service(db, context, settings).get_profile()


@router.get("/profile/sources")
def get_memory_profile_sources(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    return profile_service(db, context, settings).profile_sources()


@router.post("/profile/refresh", response_model=MemoryProfileView)
def refresh_memory_profile(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryProfileView:
    return profile_service(db, context, settings).refresh_profile(force=True)


@router.post("/profile/intents", response_model=MemoryProfileIntentResult)
def apply_memory_profile_intent(
    payload: MemoryProfileIntentRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryProfileIntentResult:
    return profile_service(db, context, settings).apply_intent(payload)


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
        mark_access=False,
    )


def _enhancement_view(
    db: DB,
    workspace_id: str,
    *,
    cache_invalidated: dict[str, Any] | None = None,
) -> MemoryEnhancementView:
    config = load_enhancement_config(db, workspace_id)
    stats = embedding_index_status(db, workspace_id)
    return MemoryEnhancementView(
        workspace_id=workspace_id,
        extraction=MemoryExtractionSettingsView(**config["extraction"]),
        embedding=MemoryEmbeddingSettingsView(**config["embedding"]),
        summarization=MemorySummarizationSettingsView(**config["summarization"]),
        active_memories=stats["active_memories"],
        indexed_memories=stats["indexed_memories"],
        current_model_key=stats["current_model_key"],
        stale_model_keys=stats["stale_model_keys"],
        cache_invalidated=cache_invalidated,
    )


@router.get("/enhancement", response_model=MemoryEnhancementView)
def get_memory_enhancement(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryEnhancementView:
    return _enhancement_view(db, context.workspace_id)


@router.put("/enhancement", response_model=MemoryEnhancementView)
def update_memory_enhancement(
    payload: MemoryEnhancementUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryEnhancementView:
    _config, cache_invalidated = save_enhancement_config(
        db,
        context.workspace_id,
        payload.model_dump(exclude_none=True),
    )
    return _enhancement_view(
        db, context.workspace_id, cache_invalidated=cache_invalidated
    )


@router.post("/enhancement/reindex")
def reindex_memory_embedding_index(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    prune_stale: Annotated[bool, Query()] = False,
) -> dict:
    return reindex_memory_embeddings(
        db, context.workspace_id, settings, prune_stale=prune_stale
    )


@router.post("/enhancement/prune-embeddings")
def prune_memory_embedding_cache(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    """Delete cached vectors for embedding models other than the configured one."""
    return prune_stale_embeddings(db, context.workspace_id)


@router.post("/enhancement/extract/{session_id}")
def extract_memories_now(
    session_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    return extract_session_memories(
        db,
        context.workspace,
        session_id,
        settings,
        actor_id=context.principal.user_id,
        force=True,
    )


@router.get("/enhancement/summarize/{session_id}", response_model=ContextSummaryView | None)
def get_session_context_summary(
    session_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ContextSummaryView | None:
    row = db.scalar(
        select(ContextSummary)
        .where(
            ContextSummary.workspace_id == context.workspace_id,
            ContextSummary.session_id == session_id,
            ContextSummary.kind == "model",
        )
        .order_by(ContextSummary.version.desc())
        .limit(1)
    )
    if row is None:
        return None
    return ContextSummaryView.model_validate(row)


@router.post("/enhancement/summarize/{session_id}")
def summarize_session_now(
    session_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    return summarize_session_context(
        db,
        context.workspace,
        session_id,
        settings,
        actor_id=context.principal.user_id,
        force=True,
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
    """List memory drafts (INTERNAL API).

    Memory drafts are the human-confirmation gate of the memory extraction
    pipeline: agents/extraction only *propose* drafts, and only committed
    drafts become MemoryRecord entries (mirrored into the v2 event stream).

    This endpoint family is primarily consumed by the extraction pipeline and
    memory governance tooling. The frontend confirmation surface should read
    committed/active memories via ``/memory`` (state=active) and resolve any
    PENDING drafts through ``/drafts/{id}/decision``; do not expose raw draft
    CRUD as a general-purpose UI contract.
    """
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
    """Create a memory draft (INTERNAL API — used by the extraction pipeline)."""
    return service(db, context, settings).create_draft(payload)


@router.get("/drafts/{draft_id}", response_model=MemoryDraftView)
def get_memory_draft(
    draft_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryDraftView:
    """Get a single memory draft (INTERNAL API)."""
    return service(db, context, settings).get_draft(draft_id)


@router.post("/drafts/{draft_id}/decision", response_model=MemoryDraftView)
def decide_memory_draft(
    draft_id: str,
    payload: MemoryDraftDecisionRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MemoryDraftView:
    """Decide a pending memory draft (INTERNAL API).

    ``commit`` materialises the draft into a MemoryRecord (and mirrors the
    event into the v2 stream); ``reject`` marks it REJECTED. Used by the
    extraction pipeline and the pending-confirmation governance surface.
    """
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
