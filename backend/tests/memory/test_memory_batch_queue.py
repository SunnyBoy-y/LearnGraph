"""Batch dedupe, idle wake-up and lease safety use a real disposable database."""
import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.models import Workspace, WorkspaceSetting, ChatSession, Message, MemoryExtractionState, MemoryJobState, DurableJob, utc_now
from app.services import durable_queue as queue_module
from app.services.memory_job_guard import job_id_for


@pytest.fixture
def queue_env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'queue.db').as_posix()}")
    Base.metadata.create_all(engine, tables=[model.__table__ for model in (
        Workspace, WorkspaceSetting, ChatSession, Message, MemoryExtractionState, MemoryJobState, DurableJob,
    )])
    factory = sessionmaker(engine, expire_on_commit=False)
    settings = SimpleNamespace(durable_queue_lease_seconds=300, durable_queue_max_attempts=3,
                               memory_extraction_idle_seconds=900, memory_extraction_min_user_turns=10)
    monkeypatch.setattr(queue_module, "SessionLocal", factory)
    monkeypatch.setattr(queue_module, "get_settings", lambda: settings)
    with factory() as db:
        db.add(Workspace(id="w", tenant_id="t", owner_user_id="u", name="test"))
        db.add(WorkspaceSetting(workspace_id="w", key="memory.shared_policy", value={"workspace_enabled": True}))
        db.flush()
        db.add(ChatSession(id="s", workspace_id="w", title="test", memory_enabled=True))
        db.commit()
    yield factory
    engine.dispose()


def _turn(factory, index):
    with factory() as db:
        db.add(Message(id=f"m{index}", workspace_id="w", session_id="s", role="user", status="completed",
                       content="我正在完成一个赛车项目。", created_at=utc_now()))
        db.commit()
    return queue_module.enqueue_memory_extraction("w", "s", "u", f"m{index}")


def test_ten_turns_coalesce_into_one_ready_job(queue_env):
    ids = {_turn(queue_env, index).id for index in range(10)}
    assert len(ids) == 1
    with queue_env() as db:
        assert db.scalar(select(func.count()).select_from(DurableJob)) == 1
        assert queue_module.DurableQueue(db).claim_next() is not None


def test_small_batch_has_a_durable_idle_wakeup(queue_env):
    job = _turn(queue_env, 0)
    with queue_env() as db:
        row = db.get(DurableJob, job.id)
        assert row.available_at.replace(tzinfo=None) > (utc_now() + timedelta(seconds=850)).replace(tzinfo=None)
        assert queue_module.DurableQueue(db).claim_next() is None


def test_new_turn_cannot_overwrite_a_leased_job(queue_env):
    for index in range(10):
        _turn(queue_env, index)
    with queue_env() as db:
        leased = queue_module.DurableQueue(db).claim_next()
        token = leased.lease_token
    latest = _turn(queue_env, 10)
    assert latest.id == leased.id and latest.status == "leased" and latest.lease_token == token


def test_long_message_checkpoint_gets_a_new_queue_key_after_an_old_job_completed(queue_env):
    old = _turn(queue_env, 0)
    content = "我正在完成一个赛车项目。"
    with queue_env() as db:
        db.get(DurableJob, old.id).status = "completed"
        db.add(MemoryJobState(
            id=job_id_for("w", "extraction", "s"), workspace_id="w", kind="extraction",
            resource_id="s", checkpoint={
                "message_id": "m0", "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                "offset": 8,
            },
        ))
        db.commit()
    fresh = queue_module.enqueue_memory_extraction("w", "s", "u", "m0")
    assert fresh is not None and fresh.id != old.id
    assert fresh.dedupe_key.endswith(":m0:" + hashlib.sha256(content.encode()).hexdigest() + ":8")


def test_legacy_immediate_job_defers_without_calling_provider(queue_env, monkeypatch):
    from app.services import memory_enhancement
    _turn(queue_env, 0)
    with queue_env() as db:
        job = db.scalar(select(DurableJob))
        job.available_at = utc_now() - timedelta(seconds=1)
        db.commit()
    def forbidden(*args, **kwargs):
        pytest.fail("An active short batch must not contact a model")
    monkeypatch.setattr(memory_enhancement, "model_provider_for_workspace", forbidden)
    assert queue_module.run_one_durable_job("worker")
    with queue_env() as db:
        job = db.scalar(select(DurableJob))
        assert job.status == "queued" and job.attempt_count == 0
        assert job.available_at.replace(tzinfo=None) > (utc_now() + timedelta(seconds=800)).replace(tzinfo=None)


def test_worker_schedules_messages_arriving_during_generation(queue_env, monkeypatch):
    from app.services import memory_enhancement
    for index in range(10):
        _turn(queue_env, index)
    def extract(db, workspace, session_id, settings, **kwargs):
        db.add(MemoryExtractionState(workspace_id="w", session_id="s", last_message_id="m9", last_message_at=utc_now()))
        db.add(Message(id="late", workspace_id="w", session_id="s", role="user", status="completed", content="我喜欢简洁讲解。", created_at=utc_now() + timedelta(milliseconds=1)))
        db.commit()
        return {"status": "ok", "drafts_created": 0}
    monkeypatch.setattr(memory_enhancement, "extract_session_memories", extract)
    assert queue_module.run_one_durable_job("worker")
    with queue_env() as db:
        jobs = db.scalars(select(DurableJob)).all()
        assert len(jobs) == 2
        assert sorted(job.status for job in jobs) == ["completed", "queued"]
        assert next(job for job in jobs if job.status == "queued").payload["batch_anchor"] == "m9"


def test_hundred_turns_dispatch_ten_batches(queue_env, monkeypatch):
    from app.services import memory_enhancement
    calls = []
    def extract(db, workspace, session_id, settings, **kwargs):
        state = db.scalar(select(MemoryExtractionState).where(MemoryExtractionState.session_id == session_id))
        pending = memory_enhancement._new_messages_since(db, workspace.id, session_id, state)
        calls.append(len(pending))
        if state is None:
            state = MemoryExtractionState(workspace_id="w", session_id="s")
            db.add(state)
        state.last_message_id, state.last_message_at = pending[-1].id, pending[-1].created_at
        db.commit()
        return {"status": "ok", "drafts_created": 0}
    monkeypatch.setattr(memory_enhancement, "extract_session_memories", extract)
    for index in range(100):
        _turn(queue_env, index)
        assert queue_module.run_one_durable_job("worker") is (index % 10 == 9)
    assert calls == [10] * 10
    with queue_env() as db:
        assert db.scalar(select(func.count()).select_from(DurableJob)) == 10
