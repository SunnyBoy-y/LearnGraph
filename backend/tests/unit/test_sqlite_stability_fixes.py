"""Regression tests for SQLite stability fixes (P0/P1/P2)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core import database as database_mod
from app.core.config import Settings
from app.core.database import (
    Base,
    _CrossThreadRLock,
    _configure_sqlite_connection,
    _is_write_statement,
    install_sqlite_write_gate,
    reset_sqlite_metrics,
    session_holds_write_gate,
    snapshot_sqlite_metrics,
)
from app.core.database_routing import (
    DatabaseActivationError,
    PrimaryDatabaseEngine,
    atomic_json,
    read_json,
    startup_url,
    state_root,
)
from app.domain.memory_event_models import MemoryRetrievalTrace, MemoryScopeContext
from app.domain.models import (
    ChatSession,
    Message,
    MessageStreamEvent,
    MessageVersion,
    SandboxAgentEvent,
    SandboxAgentTask,
    User,
    Workspace,
)
from app.services.memory_retrieval import MemoryHybridRetriever
from app.services.memory_router import MemoryRouter
from app.services.site_backup import SiteBackupService
from app.services.site_restore import recover_sqlite_offline, restore_offline


def _make_wal_engine(db_path: Path):
    eng = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 2.0},
    )

    @event.listens_for(eng, "connect")
    def _on_connect(dbapi_conn, _record):
        _configure_sqlite_connection(dbapi_conn, _record)

    install_sqlite_write_gate(eng)
    return eng


def test_cross_thread_rlock_fresh_thread_release_does_not_leak():
    """A brand-new thread that never called acquire() must fully release a handed-off hold."""
    lock = _CrossThreadRLock()
    acquired = threading.Event()

    def t_acquire():
        assert lock.acquire(blocking=False)
        acquired.set()

    def t_release_fresh():
        assert acquired.wait(5)
        lock.release()

    ta = threading.Thread(target=t_acquire)
    tb = threading.Thread(target=t_release_fresh)
    ta.start()
    tb.start()
    ta.join(5)
    tb.join(5)

    assert not ta.is_alive() and not tb.is_alive()
    assert lock.acquire(blocking=False)
    lock.release()


def test_cross_thread_rlock_warmed_thread_release_preserves_mutual_exclusion():
    """After thread B releases thread A's hold, thread A must NOT bypass thread C's mutex."""
    lock = _CrossThreadRLock()
    b_warmed = threading.Event()
    a_acquired = threading.Event()
    b_released = threading.Event()
    c_holding = threading.Event()
    done = threading.Event()
    a_bypassed: list[bool] = []

    def thread_b():
        assert lock.acquire(blocking=False)
        lock.release()
        b_warmed.set()
        assert a_acquired.wait(5)
        lock.release()
        b_released.set()

    def thread_a():
        assert b_warmed.wait(5)
        assert lock.acquire(blocking=False)
        a_acquired.set()
        assert c_holding.wait(5)
        a_bypassed.append(lock.acquire(blocking=False))
        done.set()

    def thread_c():
        assert b_released.wait(5)
        assert lock.acquire(blocking=False)
        c_holding.set()
        assert done.wait(5)
        lock.release()

    threads = [threading.Thread(target=fn) for fn in (thread_b, thread_a, thread_c)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)

    assert a_bypassed == [False]
    assert lock.acquire(blocking=False)
    lock.release()


def test_cross_thread_rlock_watchdog_evicted_token_cannot_unlock_next_holder():
    """A late release_token from a watchdog-evicted hold must not unlock a subsequent holder."""
    lock = _CrossThreadRLock()
    reset_sqlite_metrics()
    evicted_token = lock.acquire_token(blocking=False)
    assert evicted_token is not None
    time.sleep(0.02)
    report = lock.watchdog_check(threshold_seconds=0.001)
    assert report["reset"] is True

    next_token = lock.acquire_token(blocking=False)
    assert next_token is not None and next_token != evicted_token

    # Late cross-thread release of the evicted token must be a safe no-op.
    def late_release():
        lock.release_token(evicted_token)

    t = threading.Thread(target=late_release)
    t.start()
    t.join(5)

    # Next holder still owns the lock!
    other_acquired: list[bool] = []

    def probe():
        other_acquired.append(lock.acquire(blocking=False))

    p = threading.Thread(target=probe)
    p.start()
    p.join(5)
    assert other_acquired == [False]

    lock.release_token(next_token)
    assert lock.acquire(blocking=False)
    lock.release()
    assert snapshot_sqlite_metrics()["release_failures"] == 0.0


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("INSERT INTO t VALUES (1)", True),
        ("insert into t values (1)", True),
        ("update t set v = 1", True),
        ("delete from t", True),
        ("/* comment */ INSERT INTO t VALUES (1)", True),
        ("-- leading comment\nupdate t set v = 2", True),
        ("WITH cte AS (SELECT 1) INSERT INTO t SELECT * FROM cte", True),
        ("with cte as (select 1) delete from t where id in (select * from cte)", True),
        ("BEGIN IMMEDIATE", True),
        ("begin exclusive", True),
        ("SELECT * FROM t", False),
        ("WITH cte AS (SELECT 1) SELECT * FROM cte", False),
        ("BEGIN", False),
        ("PRAGMA foreign_keys", False),
    ],
)
def test_is_write_statement_handles_case_comments_cte_and_begin(sql: str, expected: bool):
    assert _is_write_statement(sql) is expected


def test_gate_wait_ms_is_recorded_on_contention(tmp_path):
    eng = _make_wal_engine(tmp_path / "wait.db")
    try:
        with eng.begin() as conn:
            conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)"))
        reset_sqlite_metrics()
        SF = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
        holding = threading.Event()

        def holder():
            with SF() as db:
                db.execute(text("INSERT INTO items (val) VALUES ('first')"))
                holding.set()
                time.sleep(0.08)
                db.commit()

        t = threading.Thread(target=holder)
        t.start()
        assert holding.wait(5)
        with SF() as db2:
            db2.execute(text("INSERT INTO items (val) VALUES ('second')"))
            db2.commit()
        t.join(5)

        metrics = snapshot_sqlite_metrics()
        assert metrics["gate_contentions"] >= 1.0
        assert metrics["gate_wait_ms"] >= 20.0
    finally:
        eng.dispose()


def test_sqlite_savepoint_atomicity_and_non_transactional_reads(tmp_path):
    """begin_nested() must roll back when outer session rolls back, while plain SELECT stays non-transactional."""
    eng = _make_wal_engine(tmp_path / "savepoint.db")
    try:
        with eng.begin() as conn:
            conn.execute(text("CREATE TABLE sp_items (id INTEGER PRIMARY KEY, v TEXT UNIQUE)"))
            conn.execute(text("INSERT INTO sp_items VALUES (1, 'seed')"))
        SF = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

        # 1) Plain SELECT does not open a SQLite transaction, so concurrent write + upgrade succeeds.
        with SF() as s1, SF() as s2:
            assert s1.execute(text("SELECT v FROM sp_items WHERE id=1")).scalar() == "seed"
            assert s1.connection().connection.driver_connection.in_transaction is False
            s2.execute(text("UPDATE sp_items SET v='from_s2' WHERE id=1"))
            s2.commit()
            s1.execute(text("UPDATE sp_items SET v='from_s1' WHERE id=1"))
            s1.commit()

        # 2) SELECT followed by begin_nested() INSERT then outer rollback MUST roll back!
        with SF() as s3:
            _ = s3.execute(text("SELECT count(*) FROM sp_items")).scalar()
            with s3.begin_nested():
                s3.execute(text("INSERT INTO sp_items VALUES (2, 'must_not_persist')"))
            s3.rollback()
        with eng.connect() as conn:
            assert conn.execute(text("SELECT id FROM sp_items ORDER BY id")).scalars().all() == [1]

        # 3) Outer write + failed nested savepoint + outer commit persists only outer write.
        with SF() as s4:
            s4.execute(text("INSERT INTO sp_items VALUES (3, 'outer_ok')"))
            with pytest.raises(IntegrityError):
                with s4.begin_nested():
                    s4.execute(text("INSERT INTO sp_items VALUES (4, 'nested_rolled_back')"))
                    s4.execute(text("INSERT INTO sp_items VALUES (5, 'outer_ok')"))
            s4.commit()
        with eng.connect() as conn:
            assert conn.execute(text("SELECT id FROM sp_items ORDER BY id")).scalars().all() == [1, 3]
    finally:
        eng.dispose()


def test_memory_router_trace_persists_in_isolated_session_without_holding_write_gate(tmp_path):
    """Context-building retrieval traces must not leave the caller session holding the SQLite write gate."""
    eng = _make_wal_engine(tmp_path / "trace.db")
    Base.metadata.create_all(eng)
    SF = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    try:
        with SF() as db:
            db.add(Workspace(id="ws-1", tenant_id="t-1", owner_user_id="u-1", name="w"))
            db.add(
                User(
                    id="u-1",
                    tenant_id="t-1",
                    username="alice",
                    username_normalized="alice",
                    password_hash="x",
                )
            )
            db.commit()

        with SF() as caller_db:
            router = MemoryRouter(MemoryHybridRetriever(caller_db), db=caller_db)
            scope = MemoryScopeContext(
                tenant_id="t-1",
                principal_user_id="u-1",
                workspace_id="ws-1",
                conversation_id="cs-1",
                node_ids=(),
            )
            retrieval = SimpleNamespace(candidates=[], excluded={}, degraded_modes=())
            trace_id = router._persist_trace(scope, "test query", (), [], retrieval, 5)
            assert trace_id is not None
            assert session_holds_write_gate(caller_db) is False
            # Another connection can immediately write without contention.
            with SF() as other_db:
                rows = list(other_db.scalars(select(MemoryRetrievalTrace)).all())
                assert len(rows) == 1 and rows[0].id == trace_id
    finally:
        eng.dispose()


def test_sqlite_pragmas_configured_on_connect(tmp_path):
    eng = _make_wal_engine(tmp_path / "pragmas.db")
    try:
        with eng.connect() as conn:
            assert str(conn.exec_driver_sql("PRAGMA journal_mode").scalar()).casefold() == "wal"
            assert int(conn.exec_driver_sql("PRAGMA foreign_keys").scalar()) == 1
            assert int(conn.exec_driver_sql("PRAGMA journal_size_limit").scalar()) == 67108864
            assert int(conn.exec_driver_sql("PRAGMA temp_store").scalar()) == 2
            assert int(conn.exec_driver_sql("PRAGMA cache_size").scalar()) == -16000
    finally:
        eng.dispose()


def test_hot_table_archive_and_retention_are_deterministic_and_batched(tmp_path, monkeypatch):
    """SQLite archive must move every eligible row exactly once even with tied timestamps."""
    from app.core import scheduler as scheduler_mod

    eng = _make_wal_engine(tmp_path / "archive.db")
    Base.metadata.create_all(eng)
    SF = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

    monkeypatch.setattr(scheduler_mod, "SessionLocal", SF)
    monkeypatch.setattr(scheduler_mod, "_should_defer_sweep", lambda _name: False)
    fake_settings = SimpleNamespace(
        stream_event_retention_enabled=True,
        stream_event_retention_days=30,
        hot_table_archive_enabled=True,
        hot_table_archive_days=30,
        hot_table_archive_batch_size=100,
    )
    monkeypatch.setattr(scheduler_mod, "get_settings", lambda: fake_settings)

    now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
    old_ts = now - timedelta(days=45)
    fresh_ts = now - timedelta(days=2)

    try:
        with SF() as db:
            # Verify sandbox_agent_events.created_at index exists on fresh schema.
            indexes = {
                row[0]
                for row in db.connection().exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sandbox_agent_events'"
                )
            }
            assert "ix_sandbox_agent_events_created_at" in indexes

            db.add(Workspace(id="ws-a", tenant_id="t-a", owner_user_id="u-a", name="a"))
            db.add(ChatSession(id="cs-a", workspace_id="ws-a", title="s"))
            db.add(
                Message(
                    id="m-old",
                    workspace_id="ws-a",
                    session_id="cs-a",
                    role="assistant",
                    status="completed",
                    content="done",
                    created_at=old_ts,
                    updated_at=old_ts,
                )
            )
            db.add(
                Message(
                    id="m-live",
                    workspace_id="ws-a",
                    session_id="cs-a",
                    role="assistant",
                    status="streaming",
                    content="live",
                    created_at=old_ts,
                    updated_at=old_ts,
                )
            )
            db.add(
                SandboxAgentTask(
                    id="task-1",
                    workspace_id="ws-a",
                    owner_user_id="u-a",
                    task_id="t-1",
                    chat_session_id="cs-a",
                    title="test",
                )
            )
            db.flush()

            # 230 old rows with identical timestamps (>2 batches of 100) + 5 fresh rows
            for seq in range(230):
                db.add(
                    SandboxAgentEvent(
                        id=f"sae-old-{seq:03d}",
                        workspace_id="ws-a",
                        task_id="task-1",
                        seq=seq,
                        event_type="stdout",
                        payload={"seq": seq},
                        created_at=old_ts,
                    )
                )
            for seq in range(5):
                db.add(
                    SandboxAgentEvent(
                        id=f"sae-new-{seq:03d}",
                        workspace_id="ws-a",
                        task_id="task-1",
                        seq=1000 + seq,
                        event_type="stdout",
                        payload={"seq": 1000 + seq},
                        created_at=fresh_ts,
                    )
                )
            db.add(
                MessageVersion(
                    id="mv-old",
                    workspace_id="ws-a",
                    message_id="m-old",
                    version=1,
                    status="completed",
                )
            )
            db.add(
                MessageVersion(
                    id="mv-live",
                    workspace_id="ws-a",
                    message_id="m-live",
                    version=1,
                    status="streaming",
                )
            )
            db.flush()
            for seq in range(15):
                db.add(
                    MessageStreamEvent(
                        id=f"mse-old-{seq:03d}",
                        workspace_id="ws-a",
                        session_id="cs-a",
                        message_id="m-old",
                        message_version_id="mv-old",
                        sequence=seq,
                        event_type="delta",
                        payload={},
                        created_at=old_ts,
                    )
                )
            db.add(
                MessageStreamEvent(
                    id="mse-live-0",
                    workspace_id="ws-a",
                    session_id="cs-a",
                    message_id="m-live",
                    message_version_id="mv-live",
                    sequence=0,
                    event_type="delta",
                    payload={},
                    created_at=old_ts,
                )
            )
            db.commit()

        # 1) Retention purges terminal message events and preserves live streaming message events.
        purged = scheduler_mod.run_stream_events_retention(now=now)
        assert purged == 15
        with SF() as db:
            remaining_mse = list(db.scalars(select(MessageStreamEvent.id)).all())
            assert remaining_mse == ["mse-live-0"]

        # 2) Hot-table archive moves all 230 tied-timestamp sandbox_agent_events with zero duplicates/losses.
        moved = scheduler_mod.run_hot_table_archive(now=now)
        assert moved["sandbox_agent_events"] == 230
        with SF() as db:
            hot_ids = set(db.scalars(select(SandboxAgentEvent.id)).all())
            archived_rows = db.execute(
                text("SELECT id FROM sandbox_agent_events_archive")
            ).scalars().all()
            assert len(archived_rows) == 230
            assert len(set(archived_rows)) == 230
            assert hot_ids == {f"sae-new-{seq:03d}" for seq in range(5)}
    finally:
        eng.dispose()


def test_sqlite_site_restore_journal_blocks_startup_and_recovers_on_interruption(tmp_path, monkeypatch):
    """An interrupted SQLite site restore leaves IN_PROGRESS journal, blocks startup, and rolls back cleanly."""
    monkeypatch.chdir(tmp_path)
    storage = tmp_path / "storage"
    memory = tmp_path / "memory"
    storage.mkdir()
    memory.mkdir()
    (storage / "orig.txt").write_text("original-storage", encoding="utf-8")
    (memory / "orig.md").write_text("original-memory", encoding="utf-8")

    db_path = tmp_path / "site.db"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE users(id TEXT PRIMARY KEY);
            CREATE TABLE workspaces(id TEXT PRIMARY KEY);
            CREATE TABLE provider_configs(id TEXT PRIMARY KEY);
            CREATE TABLE provider_secrets(provider_id TEXT PRIMARY KEY);
            CREATE TABLE auth_sessions(id TEXT PRIMARY KEY, revoked_at TEXT);
            CREATE TABLE files(object_key TEXT, size_bytes INTEGER, sha256 TEXT);
            INSERT INTO users VALUES ('u1');
            """
        )
        conn.commit()
    # Create stale WAL/SHM sidecar files alongside the DB.
    wal_path = db_path.with_name(db_path.name + "-wal")
    shm_path = db_path.with_name(db_path.name + "-shm")
    wal_path.write_bytes(b"stale-wal-junk")
    shm_path.write_bytes(b"stale-shm-junk")

    env_file = tmp_path / ".env"
    env_file.write_text("LEARNGRAPH_MASTER_KEY='orig-key'\n", encoding="utf-8")

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{db_path.as_posix()}",
        storage_root=storage,
        memory_root=memory,
        database_migration_state_dir=str(tmp_path / "migration"),
        secret_provider="environment",
        master_key="orig-key",
    )

    # Simulate an interrupted restore right after roots were swapped and journal written.
    recovery_id = "interrupted123"
    db_before = db_path.with_name(db_path.name + ".before-site-restore-" + recovery_id)
    with closing(sqlite3.connect(db_path)) as src, closing(sqlite3.connect(db_before)) as dst:
        src.backup(dst)
    env_before = env_file.with_name(env_file.name + ".before-site-restore-" + recovery_id)
    env_before.write_bytes(env_file.read_bytes())

    storage_prev = storage.with_name(storage.name + ".before-site-restore-" + recovery_id)
    storage.rename(storage_prev)
    storage.mkdir()
    (storage / "partial.txt").write_text("partial-new", encoding="utf-8")
    env_file.write_text("LEARNGRAPH_MASTER_KEY='half-written'\n", encoding="utf-8")

    journal_path = state_root(settings) / "site-restore.json"
    atomic_json(
        journal_path,
        {
            "id": recovery_id,
            "engine": "sqlite",
            "state": "IN_PROGRESS",
            "database": str(db_path),
            "database_before": str(db_before),
            "had_database": True,
            "env_file": str(env_file),
            "env_before": str(env_before),
            "had_env": True,
            "roots": [
                {"path": str(storage), "previous": str(storage_prev), "existed": True}
            ],
            "external": [],
        },
    )

    with pytest.raises(DatabaseActivationError, match="interrupted"):
        startup_url(settings)

    result = recover_sqlite_offline(settings)
    assert result["status"] == "rolled_back"
    assert read_json(journal_path)["state"] == "ROLLED_BACK"
    assert (storage / "orig.txt").read_text(encoding="utf-8") == "original-storage"
    assert not (storage / "partial.txt").exists()
    assert env_file.read_text(encoding="utf-8") == "LEARNGRAPH_MASTER_KEY='orig-key'\n"
    assert not wal_path.exists()
    assert not shm_path.exists()
    # Startup is now unblocked.
    assert startup_url(settings) == settings.database_url


