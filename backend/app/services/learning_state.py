from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.domain.memory_event_models import LearningNodeState, MemoryScopeContext, utc_now
from app.domain.models import Evidence


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes while freshly created rows are aware."""

    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


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
                or_(
                    Evidence.status.in_(("accepted", "approved", "active")),
                    # 练习错答是系统自动判分的结果，不需要人工确认，必须计入掌握
                    # 投影：否则答错既不拉低掌握分、也不进 misconceptions，只在会话
                    # 报告里体现，掌握状态会明显偏乐观。
                    and_(
                        Evidence.source_type == "exercise",
                        Evidence.result == "incorrect",
                    ),
                ),
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
            if last_assessed is None or (_as_utc(item.updated_at) or utc_now()) > (
                _as_utc(last_assessed) or utc_now()
            ):
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
        last_assessed_utc = _as_utc(last_assessed)
        if (
            last_assessed_utc is not None
            and (now - last_assessed_utc).days >= 14
            and status in {"mastered", "familiar"}
        ):
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
                stream_version=0,
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
