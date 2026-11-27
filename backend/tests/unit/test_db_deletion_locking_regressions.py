from __future__ import annotations

import sqlite3
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session

from app.core.database import (
    Base,
    _WRITE_GATE_MARKER,
    install_sqlite_write_gate,
    session_holds_write_gate,
)
from app.core.database_routing import PrimaryDatabaseEngine
from app.core.errors import install_error_handlers
from app.core.security import Principal
from app.domain.memory_event_models import MemoryContextPackage
from app.domain.models import ChatSession, MemoryRecord, MemoryRevision, Workspace
from app.services.chat import ChatService
from app.services.memory_projector import ensure_memory_search_fts
from app.services.workflow import WorkflowService


def test_primary_database_engine_connect_does_not_deadlock_on_full_pool(tmp_path):
    """QueuePool checkin must not deadlock against a thread waiting in connect()."""
    db_path = tmp_path / "pool-deadlock.db"
    settings = SimpleNamespace(
        database_migration_state_dir=str(tmp_path),
        sqlite_read_only=False,
    )
    physical = create_engine(
        f"sqlite:///{db_path}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=2,
        connect_args={"check_same_thread": False},
    )
    engine = PrimaryDatabaseEngine(physical, settings)
    try:
        first = engine.connect()
        close_elapsed: list[float] = []

        def closer() -> None:
            time.sleep(0.1)
            started = time.monotonic()
            first.close()
            close_elapsed.append(time.monotonic() - started)

        thread = threading.Thread(target=closer)
        thread.start()
        started = time.monotonic()
        second = engine.connect()
        acquire_elapsed = time.monotonic() - started
        second.close()
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert close_elapsed and close_elapsed[0] < 0.5
        assert acquire_elapsed < 1.0
    finally:
        engine.dispose()


def test_yield_without_write_lock_flushes_uncommitted_write_before_next_token(tmp_path):
    """Streaming loop must commit/release the SQLite write gate before waiting on the next LLM token."""
    db_path = tmp_path / "stream-gate.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    install_sqlite_write_gate(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)")

    try:
        with Session(engine) as db:
            chat = object.__new__(ChatService)
            chat.db = db
            chat._stream_checkpoint_fn = None
            chat._stream_redo_fn = None
            chat._pending_event_snapshots = []
            chat._event_commit_count = 0
            chat._last_event_commit_at = time.monotonic()

            gate_held_during_wait: list[bool] = []

            def fake_provider():
                gate_held_during_wait.append(session_holds_write_gate(db))
                yield "token-1"
                # By the time we resume to produce token-2, the write flushed
                # while handling token-1 must already be committed.
                gate_held_during_wait.append(session_holds_write_gate(db))
                yield "token-2"

            received: list[str] = []
            for token in chat._yield_without_write_lock(fake_provider()):
                received.append(token)
                if token == "token-1":
                    db.execute(text("INSERT INTO items (val) VALUES ('part-started')"))
                    assert session_holds_write_gate(db) is True

            assert received == ["token-1", "token-2"]
            assert gate_held_during_wait == [False, False]
            # Confirm the connection marker was cleared by commit.
            conn = db.connection()
            assert not conn.info.get(_WRITE_GATE_MARKER)
    finally:
        engine.dispose()


def test_delete_session_scans_context_packages_once_regardless_of_memory_count():
    """Deleting a session with M memories must not scan memory_context_packages M+1 times."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            workspace = Workspace(id="w", tenant_id="t", owner_user_id="u", name="test")
            db.add(workspace)
            db.flush()
            db.add(ChatSession(id="s1", workspace_id="w", title="Session 1"))
            db.flush()
            for idx in range(5):
                mid = f"mem-{idx}"
                db.add(
                    MemoryRecord(
                        id=mid,
                        workspace_id="w",
                        tenant_id="t",
                        session_id="s1",
                        title=mid,
                        content_hash="hash",
                        relative_path=f"topics/{mid}.md",
                    )
                )
                db.flush()
                db.add(
                    MemoryRevision(
                        workspace_id="w",
                        memory_id=mid,
                        revision=1,
                        title=mid,
                        content="secret",
                        content_hash="hash",
                        actor_id="u",
                    )
                )
            db.add(
                MemoryContextPackage(
                    workspace_id="w",
                    request_hash="r",
                    query_hash="q",
                    scope_hash="s",
                    policy_version="1",
                    builder_version="1",
                    agent_id="main",
                    package_hash="h",
                    selected_ids_json=["mem-0", "mem-3", "other"],
                    injected_ids_json=["mem-1", "keep"],
                )
            )
            ensure_memory_search_fts(db)
            db.commit()

            package_selects = 0

            @event.listens_for(engine, "before_cursor_execute")
            def count_queries(_conn, _cursor, statement, *_args):
                nonlocal package_selects
                lowered = statement.lower()
                if lowered.lstrip().startswith("select") and "from memory_context_packages" in lowered:
                    package_selects += 1

            principal = Principal(user_id="u", username="test", tenant_id="t", session_id="login")
            workflow = WorkflowService(db, workspace, principal)
            workflow.delete_session("s1", "Session 1")

            assert package_selects == 1
            pkg = db.query(MemoryContextPackage).one()
            assert pkg.selected_ids_json == ["other"]
            assert pkg.injected_ids_json == ["keep"]
            assert pkg.outcome_status == "invalidated_by_forget"
    finally:
        engine.dispose()


def test_db_lock_and_pool_timeout_return_503_database_busy():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/pool-timeout")
    def raise_pool_timeout():
        raise SQLAlchemyTimeoutError("QueuePool limit reached")

    @app.get("/sqlite-locked")
    def raise_sqlite_locked():
        raise OperationalError(
            "DELETE FROM chat_sessions",
            {},
            sqlite3.OperationalError("database is locked"),
        )

    client = TestClient(app)

    r1 = client.get("/pool-timeout")
    assert r1.status_code == 503
    assert r1.headers.get("retry-after") == "1"
    assert r1.json()["error"]["code"] == "database_busy"

    r2 = client.get("/sqlite-locked")
    assert r2.status_code == 503
    assert r2.headers.get("retry-after") == "1"
    assert r2.json()["error"]["code"] == "database_busy"
