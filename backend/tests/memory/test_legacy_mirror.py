from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-mirror-tests-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'mirror.db').as_posix()}"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.migrations import apply_schema_migrations  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import MemoryScopeContext, MemoryStream  # noqa: E402
from app.domain.models import MemoryRecord, MemoryRevision, Workspace  # noqa: E402
from app.services.memory_crypto import MemoryPayloadCipher  # noqa: E402
from app.services.memory_cutover import MemoryCutoverService  # noqa: E402
from app.services.memory_event_store import MemoryEventStore  # noqa: E402
from app.services.memory_projector import MemoryProjector  # noqa: E402

WS = "w1"
TENANT = "t1"
USER = "u1"


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_schema_migrations(conn)
    yield


def scope(**kw):
    return MemoryScopeContext(
        tenant_id=kw.get("tenant", TENANT),
        principal_user_id=kw.get("user", USER),
        workspace_id=kw.get("workspace", WS),
    )


def _seed_workspace(db) -> Workspace:
    ws = Workspace(id=WS, tenant_id=TENANT, owner_user_id=USER, name="t")
    db.add(ws)
    db.commit()
    return ws


def _add_legacy_memory(
    db,
    *,
    memory_id: str,
    title: str,
    content: str,
    state: str = "active",
    revision: int = 1,
) -> MemoryRecord:
    record = MemoryRecord(
        id=memory_id,
        workspace_id=WS,
        tenant_id=TENANT,
        subject_user_id=USER,
        title=title,
        content_hash=hashlib_sha256(f"{title}\0{content}"),
        state=state,
        revision=revision,
        record_kind="semantic_memory",
        relative_path="",
    )
    db.add(record)
    db.flush()
    rev = MemoryRevision(
        workspace_id=WS,
        memory_id=memory_id,
        revision=revision,
        title=title,
        content=content,
        content_hash=hashlib_sha256(f"{title}\0{content}"),
        actor_id=USER,
        operation="ADD",
        is_active=True,
    )
    db.add(rev)
    db.commit()
    return record


def hashlib_sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _projector(db) -> MemoryProjector:
    return MemoryProjector(db, cipher=MemoryPayloadCipher("secret"))


# ── Slice A: backfill only writes snapshots, never fabricates domain events ──


def test_backfill_creates_search_document_with_matching_hash():
    with SessionLocal() as db:
        _seed_workspace(db)
        _add_legacy_memory(db, memory_id="m1", title="Alpha", content="hello world")
        count = _projector(db).backfill_legacy(tenant_id=TENANT, workspace_id=WS)
        assert count == 1
        report = _projector(db).parity_report(WS)
        assert report.legacy_count == 1
        assert report.projection_count == 1
        assert report.matching_hashes == 1
        assert report.mismatches == []


def test_backfill_is_idempotent():
    with SessionLocal() as db:
        _seed_workspace(db)
        _add_legacy_memory(db, memory_id="m1", title="Alpha", content="hello world")
        proj = _projector(db)
        first = proj.backfill_legacy(tenant_id=TENANT, workspace_id=WS)
        second = proj.backfill_legacy(tenant_id=TENANT, workspace_id=WS)
        # count is "records processed", not "net-new"; idempotency is proven by
        # the document count staying at 1 (upsert, not insert-duplicate).
        assert first == 1
        assert second == 1
        report = proj.parity_report(WS)
        assert report.projection_count == 1


def test_backfill_skips_inactive_and_missing_revision():
    with SessionLocal() as db:
        _seed_workspace(db)
        _add_legacy_memory(db, memory_id="active1", title="A", content="x")
        _add_legacy_memory(db, memory_id="inactive1", title="B", content="y", state="archived")
        record = MemoryRecord(
            id="norev",
            workspace_id=WS,
            tenant_id=TENANT,
            subject_user_id=USER,
            title="NoRev",
            content_hash=hashlib_sha256("NoRev\0"),
            state="active",
            revision=1,
            relative_path="",
        )
        db.add(record)
        db.commit()
        count = _projector(db).backfill_legacy(tenant_id=TENANT, workspace_id=WS)
        assert count == 1  # only active1
        report = _projector(db).parity_report(WS)
        # parity only compares active records; norev (active, no revision) is a
        # real gap, while inactive1 is out of the comparison scope entirely.
        assert report.legacy_count == 2  # active1 + norev
        assert report.mismatches == ["norev"]


def test_backfill_does_not_fabricate_domain_events():
    """Synthetic snapshots must never be recorded as real memory.* events."""
    with SessionLocal() as db:
        _seed_workspace(db)
        _add_legacy_memory(db, memory_id="m1", title="Alpha", content="hello")
        _projector(db).backfill_legacy(tenant_id=TENANT, workspace_id=WS)
        events = db.scalars(
            select(memory_event_models.MemoryEvent).where(
                memory_event_models.MemoryEvent.workspace_id == WS
            )
        ).all()
        assert all(not e.event_type.startswith("memory.") for e in events)
        assert all(e.event_type == "legacy.memory_state_snapshotted" for e in events)


