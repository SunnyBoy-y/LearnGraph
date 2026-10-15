"""Regression tests for stream-event commit retry on transient SQLite locks.

背景（2026-08-13）：并发 agent 流 + 巨型 WAL autocheckpoint 让单个写锁被
持有数秒，``_append_event`` 的普通 commit 直接抛 ``database is locked`` 杀死
整个流（``provider_stream_failed``）。修复后 ``_commit_pending_stream_events``
在瞬时锁下 rollback → 重跑 checkpoint 回调 → 从内存快照重建事件行 → 重提交；
``_append_event`` 用分离的 envelope 记录，重放后 SSE 事件 id 不变。

本测试用两个 SQLite 连接模拟写锁竞争，验证：
1. 锁窗口内 flush 会自动重试而不是抛错；
2. 重试后事件行与快照 id 一致、无重复；
3. 超过重试预算的持续锁仍会抛出（不吞错）。
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

# 必须先 import，让 memory 事件表注册进 Base.metadata（create_all 才建表）。
from app.domain import memory_event_models  # noqa: F401
from app.domain.models import (
    ChatSession,
    Message,
    MessageStreamEvent,
    MessageVersion,
)
from app.services.chat import ChatService


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 1},
    )

    @sa_event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=300")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    from app.domain import extension_models, models  # noqa: F401

    from app.core.database import Base

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def service(engine):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        provider = SimpleNamespace(
            provider_id="test-provider",
            remote_capability=False,
            model_id="test-model",
        )
        svc = ChatService(db, "ws-1", "user-1", provider)
        session_row = ChatSession(
            workspace_id="ws-1", title="锁重试测试", session_kind="main"
        )
        db.add(session_row)
        db.flush()
        message = Message(
            workspace_id="ws-1", session_id=session_row.id, role="assistant", content=""
        )
        db.add(message)
        db.flush()
        version = MessageVersion(
            workspace_id="ws-1",
            message_id=message.id,
            version=1,
            status="streaming",
        )
        db.add(version)
        db.commit()
        yield SimpleNamespace(
            svc=svc,
            session_id=session_row.id,
            message_id=message.id,
            version_id=version.id,
        )


def _hold_write_lock_short(engine, hold_seconds: float) -> None:
    """Open a second connection and hold the SQLite write lock briefly."""
    with engine.connect() as conn:
        conn.execute(sa_text("BEGIN IMMEDIATE"))
        conn.execute(sa_text("CREATE TABLE IF NOT EXISTS _lock_hold (id INTEGER)"))
        time.sleep(hold_seconds)
        conn.execute(sa_text("ROLLBACK"))


def _event_ids(db, message_id: str) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            sa_text(
                "SELECT id FROM message_stream_events "
                "WHERE message_id = :mid ORDER BY sequence"
            ),
            {"mid": message_id},
        ).fetchall()
    ]


def test_append_event_retries_through_transient_lock(service, engine) -> None:
    """锁窗口内 flush 自动重试，事件最终落库且与快照 id 一致。"""
    holder = threading.Thread(
        target=_hold_write_lock_short, args=(engine, 1.2), daemon=True
    )
    holder.start()

    svc = service.svc
    first = second = None
    try:
        svc._begin_event_batching()
        first = svc._append_event(
            session_id=service.session_id,
            message_id=service.message_id,
            message_version_id=service.version_id,
            part_id=None,
            sequence=1,
            event_type="part.started",
            payload={"part": {"id": "p1", "type": "text", "status": "streaming"}},
        )
        # 第二次 append 触发 flush（0.5s 时间窗已过或数量达标）；
        # 此时写锁被 holder 持有，flush 必须重试而不是抛错。
        second = svc._append_event(
            session_id=service.session_id,
            message_id=service.message_id,
            message_version_id=service.version_id,
            part_id=None,
            sequence=2,
            event_type="part.delta",
            payload={"part": {"id": "p1", "type": "text", "content_delta": "hi"}},
        )
        svc._end_event_batching()
    finally:
        holder.join(timeout=15)

    # 两个事件 id 与 SSE envelope 一致，且无重复
    assert first.event_id != second.event_id
    ids = _event_ids(service.svc.db, service.message_id)
    assert ids == [first.event_id, second.event_id], f"unexpected rows: {ids}"


def test_persistent_lock_still_raises(service, engine) -> None:
    """持续持有写锁超过重试预算时仍然抛错（不吞错）。"""
    holder = threading.Thread(
        target=_hold_write_lock_short, args=(engine, 12.0), daemon=True
    )
    holder.start()
    time.sleep(0.2)

    svc = service.svc
    try:
        svc._begin_event_batching()
        with pytest.raises(Exception) as excinfo:
            # 25 events × 0.05s ≈ 1.25s：超过 STREAM_EVENT_BATCH_SECONDS=1.0
            # 的 flush 节奏，批量窗内必然触发一次 commit，锁错误才会被抛出。
            for seq in range(1, 26):
                svc._append_event(
                    session_id=service.session_id,
                    message_id=service.message_id,
                    message_version_id=service.version_id,
                    part_id=None,
                    sequence=seq,
                    event_type="part.delta",
                    payload={"part": {"id": "p1", "type": "text", "content_delta": "x"}},
                )
                time.sleep(0.05)
        assert "locked" in str(excinfo.value).lower() or "locked" in repr(
            excinfo.value
        )
    finally:
        holder.join(timeout=15)


from sqlalchemy import text as sa_text  # noqa: E402


# ── v2.0: commit_with_locked_retry helper ─────────────────────────────────


def _locked_exc() -> Exception:
    import sqlite3

    from sqlalchemy.exc import OperationalError

    return OperationalError("INSERT ...", {}, sqlite3.OperationalError("database is locked"))


def test_commit_with_locked_retry_retries_and_redoes(engine, monkeypatch) -> None:
    """A locked commit is retried; the caller's redo re-applies pending rows."""
    from app.core.database import commit_with_locked_retry

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    calls = {"commit": 0, "redo": 0}
    real_commit = db.commit

    def flaky_commit() -> None:
        calls["commit"] += 1
        if calls["commit"] == 1:
            raise _locked_exc()
        real_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)

    def redo() -> None:
        calls["redo"] += 1

    commit_with_locked_retry(db, redo=redo, attempts=4, base_delay_seconds=0.01)
    assert calls["commit"] == 2
    assert calls["redo"] == 1
    db.close()


