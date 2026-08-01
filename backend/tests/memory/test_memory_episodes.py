from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-episode-tests-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'episodes.db').as_posix()}"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.core.migrations import apply_schema_migrations  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import ConversationEpisode, MemoryEvent, MemoryScopeContext  # noqa: E402
from app.domain.memory_event_types import MemoryEventType  # noqa: E402
from app.domain.models import ChatSession, Message  # noqa: E402
from app.domain.schemas.memory_tasks import EpisodeCloseRequest, EpisodeGenerateRequest  # noqa: E402
from app.services.episode_boundary import BoundaryInputs  # noqa: E402
from app.services.memory_crypto import MemoryPayloadCipher  # noqa: E402
from app.services.memory_episodes import MemoryEpisodeService  # noqa: E402
from app.services.memory_event_store import MemoryEventStore  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_schema_migrations(conn)
    yield


def scope(*, workspace="w1", tenant="t1", user="u1"):
    return MemoryScopeContext(tenant_id=tenant, principal_user_id=user, workspace_id=workspace)


def service(db):
    return MemoryEpisodeService(db, MemoryEventStore(db, MemoryPayloadCipher("secret")))


def seed_message(db, *, message_id="msg-1", conversation_id="conv-1", workspace_id="w1"):
    if db.get(Message, message_id) is not None:
        return
    if db.get(ChatSession, conversation_id) is None:
        db.add(ChatSession(id=conversation_id, workspace_id=workspace_id, title="Episode source"))
        db.flush()
    db.add(Message(id=message_id, session_id=conversation_id, workspace_id=workspace_id, role="user", content="source"))
    db.commit()


def observe(svc, *, inputs=BoundaryInputs(), idem="observe-key", scoped=None):
    effective = scoped or scope()
    seed_message(svc.db, workspace_id=effective.workspace_id)
    return svc.observe_and_advance(
        effective, "u1", conversation_id="conv-1", source_message_refs=["msg-1"],
        inputs=inputs, idempotency_key=idem,
    )


def events(db):
    return db.scalars(select(MemoryEvent).order_by(MemoryEvent.global_position)).all()


def close_request(*, version=1, idem="close-key", refs=None):
    return EpisodeCloseRequest(
        expected_stream_version=version,
        title="Completed", summary="Summary",
        decisions=[{"decision": "use sqlite", "source": "msg-1"}],
        open_questions=[{"question": "next?", "source_refs": ["msg-1"]}],
        constraints=[{"constraint": "local only", "source": "msg-1"}],
        entities=[{"name": "sqlite"}],
        source_message_refs=refs or ["msg-1"], idempotency_key=idem,
    )


def test_first_observation_creates_open_version_one():
    with SessionLocal() as db:
        result = observe(service(db), idem="open-001")
        assert result.boundary_detected is False
        assert result.opened_episode is not None
        assert result.opened_episode.status == "open"
        assert result.opened_episode.stream_version == 1
        row = db.scalar(select(ConversationEpisode))
        assert row is not None and row.ended_at is None and row.end_event_position is None
        assert [e.event_type for e in events(db)] == [MemoryEventType.EPISODE_OPENED]


def test_no_boundary_noop_and_open_idempotency_replay():
    with SessionLocal() as db:
        svc = service(db)
        first = observe(svc, idem="open-002")
        noop = observe(svc, idem="open-003")
        replay = observe(svc, idem="open-002")
        assert first.opened_episode is not None
        assert noop.opened_episode is None
        assert replay.opened_episode is not None
        assert replay.opened_episode.episode_id == first.opened_episode.episode_id
        assert len(events(db)) == 1


def test_boundary_closes_old_stream_and_opens_successor_with_priority():
    with SessionLocal() as db:
        svc = service(db)
        initial = observe(svc, idem="advance-001")
        assert initial.opened_episode is not None
        result = observe(
            svc,
            inputs=BoundaryInputs(explicit_topic_switch=True, conversation_closed=True, idle_seconds=10_000),
            idem="advance-002",
        )
        assert result.boundary_detected is True
        assert result.boundary_reason == "explicit_topic_switch"
        assert result.closed_episode is not None and result.opened_episode is not None
        assert result.closed_episode.episode_id == initial.opened_episode.episode_id
        assert result.closed_episode.stream_version == 2
        assert result.opened_episode.stream_version == 1
        assert result.opened_episode.episode_id != initial.opened_episode.episode_id
        assert [e.event_type for e in events(db)] == [
            MemoryEventType.EPISODE_OPENED, MemoryEventType.EPISODE_CLOSED, MemoryEventType.EPISODE_OPENED,
        ]


