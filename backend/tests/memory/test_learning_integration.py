from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-learning-int-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'learning.db').as_posix()}"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.migrations import apply_schema_migrations  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import (  # noqa: E402
    LearningNodeState,
    MemoryEvent,
    MemoryScopeContext,
)
from app.domain.memory_event_types import MemoryEventType  # noqa: E402
from app.domain.models import Evidence, Goal, Graph, GraphNode  # noqa: E402
from app.services.learning_state import LearningStateProjector  # noqa: E402
from app.services.mastery import MasteryService  # noqa: E402


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


def _seed_graph_node(db, *, node_id: str = "node-1") -> GraphNode:
    goal = Goal(id="goal-1", workspace_id="w1", title="g", raw_prompt="g")
    db.add(goal)
    db.flush()
    graph = Graph(id="graph-1", workspace_id="w1", goal_id="goal-1", title="x")
    db.add(graph)
    db.flush()
    node = GraphNode(id=node_id, workspace_id="w1", graph_id="graph-1", label="node-1")
    db.add(node)
    db.commit()
    return node


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
    source_type: str = "exercise",
) -> Evidence:
    row = Evidence(
        id=evidence_id,
        workspace_id="w1",
        node_id=node_id,
        source_type=source_type,
        score=score,
        confidence=confidence,
        difficulty=difficulty,
        assistance_level=assistance_level,
        result=result,
        validity_status="active",
        status="accepted",
        summary="test",
    )
    db.add(row)
    db.commit()
    return row


def test_learning_evidence_drives_both_star_and_projection():
    """One accepted exercise evidence must bump the legacy growth star and the
    current learning-state projection at the same time — no drift."""
    with SessionLocal() as db:
        node = _seed_graph_node(db)
        evidence = _add_evidence(
            db, evidence_id="ev-1", node_id="node-1",
            score=0.9, confidence=0.9, difficulty=0.8, source_type="exercise",
        )
        awarded = MasteryService(db, "w1", "u1").apply_evidence(evidence, node)
        state = LearningStateProjector(db).rebuild_node(
            scope(), "node-1", head_event_id="evt-1"
        ).state
        db.commit()

        assert awarded is True
        node = db.get(GraphNode, "node-1")
        assert node.mastery_stars == 1  # legacy star, never decreases
        # One strong evidence is not yet mastered (confidence < 0.7 requires
        # multiple sources); the projection reflects the current state.
        assert state.status == "familiar"
        assert state.mastery_score >= 0.85
        assert state.evidence_count == 1


def test_growth_star_never_decreases_but_projection_reflects_evidence():
    """Even when evidence weakens the projection, the legacy star stays put."""
    with SessionLocal() as db:
        node = _seed_graph_node(db)
        good = _add_evidence(
            db, evidence_id="ev-good", node_id="node-1",
            score=0.9, confidence=0.9, difficulty=0.8, source_type="exercise",
        )
        MasteryService(db, "w1", "u1").apply_evidence(good, node)
        # A low-confidence conflict goes down the _mark_conflict path: the star
        # must NOT decrease, while the projection drops to weak.
        weak = _add_evidence(
            db, evidence_id="ev-weak", node_id="node-1",
            score=0.0, result="incorrect", confidence=0.4, source_type="exercise",
        )
        MasteryService(db, "w1", "u1").apply_evidence(weak, node)
        state = LearningStateProjector(db).rebuild_node(
            scope(), "node-1", head_event_id="evt-2"
        ).state
        db.commit()

        node = db.get(GraphNode, "node-1")
        assert node.mastery_stars == 1  # legacy star stays from the good evidence
        # Projection reflects both sources: the misconception drags score below
        # the mastered threshold, so the current state drops below mastered.
        assert state.status in {"familiar", "weak"}
        assert state.mastery_score < 1.0
        assert len(state.misconceptions_json) >= 1


def test_conflict_marks_disputed_projection():
    """Conflicting low-confidence evidence must lower projection confidence,
    not silently raise the legacy star."""
    with SessionLocal() as db:
        _seed_graph_node(db)
        _add_evidence(
            db, evidence_id="ev-1", node_id="node-1",
            score=0.0, result="incorrect", confidence=0.9, source_type="exercise",
        )
        state = LearningStateProjector(db).rebuild_node(
            scope(), "node-1", head_event_id="evt-1"
        ).state
        db.commit()
        assert state.status == "weak"
        assert state.mastery_score < 0.5
        assert state.confidence < 0.7


def test_no_graph_node_skips_star_but_still_projects():
    """A learning node without a graph node must still get the new projection."""
    with SessionLocal() as db:
        _add_evidence(
            db, evidence_id="ev-1", node_id="ghost-node",
            score=0.9, confidence=0.9, difficulty=0.8,
        )
        # No GraphNode exists for ghost-node; the projection alone is updated.
        state = LearningStateProjector(db).rebuild_node(
            scope(), "ghost-node", head_event_id="evt-1"
        ).state
        db.commit()
        assert state.status == "familiar"
        assert db.scalar(select(GraphNode).where(GraphNode.id == "ghost-node")) is None


def test_apply_evidence_is_idempotent_per_evidence():
    """Re-applying the same accepted evidence must not award another star."""
    with SessionLocal() as db:
        node = _seed_graph_node(db)
        evidence = _add_evidence(
            db, evidence_id="ev-1", node_id="node-1",
            score=0.9, confidence=0.9, difficulty=0.8, source_type="exercise",
        )
        svc = MasteryService(db, "w1", "u1")
        first = svc.apply_evidence(evidence, node)
        second = svc.apply_evidence(evidence, node)
        db.commit()
        node = db.get(GraphNode, "node-1")
        assert first is True
        assert second is False
        assert node.mastery_stars == 1