def test_commit_with_locked_retry_redo_lock_continues(engine, monkeypatch) -> None:
    """A redo that itself hits the lock continues retrying, not raising."""
    from app.core.database import commit_with_locked_retry

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    calls = {"commit": 0, "redo": 0}
    real_commit = db.commit

    def locked_commit() -> None:
        calls["commit"] += 1
        raise _locked_exc()

    monkeypatch.setattr(db, "commit", locked_commit)

    def locked_redo() -> None:
        calls["redo"] += 1
        raise _locked_exc()

    with pytest.raises(Exception) as excinfo:
        commit_with_locked_retry(db, redo=locked_redo, attempts=2, base_delay_seconds=0.01)
    assert "locked" in str(excinfo.value).lower()
    # Budget was 2 commits; the redo lock did not burn extra attempts.
    assert calls["commit"] == 2
    assert calls["redo"] == 1
    db.close()


def test_commit_with_locked_retry_non_lock_error_raises(engine, monkeypatch) -> None:
    """A non-lock commit error must propagate immediately (no retry)."""
    from app.core.database import commit_with_locked_retry

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()

    def bad_commit() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "commit", bad_commit)
    with pytest.raises(RuntimeError, match="boom"):
        commit_with_locked_retry(db, attempts=3, base_delay_seconds=0.01)
    db.close()
