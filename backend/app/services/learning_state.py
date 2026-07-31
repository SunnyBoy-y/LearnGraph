from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.memory_event_models import LearningNodeState, MemoryScopeContext, utc_now
from app.domain.models import Evidence


@dataclass(frozen=True, slots=True)
class LearningStateResult:
    state: LearningNodeState
    changed: bool


class LearningStateProjector:
    VERSION = "learning-state-v1"

    def __init__(self, db: Session) -> None:
        self.db = db

    def rebuild_node(
        self, scope: MemoryScopeContext, knowledge_node_id: str, *, head_event_id: str
    ) -> LearningStateResult:
        evidence = self.db.scalars(
            select(Evidence).where(
                Evidence.workspace_id == scope.workspace_id,
                Evidence.node_id == knowledge_node_id,
                Evidence.validity_status == "active",
                Evidence.status.in_(("accepted", "approved", "active")),
            )
        ).all()
        weighted = 0.0
        total_weight = 0.0
        misconceptions: list[dict[str, str]] = []
        last_assessed = None
        source_ids: list[str] = []
        for item in evidence:
            weight = max(0.05, float(item.confidence))
            weight *= 1.0 + max(0.0, float(item.difficulty))
            weight *= 1.0 - min(0.9, max(0.0, float(item.assistance_level)))
            result_score = item.score
            if result_score is None:
                result_score = 1.0 if item.result in {"correct", "passed", "success"} else 0.0
            weighted += max(0.0, min(1.0, float(result_score))) * weight
            total_weight += weight
            source_ids.append(item.id)
            if item.result in {"incorrect", "failed", "misconception"}:
                misconceptions.append({"evidence_id": item.id, "summary": item.summary[:240]})
            if last_assessed is None or item.updated_at > last_assessed:
                last_assessed = item.updated_at
        score = weighted / total_weight if total_weight else 0.0
        confidence = min(1.0, total_weight / 5.0)
        if not evidence:
            status = "unseen"
        elif misconceptions and score < 0.5:
            status = "weak"
        elif score >= 0.85 and confidence >= 0.7:
            status = "mastered"
        elif score >= 0.65:
            status = "familiar"
        else:
            status = "learning"
        now = utc_now()
        if last_assessed is not None and (now - last_assessed).days >= 14 and status in {"mastered", "familiar"}:
            status = "needs_review"
        state = self.db.scalar(
            select(LearningNodeState).where(
                LearningNodeState.tenant_id == scope.tenant_id,
                LearningNodeState.subject_user_id == scope.principal_user_id,
                LearningNodeState.workspace_id == scope.workspace_id,
                LearningNodeState.knowledge_node_id == knowledge_node_id,
            )
        )
        changed = False
        if state is None:
            state = LearningNodeState(
                tenant_id=scope.tenant_id,
                subject_user_id=scope.principal_user_id,
                workspace_id=scope.workspace_id,
                knowledge_node_id=knowledge_node_id,
                head_event_id=head_event_id,
            )
            self.db.add(state)
            changed = True
        old = (state.status, state.mastery_score, state.confidence, state.evidence_count)
        state.status = status
        state.mastery_score = score
        state.confidence = confidence
        state.evidence_count = len(evidence)
        state.misconceptions_json = misconceptions
        state.last_assessed_at = last_assessed
        state.next_review_at = now + timedelta(days=14 if status == "mastered" else 3)
        state.source_evidence_ids_json = source_ids
        state.algorithm_version = self.VERSION
        state.stream_version += 1
        state.head_event_id = head_event_id
        changed = changed or old != (status, score, confidence, len(evidence))
        self.db.flush()
        return LearningStateResult(state, changed)
