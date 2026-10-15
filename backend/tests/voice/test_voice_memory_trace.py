"""The retrieval trace: does it exist at all, is it labelled, does it persist.

This is the regression suite for a gap that was invisible for a long time: the
trace writer is a no-op unless the router is built with a session, and the chat /
voice factories were not passing one.  The tests below assert the three things
that gap hid -- a row exists, it carries the surface label, and the voice path
actually commits it -- plus the safety property that a failing trace write can no
longer roll back its caller.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.memory_event_models import (
    MemoryRetrievalTrace,
    MemoryScopeContext,
    MemorySearchDocument,
)
from app.domain.models import ChatSession, User, Workspace
from app.services import memory_router as memory_router_module
from app.services.chat import ChatService
from app.services.chat_service_factory import build_voice_chat_service
from app.services.memory_retrieval import MemoryHybridRetriever
from app.services.memory_router import MemoryRouter
from app.services.voice_context import VOICE_MEMORY_AGENT_ID


@pytest.fixture
def store(monkeypatch):
    """(session, session factory) over one in-memory workspace with one memory."""

    monkeypatch.setattr(
        ChatService, "_write_shadow_telemetry", lambda self, *a, **k: None
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        session.add(
            Workspace(
                id="ws-trace",
                tenant_id="tenant-trace",
                owner_user_id="u-trace",
                name="trace",
            )
        )
        session.add(
            User(
                id="u-trace",
                tenant_id="tenant-trace",
                username="tutor",
                username_normalized="tutor",
                password_hash="x",
            )
        )
        session.add(
            ChatSession(
                id="cs-trace",
                workspace_id="ws-trace",
                title="trace",
                memory_enabled=True,
            )
        )
        from app.domain.models import WorkspaceSetting
        session.add(WorkspaceSetting(workspace_id="ws-trace", key="memory.shared_policy",
            value={"workspace_enabled": True, "workspace_recall_enabled": True}))
        session.add(
            MemorySearchDocument(
                id="doc-trace",
                target_type="memory",
                target_id="mem-trace",
                target_version=1,
                memory_layer="L3",
                memory_type="semantic_memory",
                subject="Rust 所有权",
                content="用户说 Rust 的所有权规则他早就懂了。",
                tenant_id="tenant-trace",
                subject_user_id="u-trace",
                workspace_id="ws-trace",
                status="active",
                sensitivity="normal",
                importance=0.8,
                confidence=0.9,
                source_event_id="event-trace",
                content_hash="hash-trace",
            )
        )
        session.commit()
        yield session, Session
    engine.dispose()


def _chat(session):
    from app.core.config import Settings

    return build_voice_chat_service(
        session,
        workspace_id="ws-trace",
        actor_id="u-trace",
        settings=Settings(),
    )


def _traces(session) -> list[MemoryRetrievalTrace]:
    return list(
        session.scalars(
            select(MemoryRetrievalTrace).where(
                MemoryRetrievalTrace.workspace_id == "ws-trace"
            )
        ).all()
    )


# ------------------------------------------------------------ the row exists


def test_a_text_turn_leaves_a_retrieval_trace(store):
    session, _Session = store

    _chat(session)._build_v2_memory_context("cs-trace", "Rust 所有权")

    traces = _traces(session)
    assert traces, "a chat memory read must leave a trace"
    assert {item.agent_id for item in traces} == {"main_agent"}


def test_a_voice_recall_leaves_a_trace_labelled_voice(store):
    session, _Session = store

    _chat(session).voice_memory_block("cs-trace", "Rust 所有权")

    assert {item.agent_id for item in _traces(session)} == {VOICE_MEMORY_AGENT_ID}


def test_the_trace_records_what_the_retrieval_did(store):
    session, _Session = store

    _chat(session).voice_memory_block("cs-trace", "Rust 所有权")

    trace = _traces(session)[0]
    assert trace.strategy == "hybrid_memory_v2"
    assert trace.status == "completed"
    assert trace.selected_count >= 1
    assert trace.latency_ms is not None
    assert trace.query_hash


# --------------------------------------------------------- the voice commit


def test_a_voice_recall_commits_its_trace_so_it_survives_the_session(store, monkeypatch):
    """The per-turn recall runs in its own short session: without a commit the
    flushed row dies with it."""

    from app.core import config as config_mod
    from app.core import database as database_mod
    from app.services import chat_service_factory as factory_mod
    from app.voice import events as events_mod
    from app.voice.embedded_memory import recall_voice_memory

    session, Session = store
    handle = SimpleNamespace(
        workspace_id="ws-trace",
        owner_user_id="u-trace",
        chat_session_id="cs-trace",
        model_id=None,
        provider_id=None,
        max_thinking_mode="off",
    )
    chat = _chat(session)
    monkeypatch.setattr(events_mod, "load_session", lambda sid: handle)
    monkeypatch.setattr(
        database_mod,
        "SessionLocal",
        lambda: __import__("contextlib").nullcontext(session),
    )
    monkeypatch.setattr(factory_mod, "build_voice_chat_service", lambda *a, **k: chat)
    monkeypatch.setattr(config_mod, "get_settings", lambda: SimpleNamespace())

    recall = recall_voice_memory("vs_1", "Rust 所有权")
    assert recall is not None

    # A brand new session can only see it if it was committed.
    with Session() as fresh:
        rows = list(fresh.scalars(select(MemoryRetrievalTrace)).all())
    assert rows and {item.agent_id for item in rows} == {VOICE_MEMORY_AGENT_ID}


# ------------------------------------------------- a failing trace is harmless


def test_a_failing_trace_does_not_roll_back_the_callers_pending_writes(store, monkeypatch):
    """The old fallback rolled back the caller's session; the savepoint must not."""

    session, _Session = store

    def boom(_db):
        raise RuntimeError("fts probe exploded")

    monkeypatch.setattr(memory_router_module, "probe_memory_search_fts_capability", boom)

    pending = MemorySearchDocument(
        id="doc-pending",
        target_type="memory",
        target_id="mem-pending",
        target_version=1,
        memory_layer="L3",
        memory_type="semantic_memory",
        subject="待写",
        content="这一行是调用方在同一会话里还没提交的写入。",
        tenant_id="tenant-trace",
        subject_user_id="u-trace",
        workspace_id="ws-trace",
        status="active",
        sensitivity="normal",
        importance=0.5,
        confidence=0.5,
        source_event_id="event-pending",
        content_hash="hash-pending",
    )
    session.add(pending)

    router = MemoryRouter(MemoryHybridRetriever(session), db=session)
    scope = MemoryScopeContext(
        tenant_id="tenant-trace",
        principal_user_id="u-trace",
        workspace_id="ws-trace",
        conversation_id="cs-trace",
        node_ids=(),
    )
    retrieval = SimpleNamespace(candidates=[], excluded={}, degraded_modes=())

    trace_id = router._persist_trace(scope, "Rust 所有权", (), [], retrieval, 3)

    assert trace_id is None, "a failed trace reports nothing"
    # The caller's write is still there and still committable.
    session.commit()
    assert session.get(MemorySearchDocument, "doc-pending") is not None


def test_a_trace_row_is_not_written_twice_for_one_read(store):
    session, _Session = store

    _chat(session).voice_memory_block("cs-trace", "Rust 所有权")

    assert len(_traces(session)) == 1