def test_parity_mismatch_on_content_change():
    """If the legacy record content changes, backfill parity must flag it."""
    with SessionLocal() as db:
        _seed_workspace(db)
        _add_legacy_memory(db, memory_id="m1", title="Alpha", content="v1")
        _projector(db).backfill_legacy(tenant_id=TENANT, workspace_id=WS)
        # mutate the live search document to simulate drift
        from app.domain.memory_event_models import MemorySearchDocument

        doc = db.scalar(
            select(MemorySearchDocument).where(MemorySearchDocument.target_id == "m1")
        )
        assert doc is not None
        doc.content_hash = hashlib_sha256("drifted")
        db.commit()
        report = _projector(db).parity_report(WS)
        assert report.matching_hashes == 0
        assert "m1" in report.mismatches


# ── Slice B: dual-write keeps Event + Legacy record + outbox in one txn ──────


def test_dual_write_event_record_outbox_atomic():
    """mirror_legacy_record appends an event, mutates the legacy record, and
    enqueues outbox rows — all in the same db transaction. A forced failure
    after append must roll back everything."""
    from app.domain.memory_event_models import MemoryProjectionOutbox

    with SessionLocal() as db:
        _seed_workspace(db)
        record = _add_legacy_memory(db, memory_id="m1", title="Alpha", content="v1")
        store = MemoryEventStore(db, MemoryPayloadCipher("secret"))
        from app.services.memory_commands import MemoryCommandService

        svc = MemoryCommandService(db, store)
        svc.mirror_legacy_record(
            scope(),
            record,
            content="v1",
            operation="ADD",
            actor_id=USER,
            idempotency_key="dual-write-1",
        )
        # now force a failure after append before commit
        try:
            db.flush()
            raise RuntimeError("boom")
        except RuntimeError:
            db.rollback()

        events = db.scalars(
            select(memory_event_models.MemoryEvent).where(
                memory_event_models.MemoryEvent.workspace_id == WS
            )
        ).all()
        outbox = db.scalars(
            select(MemoryProjectionOutbox).where(
                MemoryProjectionOutbox.workspace_id == WS
            )
        ).all()
        assert events == []
        assert outbox == []


def test_dual_write_commits_event_and_outbox_together():
    from app.domain.memory_event_models import MemoryProjectionOutbox

    with SessionLocal() as db:
        _seed_workspace(db)
        record = _add_legacy_memory(db, memory_id="m1", title="Alpha", content="v1")
        store = MemoryEventStore(db, MemoryPayloadCipher("secret"))
        from app.services.memory_commands import MemoryCommandService

        svc = MemoryCommandService(db, store)
        svc.mirror_legacy_record(
            scope(),
            record,
            content="v1",
            operation="ADD",
            actor_id=USER,
            idempotency_key="dual-write-2",
        )
        db.commit()
        events = db.scalars(
            select(memory_event_models.MemoryEvent).where(
                memory_event_models.MemoryEvent.workspace_id == WS
            )
        ).all()
        outbox = db.scalars(
            select(MemoryProjectionOutbox).where(
                MemoryProjectionOutbox.workspace_id == WS
            )
        ).all()
        assert len(events) == 1
        assert events[0].event_type == "memory.atom_created"
        assert len(outbox) >= 1  # markdown/mem0/embedding/profile/index enqueued


# ── Slice C: replay-validate + can_enable_event_reads ────────────────────────


def test_replay_validate_reports_stream_gap():
    with SessionLocal() as db:
        _seed_workspace(db)
        record = _add_legacy_memory(db, memory_id="m1", title="Alpha", content="v1")
        store = MemoryEventStore(db, MemoryPayloadCipher("secret"))
        from app.services.memory_commands import MemoryCommandService

        svc = MemoryCommandService(db, store)
        svc.mirror_legacy_record(
            scope(), record, content="v1", operation="ADD", actor_id=USER,
            idempotency_key="rv-1",
        )
        db.commit()

        from app.core.config import Settings

        cutover = MemoryCutoverService(db, Settings())
        report = cutover.replay_validate(WS)
        assert report.errors == ()
        assert report.event_count == 1

        # Simulate a gap: bump stream.current_version without the matching event.
        stream = db.scalars(select(MemoryStream)).first()
        assert stream is not None
        stream.current_version = stream.current_version + 1
        db.commit()
        report2 = cutover.replay_validate(WS)
        assert report2.errors
        assert any("stream_version_gap" in e for e in report2.errors)


def test_can_enable_event_reads_gates_on_errors():
    from app.core.config import Settings

    with SessionLocal() as db:
        _seed_workspace(db)
        cutover = MemoryCutoverService(db, Settings())
        clean = cutover.replay_validate(WS)
        assert cutover.can_enable_event_reads(clean) is True
