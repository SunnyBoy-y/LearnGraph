from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-learning-tests-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'learning.db').as_posix()}"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.migrations import apply_schema_migrations  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import (  # noqa: E402
    LearningNodeState,
    MemoryScopeContext,
    utc_now,
)
from app.domain.models import Evidence, GraphNode, utc_now as model_utc_now  # noqa: E402
from app.services.learning_state import LearningStateProjector  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_schema_migrations(conn)
    yield


def scope(*, tenant: str = "t1", user: str = "u1", workspace: str = "w1") -> MemoryScopeContext:
    return MemoryScopeContext(
        tenant_id=tenant,
        principal_user_id=user,
        workspace_id=workspace,
    )


def _add_evidence(
    db,
    *,
    evidence_id: str,
    node_id: str,
    score: float = 0.0,
    confidence: float = 0.7,
    difficulty: float = 0.0,
    assistance_level: float = 0.0,
    result: str = "correct",
    validity_status: str = "active",
    status: str = "accepted",
) -> Evidence:
    row = Evidence(
        id=evidence_id,
        workspace_id="w1",
        node_id=node_id,
        score=score,
        confidence=confidence,
        difficulty=difficulty,
        assistance_level=assistance_level,
        result=result,
        validity_status=validity_status,
        status=status,
        source_type="exercise",
        summary="test",
    )
    db.add(row)
    db.commit()
    return row


def test_no_evidence_yields_unseen():
    with SessionLocal() as db:
        result = LearningStateProjector(db).rebuild_node(
            scope(), "node-1", head_event_id="evt-1"
        )
        assert result.state.status == "unseen"
        assert result.state.mastery_score == 0.0
        assert result.changed is True


def test_high_evidence_produces_mastered():
    with SessionLocal() as db:
        for i in range(5):
            _add_evidence(db, evidence_id=f"ev-{i}", node_id="node-1", score=0.9, confidence=0.9, difficulty=0.8)
        result = LearningStateProjector(db).rebuild_node(
            scope(), "node-1", head_event_id="evt-1"
        )
        assert result.state.status == "mastered"
        assert result.state.mastery_score >= 0.85


def test_misconceptions_produce_weak():
    with SessionLocal() as db:
        _add_evidence(db, evidence_id="ev-1", node_id="node-1", score=0.0, result="incorrect")
        result = LearningStateProjector(db).rebuild_node(
            scope(), "node-1", head_event_id="evt-1"
        )
        assert result.state.status == "weak"
        assert len(result.state.misconceptions_json) >= 1


def test_evidence_weight_discounts_assistance():
    with SessionLocal() as db:
        _add_evidence(db, evidence_id="ev-1", node_id="node-1", score=1.0, confidence=0.9, assistance_level=0.0)
        _add_evidence(db, evidence_id="ev-2", node_id="node-1", score=1.0, confidence=0.9, assistance_level=0.8)
        result = LearningStateProjector(db).rebuild_node(
            scope(), "node-1", head_event_id="evt-1"
        )
        # 2 evidences give confidence=0.4 (below 0.7) so threshold yields familiar
        assert result.state.status == "familiar"
        assert result.state.mastery_score > 0.0


def test_inactive_evidence_is_excluded():
    with SessionLocal() as db:
        _add_evidence(db, evidence_id="ev-1", node_id="node-1", score=1.0, confidence=0.9, validity_status="invalidated")
        result = LearningStateProjector(db).rebuild_node(
            scope(), "node-1", head_event_id="evt-1"
        )
        assert result.state.status == "unseen"


def test_rebuild_is_idempotent():
    with SessionLocal() as db:
        _add_evidence(db, evidence_id="ev-1", node_id="node-1", score=1.0, confidence=0.9, difficulty=0.8)
        first = LearningStateProjector(db).rebuild_node(
            scope(), "node-1", head_event_id="evt-1"
        )
        second = LearningStateProjector(db).rebuild_node(
            scope(), "node-1", head_event_id="evt-1"
        )
        assert first.changed is True
        assert second.changed is False
        assert first.state.status == second.state.status
        assert first.state.mastery_score == second.state.mastery_score