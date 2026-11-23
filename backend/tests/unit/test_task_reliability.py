from __future__ import annotations

import asyncio
import threading
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.models import DurableJob, User, Workspace
from app.domain.task_reliability_models import DurableJobAttempt
from app.services import durable_queue as queue_module
from app.services import scheduled_tasks
from app.services.task_reliability import task_execution_metrics


@pytest.fixture
def sessions(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'queue.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(id="owner", tenant_id="tenant", username="owner", username_normalized="owner", password_hash="test"))
        db.add(Workspace(id="ws", tenant_id="tenant", owner_user_id="owner", name="Tests"))
        db.commit()
    yield factory
    engine.dispose()


def test_expired_final_attempt_is_not_claimed_again(sessions):
    now = queue_module.utc_now()
    with sessions() as db:
        first = queue_module.DurableQueue(db, worker_id="one", lease_seconds=1)
        first.enqueue(workspace_id="ws", kind="probe", payload={}, available_at=now, max_attempts=1)
        job = first.claim_next(now=now)
    with sessions() as db:
        second = queue_module.DurableQueue(db, worker_id="two")
        assert second.claim_next(now=now + timedelta(seconds=2)) is None
        assert db.get(DurableJob, job.id).attempt_count == 1
        assert db.get(DurableJob, job.id).status == "failed"
        assert db.get(DurableJobAttempt, job.lease_token).outcome == "lease_expired"
    with sessions() as db:
        assert not queue_module.DurableQueue(db, worker_id="one").complete(job)


def test_renewal_and_recovery_metrics(sessions):
    now = queue_module.utc_now() - timedelta(minutes=1)
    with sessions() as db:
        first = queue_module.DurableQueue(db, worker_id="one", lease_seconds=3)
        first.enqueue(workspace_id="ws", kind="probe", payload={}, available_at=now)
        initial = first.claim_next(now=now)
        assert first.renew(initial.id, initial.lease_token, now=now + timedelta(seconds=2))
    with sessions() as db:
        second = queue_module.DurableQueue(db, worker_id="two", lease_seconds=3)
        assert second.claim_next(now=now + timedelta(seconds=4)) is None
        takeover = second.claim_next(now=now + timedelta(seconds=6))
        assert takeover.lease_token != initial.lease_token
        assert second.complete(takeover, now=now + timedelta(seconds=7))
        metrics = task_execution_metrics(db, "ws", 7)["durable_jobs"]["probe"]
        assert metrics["outcomes"] == {"lease_expired": 1, "succeeded": 1}
        assert metrics["first_attempt_failure_rate"] == 1
        assert metrics["final_failure_rate"] == 0
        assert metrics["automatic_recovery_rate"] == 1
        assert task_execution_metrics(db, "another-workspace", 7)["durable_jobs"] == {}


def test_worker_continues_after_database_failure(monkeypatch):
    monkeypatch.setattr(queue_module, "get_settings", lambda: SimpleNamespace(durable_queue_poll_seconds=0.01))
    calls = []

    async def exercise():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def invoke(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OperationalError("claim", {}, RuntimeError("temporary failure"))
            kwargs["on_poll"]()
            loop.call_soon_threadsafe(stop.set)
            return False

        monkeypatch.setattr(queue_module, "run_one_durable_job", invoke)
        await asyncio.wait_for(queue_module.durable_queue_worker(stop, "worker", goal_lane=True), timeout=2)

    asyncio.run(exercise())
    assert len(calls) == 2


def test_supervisor_restarts_an_unexpected_exit(monkeypatch):
    calls = []

    async def invoke(stop, worker_id, *, goal_lane):
        calls.append(worker_id)
        if len(calls) == 1:
            raise RuntimeError("injected worker crash")
        stop.set()

    async def exercise():
        await asyncio.wait_for(queue_module.supervise_durable_queue(asyncio.Event(), goal_lane=True), timeout=2)

    monkeypatch.setattr(queue_module, "durable_queue_worker", invoke)
    asyncio.run(exercise())
    assert len(set(calls)) == 2


def test_schedule_reconciles_when_all_execution_slots_are_busy(monkeypatch):
    saturated, reconciled = threading.Event(), threading.Event()
    submitted = []

    class BusyFuture:
        def done(self):
            return False

    class BusyPool:
        def submit(self, function, task_id, run_id, *args, **kwargs):
            submitted.append(run_id)
            if len(submitted) == 4:
                saturated.set()
            return BusyFuture()

        def shutdown(self, **kwargs):
            pass

    def tick(*args, **kwargs):
        if saturated.is_set():
            reconciled.set()

    monkeypatch.setattr(scheduled_tasks, "ThreadPoolExecutor", lambda **kwargs: BusyPool())
    monkeypatch.setattr(scheduled_tasks, "_schedule_dispatch_batch", lambda *args: [(i, i, False) for i in range(4)])
    monkeypatch.setattr(scheduled_tasks, "reconcile_runs", tick)

    async def exercise():
        stop = asyncio.Event()
        worker = asyncio.create_task(scheduled_tasks.scheduled_task_worker(
            stop, SimpleNamespace(scheduled_tasks_poll_seconds=0.01), nullcontext,
        ))
        try:
            assert await asyncio.to_thread(reconciled.wait, 2)
        finally:
            stop.set()
            await asyncio.wait_for(worker, timeout=2)

    asyncio.run(exercise())
    assert submitted == [0, 1, 2, 3]
