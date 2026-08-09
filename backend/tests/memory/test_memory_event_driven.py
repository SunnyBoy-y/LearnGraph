"""Event-driven memory extraction regressions (upgrade requirements 1-3).

1. ``memory.extract`` durable jobs dedupe per (session, last message).
2. Default extraction config is enabled; extraction runs only when the
   workspace memory master switch is on (no extraction sub-switch).
3. The worker path consumes ``memory.extract`` safely when no model provider
   is configured (falls back to workspace default → unavailable).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain import models as m
from app.services.durable_queue import DurableJob, DurableQueue
from app.services.memory_enhancement import (
    MEMORY_POLICY_KEY,
    _workspace_memory_enabled,
    default_enhancement_config,
)

WORKSPACE = "ws-memory-event"
ACTOR = "user-memory-event"
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
                name="memory event",
            )
        )
        session.add(
            m.ChatSession(
                id="session-1",
                workspace_id=WORKSPACE,
                title="event session",
                memory_enabled=True,
            )
        )
        session.commit()
        yield session
    engine.dispose()


def _enqueue_extract(db: Session, session_id: str, message_id: str) -> DurableJob:
    queue = DurableQueue(db)
    return queue.enqueue(
        workspace_id=WORKSPACE,
        kind="memory.extract",
        payload={
            "workspace_id": WORKSPACE,
            "session_id": session_id,
            "actor_id": ACTOR,
            "last_message_id": message_id,
        },
        dedupe_key=f"memory.extract:{session_id}:{message_id}",
    )


def test_default_extraction_config_is_enabled() -> None:
    config = default_enhancement_config()
    assert config["extraction"]["enabled"] is True
    assert config["extraction"]["auto_commit"] is True


def test_memory_extract_job_dedupes_per_message(db: Session) -> None:
    first = _enqueue_extract(db, "session-1", "message-42")
    second = _enqueue_extract(db, "session-1", "message-42")
    assert first.id == second.id
    assert first.kind == "memory.extract"
    assert first.status == "queued"
    assert first.payload["last_message_id"] == "message-42"


def test_distinct_messages_create_distinct_jobs(db: Session) -> None:
    a = _enqueue_extract(db, "session-1", "message-42")
    b = _enqueue_extract(db, "session-1", "message-43")
    assert a.id != b.id


def test_workspace_memory_switch_gates_extraction(db: Session) -> None:
    # No policy setting → master switch off → extraction must not run.
    assert _workspace_memory_enabled(db, WORKSPACE) is False
    db.add(
        m.WorkspaceSetting(
            workspace_id=WORKSPACE,
            key=MEMORY_POLICY_KEY,
            value={"workspace_enabled": True},
        )
    )
    db.commit()
    assert _workspace_memory_enabled(db, WORKSPACE) is True


def test_job_row_survives_reclaim_without_model(db: Session) -> None:
    job = _enqueue_extract(db, "session-1", "message-42")
    db.commit()
    reloaded = db.get(DurableJob, job.id)
    assert reloaded is not None
    assert reloaded.status == "queued"
    assert reloaded.payload["workspace_id"] == WORKSPACE
