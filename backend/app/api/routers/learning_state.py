from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.api.memory_deps import event_store, memory_scope
from app.domain.memory_event_models import LearningNodeState
from app.domain.memory_event_types import MemoryEventType
from app.domain.models import Evidence
from app.domain.schemas.learning_state import LearningEvidenceRequest, LearningNodeStateView
from app.services.learning_state import LearningStateProjector
from app.services.memory_event_store import AppendEvent


router = APIRouter(prefix="/learning", tags=["learning-state"])


@router.post("/evidence", response_model=LearningNodeStateView)
def record_learning_evidence(
    payload: LearningEvidenceRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> LearningNodeStateView:
    scope = memory_scope(context, node_ids=(payload.node_id,))
    event = event_store(db, settings).append(
        scope,
        aggregate_type="learning_node",
        aggregate_id=payload.node_id,
        expected_version=None,
        event=AppendEvent(
            event_type=MemoryEventType.LEARNING_EVIDENCE_RECORDED,
            payload=payload.model_dump(),
            idempotency_key=payload.idempotency_key,
            actor_id=context.principal.user_id,
            knowledge_node_id=payload.node_id,
        ),
    ).event
    existing = db.scalar(
        select(Evidence).where(
            Evidence.workspace_id == context.workspace.id,
            Evidence.source_ref == payload.source_ref,
            Evidence.source_content_hash == payload.source_content_hash,
            Evidence.node_id == payload.node_id,
        )
    )
    if existing is None:
        existing = Evidence(
            workspace_id=context.workspace.id,
            node_id=payload.node_id,
            source_type=payload.source_type,
            summary=payload.summary,
            confidence=payload.confidence,
            status="accepted",
            metadata_json=payload.metadata,
            result=payload.result,
            difficulty=payload.difficulty,
            assistance_level=payload.assistance_level,
            score=payload.score,
            source_ref=payload.source_ref,
            source_version_id=payload.source_version_id,
            source_content_hash=payload.source_content_hash,
            validity_status="active",
        )
        db.add(existing)
        db.flush()
    state = LearningStateProjector(db).rebuild_node(
        scope, payload.node_id, head_event_id=event.event_id
    ).state
    db.commit()
    return _view(state)


@router.get("/nodes/{node_id}/state", response_model=LearningNodeStateView)
def get_learning_state(
    node_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> LearningNodeStateView:
    state = db.scalar(
        select(LearningNodeState).where(
            LearningNodeState.tenant_id == context.principal.tenant_id,
            LearningNodeState.subject_user_id == context.principal.user_id,
            LearningNodeState.workspace_id == context.workspace.id,
            LearningNodeState.knowledge_node_id == node_id,
        )
    )
    if state is None:
        from app.core.errors import AppError
        raise AppError(404, "learning_state_not_found", "Learning state was not found")
    return _view(state)


def _view(state: LearningNodeState) -> LearningNodeStateView:
    return LearningNodeStateView(
        node_id=state.knowledge_node_id,
        status=state.status,
        mastery_score=state.mastery_score,
        confidence=state.confidence,
        evidence_count=state.evidence_count,
        misconceptions=state.misconceptions_json,
        last_assessed_at=state.last_assessed_at,
        next_review_at=state.next_review_at,
        evidence_ids=state.source_evidence_ids_json,
        algorithm_version=state.algorithm_version,
    )