def test_boundary_replay_returns_same_closed_and_successor_without_duplicates():
    with SessionLocal() as db:
        svc = service(db)
        observe(svc, idem="idem-base")
        first = observe(svc, inputs=BoundaryInputs(explicit_topic_switch=True), idem="idem-transition")
        replay = observe(svc, inputs=BoundaryInputs(explicit_topic_switch=True), idem="idem-transition")
        assert first.closed_episode is not None and first.opened_episode is not None
        assert replay.closed_episode is not None and replay.opened_episode is not None
        assert replay.closed_episode.episode_id == first.closed_episode.episode_id
        assert replay.opened_episode.episode_id == first.opened_episode.episode_id
        assert len(events(db)) == 3


def test_close_persists_structured_content_with_cas():
    with SessionLocal() as db:
        svc = service(db)
        opened = observe(svc, idem="close-open")
        assert opened.opened_episode is not None
        closed = svc.close(scope(), "u1", opened.opened_episode.episode_id, close_request(idem="close-001"))
        assert closed.status == "closed" and closed.stream_version == 2
        assert closed.title == "Completed"
        assert closed.decisions == [{"decision": "use sqlite", "source": "msg-1"}]
        assert [e.event_type for e in events(db)] == [MemoryEventType.EPISODE_OPENED, MemoryEventType.EPISODE_CLOSED]


def test_close_replays_same_idempotency_key_after_closed():
    with SessionLocal() as db:
        svc = service(db)
        opened = observe(svc, idem="close-replay-open")
        assert opened.opened_episode is not None
        request = close_request(idem="close-replay")
        first = svc.close(scope(), "u1", opened.opened_episode.episode_id, request)
        replay = svc.close(scope(), "u1", opened.opened_episode.episode_id, request)
        assert replay.episode_id == first.episode_id
        assert replay.stream_version == 2
        assert len(events(db)) == 2


def test_close_rejects_stale_cross_scope_and_foreign_source():
    with SessionLocal() as db:
        svc = service(db)
        opened = observe(svc, idem="close-open-2")
        assert opened.opened_episode is not None
        with pytest.raises(AppError) as exc:
            svc.close(scope(), "u1", opened.opened_episode.episode_id, close_request(version=2, idem="close-stale"))
        assert exc.value.code == "memory_stream_version_conflict"
        with pytest.raises(AppError) as exc:
            svc.close(scope(workspace="w2"), "u1", opened.opened_episode.episode_id, close_request(idem="close-scope"))
        assert exc.value.code == "episode_not_found"
        seed_message(db, message_id="msg-other", conversation_id="conv-other")
        with pytest.raises(AppError) as exc:
            svc.close(scope(), "u1", opened.opened_episode.episode_id, close_request(idem="close-foreign", refs=["msg-other"]))
        assert exc.value.code == "episode_source_ref_invalid"


def test_observe_rejects_foreign_source_scope_without_writes():
    with SessionLocal() as db:
        svc = service(db)
        observe(svc, idem="scope-open")
        with pytest.raises(AppError) as exc:
            observe(svc, inputs=BoundaryInputs(explicit_topic_switch=True), idem="scope-foreign", scoped=scope(workspace="w2"))
        assert exc.value.code == "episode_source_ref_invalid"
        assert len(db.scalars(select(ConversationEpisode)).all()) == 1


def test_legacy_generate_stays_closed_version_one_and_validates_source():
    with SessionLocal() as db:
        seed_message(db, conversation_id="conv-legacy")
        view = service(db).generate(
            scope(), "u1", EpisodeGenerateRequest(
                conversation_id="conv-legacy", title="Legacy", summary="imported",
                decisions=[{"decision": "use sqlite", "source": "msg-1"}],
                source_message_refs=["msg-1"], idempotency_key="legacy-generate",
            )
        )
        assert view.status == "closed" and view.stream_version == 1
        assert events(db)[0].event_type == MemoryEventType.EPISODE_CLOSED
