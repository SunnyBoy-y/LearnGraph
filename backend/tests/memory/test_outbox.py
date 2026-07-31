from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-outbox-tests-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'outbox.db').as_posix()}"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_ENV"] = "test"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import MemoryProjectionOutbox, utc_now  # noqa: E402
from app.services.memory_outbox import ClaimedOutboxItem, MemoryOutboxWorker  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


def enqueue(*, status: str = "queued", available_at=None, lease_owner=None, lease_until=None):
    with SessionLocal() as db:
        item = MemoryProjectionOutbox(
            id="outbox-a",
            event_id="event-a",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            projection_kind="index",
            aggregate_id="memory-a",
            dedupe_key="outbox-a",
            status=status,
            available_at=available_at or utc_now(),
            lease_owner=lease_owner,
            lease_until=lease_until,
        )
        db.add(item)
        db.commit()


def test_live_lease_cannot_be_stolen() -> None:
    enqueue(
        status="leased",
        lease_owner="worker-a",
        lease_until=utc_now() + timedelta(minutes=1),
    )
    handled: list[str] = []
    with SessionLocal() as db:
        report = MemoryOutboxWorker(
            db,
            {"index": lambda item: handled.append(item.id)},
            worker_id="worker-b",
            strict_leases=True,
        ).run_once()

    assert report.claimed == 0
    assert handled == []
    with SessionLocal() as db:
        item = db.get(MemoryProjectionOutbox, "outbox-a")
        assert item is not None
        assert item.status == "leased"
        assert item.lease_owner == "worker-a"
        assert item.lease_generation == 0


def test_expired_lease_is_reclaimed_once() -> None:
    enqueue(
        status="leased",
        lease_owner="worker-a",
        lease_until=utc_now() - timedelta(seconds=1),
    )
    handled: list[str] = []
    with SessionLocal() as db:
        report = MemoryOutboxWorker(
            db,
            {"index": lambda item: handled.append(item.id)},
            worker_id="worker-b",
            strict_leases=True,
        ).run_once()

    assert report.claimed == 1
    assert report.succeeded == 1
    assert handled == ["outbox-a"]
    with SessionLocal() as db:
        item = db.get(MemoryProjectionOutbox, "outbox-a")
        assert item is not None
        assert item.status == "succeeded"
        assert item.lease_generation == 1


def test_stale_claimant_cannot_complete_reclaimed_generation() -> None:
    enqueue()
    with SessionLocal() as first_db:
        first_worker = MemoryOutboxWorker(
            first_db, {}, worker_id="shared-worker", strict_leases=True
        )
        first_claim = first_worker._claim(["outbox-a"], utc_now())
        first_db.commit()
        assert first_claim == [ClaimedOutboxItem("outbox-a", 1)]

    with SessionLocal() as db:
        item = db.get(MemoryProjectionOutbox, "outbox-a")
        assert item is not None
        item.lease_until = utc_now() - timedelta(seconds=1)
        db.commit()

    with SessionLocal() as second_db:
        second_worker = MemoryOutboxWorker(
            second_db, {}, worker_id="shared-worker", strict_leases=True
        )
        second_claim = second_worker._claim(["outbox-a"], utc_now())
        second_db.commit()
        assert second_claim == [ClaimedOutboxItem("outbox-a", 2)]

    with SessionLocal() as stale_db:
        stale_worker = MemoryOutboxWorker(
            stale_db, {}, worker_id="shared-worker", strict_leases=True
        )
        assert stale_worker._mark_succeeded(first_claim[0]) is False
        stale_db.commit()

    with SessionLocal() as db:
        item = db.get(MemoryProjectionOutbox, "outbox-a")
        assert item is not None
        assert item.status == "leased"
        assert item.lease_generation == 2


def test_handler_failure_retries_then_dead_letters() -> None:
    enqueue()
    with SessionLocal() as db:
        worker = MemoryOutboxWorker(
            db,
            {"index": lambda _item: (_ for _ in ()).throw(RuntimeError("provider down"))},
            worker_id="worker-a",
            max_attempts=2,
            strict_leases=True,
        )
        first = worker.run_once()
        assert first.failed == 1
        item = db.get(MemoryProjectionOutbox, "outbox-a")
        assert item is not None
        assert item.status == "failed"
        assert item.attempt_count == 1
        item.available_at = utc_now() - timedelta(seconds=1)
        db.commit()
        second = worker.run_once()

    assert second.dead_letter == 1
    with SessionLocal() as db:
        item = db.get(MemoryProjectionOutbox, "outbox-a")
        assert item is not None
        assert item.status == "dead_letter"
        assert item.attempt_count == 2
        assert item.lease_owner is None
        assert item.lease_until is None


def test_retry_does_not_clear_live_lease() -> None:
    enqueue(
        status="leased",
        lease_owner="worker-a",
        lease_until=utc_now() + timedelta(minutes=1),
    )
    with SessionLocal() as db:
        assert MemoryOutboxWorker.retry(db, "outbox-a") is False
        item = db.get(MemoryProjectionOutbox, "outbox-a")
        assert item is not None
        assert item.status == "leased"
        assert item.lease_owner == "worker-a"
