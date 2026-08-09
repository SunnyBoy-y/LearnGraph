"""M4 unique memory producers: exercise evidence and agent run completion."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import Base
from app.domain import models as m
from app.domain.memory_event_models import MemoryEvent, MemoryScopeContext
from app.domain.schemas.learning import AnswerRequest
from app.services.agent_run_memory import AgentRunProjectionService, RunStart
from app.services.learning import ExerciseService
from app.services.memory_event_ingestor import event_cipher_from_settings
from app.services.memory_event_store import MemoryEventStore

WORKSPACE = "ws-producers"
ACTOR = "user-producers"
TENANT = "local-tenant"


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        session.add(
            m.Workspace(
                id=WORKSPACE,
                tenant_id=TENANT,
                owner_user_id=ACTOR,
                name="producers",
            )
        )
        session.add(
            m.Goal(
                id="goal-1",
                workspace_id=WORKSPACE,
                title="学习 Rust",
                raw_prompt="学习 Rust",
                status="approved",
            )
        )
        session.add(
            m.Graph(
                id="graph-1",
                workspace_id=WORKSPACE,
                goal_id="goal-1",
                title="Rust 图谱",
                status="published",
            )
        )
        session.add(
            m.GraphNode(
                id="node-1",
                workspace_id=WORKSPACE,
                graph_id="graph-1",
                label="所有权",
                description="Rust ownership",
            )
        )
        session.add(
            m.Exercise(
                id="exercise-1",
                workspace_id=WORKSPACE,
                node_id="node-1",
                question_type="single_choice",
                prompt="Rust 所有权核心规则是？",
                options=["一个值同时只能有一个所有者", "多个所有者"],
                answer_key="一个值同时只能有一个所有者",
                explanation="所有权规则",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def test_exercise_answer_publishes_only_learning_evidence(db: Session) -> None:
    service = ExerciseService(
        db,
        WORKSPACE,
        ACTOR,
        settings=get_settings(),
    )
    result = service.answer(
        "exercise-1",
        AnswerRequest(answer="一个值同时只能有一个所有者"),
    )
    assert result.answer_record_id
    events = db.scalars(
        select(MemoryEvent).where(MemoryEvent.workspace_id == WORKSPACE)
    ).all()
    assert [event.event_type for event in events] == [
        "learning.evidence_recorded"
    ]
    assert all(event.producer == "tool" for event in events)
    assert all(not event.event_type.startswith("memory.") for event in events)


def test_agent_run_completed_event_is_excluded_from_memory_projection(db: Session) -> None:
    scope = MemoryScopeContext(
        tenant_id=TENANT,
        principal_user_id=ACTOR,
        workspace_id=WORKSPACE,
    )
    store = MemoryEventStore(db, event_cipher_from_settings(get_settings()))
    service = AgentRunProjectionService(db, store)
    run = service.start(
        scope,
        ACTOR,
        RunStart(
            task_id=None,
            agent_id="main_agent",
            model_id="test-model",
            input_scope_hash="hash",
            context_build_id=None,
            idempotency_key="agent-start-0001",
        ),
    )
    service.complete(
        scope,
        run.id,
        actor_id=ACTOR,
        expected_version=1,
        result_summary="完成工具调用",
        tool_call_refs=[],
        artifact_refs=[],
        succeeded=True,
        idempotency_key="agent-complete-0001",
    )
    events = db.scalars(
        select(MemoryEvent)
        .where(MemoryEvent.workspace_id == WORKSPACE)
        .order_by(MemoryEvent.global_position)
    ).all()
    assert [event.event_type for event in events] == [
        "agent.run_started",
        "agent.run_completed",
    ]
    completed = events[-1]
    payload = store.read_payload(scope, completed.event_id)
    assert payload["summary_eligibility"] == "excluded"
    assert all(not event.event_type.startswith("memory.") for event in events)


def test_agent_run_completion_is_idempotent(db: Session) -> None:
    scope = MemoryScopeContext(
        tenant_id=TENANT,
        principal_user_id=ACTOR,
        workspace_id=WORKSPACE,
    )
    store = MemoryEventStore(db, event_cipher_from_settings(get_settings()))
    service = AgentRunProjectionService(db, store)
    run = service.start(
        scope,
        ACTOR,
        RunStart(
            task_id=None,
            agent_id="main_agent",
            model_id="test-model",
            input_scope_hash="hash",
            context_build_id=None,
            idempotency_key="agent-start-0002",
        ),
    )
    kwargs = {
        "scope": scope,
        "run_id": run.id,
        "actor_id": ACTOR,
        "expected_version": 1,
        "result_summary": "done",
        "tool_call_refs": [],
        "artifact_refs": [],
        "succeeded": True,
        "idempotency_key": "agent-complete-0002",
    }
    service.complete(**kwargs)
    service.complete(**kwargs)
    completed = db.scalars(
        select(MemoryEvent).where(
            MemoryEvent.workspace_id == WORKSPACE,
            MemoryEvent.event_type == "agent.run_completed",
        )
    ).all()
    assert len(completed) == 1


def test_exercise_event_payload_never_creates_atom(db: Session) -> None:
    service = ExerciseService(
        db,
        WORKSPACE,
        ACTOR,
        settings=get_settings(),
    )
    service.answer(
        "exercise-1",
        AnswerRequest(answer="一个值同时只能有一个所有者"),
    )
    atoms = db.scalars(
        select(MemoryEvent).where(
            MemoryEvent.workspace_id == WORKSPACE,
            MemoryEvent.event_type == "memory.atom_created",
        )
    ).all()
    assert atoms == []