def test_index_only_startup_repair_skips_pre_migration_backup():
    """Rebuilding a missing index should not trigger a full pre-migration DB snapshot."""
    database_mod.init_database()
    assert database_mod.engine.url.get_backend_name() == "sqlite"
    db_path = Path(str(database_mod.engine.url.database))
    backup_dir = db_path.parent / "migration-backups"
    before = set(backup_dir.glob("learngraph-*.db")) if backup_dir.exists() else set()

    with closing(sqlite3.connect(str(db_path))) as raw:
        raw.execute("DROP INDEX IF EXISTS ix_sandbox_agent_events_created_at")
        raw.commit()

    pending = database_mod.describe_pending_sqlite_migrations()
    assert any("ix_sandbox_agent_events_created_at" in item for item in pending)
    assert all(item.startswith("create index ") for item in pending)

    database_mod.init_database()

    after = set(backup_dir.glob("learngraph-*.db")) if backup_dir.exists() else set()
    assert after == before
    with closing(sqlite3.connect(str(db_path))) as raw:
        assert (
            raw.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' "
                "AND name='ix_sandbox_agent_events_created_at'"
            ).fetchone()
            is not None
        )


def test_database_maintenance_sqlite_backup_uses_paged_verified_copy(tmp_path):
    """Host updater SQLite backup must succeed on a WAL database and produce a valid archive."""
    from zipfile import ZipFile
    from app.services.database_maintenance import backup as maintenance_backup

    db_path = tmp_path / "maint.db"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE items (id INTEGER PRIMARY KEY, body TEXT);
            INSERT INTO items VALUES (1, 'hello');
            """
        )
        conn.commit()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{db_path.as_posix()}",
        storage_root=tmp_path / "storage",
        memory_root=tmp_path / "memory",
        database_migration_state_dir=str(tmp_path / "migration"),
        secret_provider="environment",
        master_key="test-master-key",
    )
    out = tmp_path / "backups" / "pre-update.zip"
    result = maintenance_backup(settings, out)
    assert result["engine"] == "sqlite"
    assert out.exists() and result["bytes"] == out.stat().st_size
    with ZipFile(out) as archive:
        assert archive.testzip() is None
        assert "database.sqlite3" in archive.namelist()

