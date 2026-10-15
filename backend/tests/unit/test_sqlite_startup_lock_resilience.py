"""SQLite 启动期写锁韧性 + 旧库自动迁移安全网。

背景：`docker compose` 在 app 变 healthy 的同一刻启动 preview 容器，而那一刻
app 内嵌的每个 scheduler 正好发出第一轮 sweep（写事务可能跨模型调用长期持有
唯一的 SQLite 写锁）。busy_timeout 只有 2s（822c760 从 10s 下调），于是 preview
启动路径上的 DDL（`DROP TRIGGER IF EXISTS session_messages_fts_update`）直接抛
`database is locked`，uvicorn 以 exit code 3 退出并进入重启循环。

这里盯住三件事：
1) 稳态（预检为空）启动是纯读：兄弟进程持写锁也能起来，不取锁、不写库、不备份；
2) 确实需要 DDL（升级/重建）时按退避重试，而不是让容器退出；
3) 升级前先落一份在线快照（迁移前状态），给旧库一个原子回滚点。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from app.core.database import describe_pending_sqlite_migrations
from app.core.database import engine
from app.core.database import init_database
from app.core.database import snapshot_sqlite_metrics
from app.core.database import _prune_sqlite_migration_backups
from app.core.config import get_settings

_HOLD_SECONDS = 3.0
_FTS_UPDATE_TRIGGER = "session_messages_fts_update"
_STREAMING_MARKER = "old.status <> 'streaming'"


def _database_path() -> Path:
    assert engine.url.get_backend_name() == "sqlite"
    path = Path(str(engine.url.database))
    assert path.exists(), f"expected the pytest scratch database at {path}"
    return path


def _backup_dir() -> Path:
    return _database_path().parent / "migration-backups"


def _snapshots() -> list[Path]:
    return sorted(_backup_dir().glob("learngraph-*.db"))


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=0.5, isolation_level=None)
    connection.execute("PRAGMA busy_timeout=0")
    return connection


class _SiblingWriter:
    """第二个连接（模拟兄弟进程的 sweep）持有唯一的 SQLite 写锁。"""

    def __init__(self, path: Path) -> None:
        self._connection = _connect(path)

    def __enter__(self) -> _SiblingWriter:
        self._connection.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, *exc_info: object) -> bool:
        # 测试非真失败：无论如何都要放开锁，避免污染同会话后续用例。
        try:
            self._connection.execute("ROLLBACK")
        finally:
            self._connection.close()
        return False


def _stored_trigger_sql(name: str) -> str | None:
    with _connect(_database_path()) as raw:
        row = raw.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (name,)
        ).fetchone()
    return None if row is None or row[0] is None else str(row[0])


def test_init_database_needs_no_write_lock_when_schema_is_current():
    """稳态启动必须是纯读：兄弟进程持写锁时也要能起来（本次 bug 的正解）。"""

    init_database()  # 先把 scratch 库带到最新 schema

    with _SiblingWriter(_database_path()):
        init_database()

    assert _STREAMING_MARKER in (_stored_trigger_sql(_FTS_UPDATE_TRIGGER) or "")


def test_init_database_retries_a_write_locked_by_a_sibling():
    """需要真写（重建索引）时，兄弟进程释放写锁前按退避重试而非抛错。"""

    init_database()
    path = _database_path()
    with _connect(path) as raw:
        raw.execute("DROP INDEX IF EXISTS ix_messages_workspace_session_created")

    release = threading.Event()

    def _hold() -> None:
        connection = _connect(path)
        connection.execute("BEGIN IMMEDIATE")
        release.wait(30)
        connection.execute("ROLLBACK")
        connection.close()

    holder = threading.Thread(target=_hold, daemon=True)
    holder.start()
    time.sleep(0.2)  # 让持锁线程先拿到锁
    timer = threading.Timer(_HOLD_SECONDS, release.set)
    timer.start()

    before = snapshot_sqlite_metrics()["locked_retries"]
    try:
        init_database()  # 第一次写必然撞上 2s busy_timeout，之后重试成功
    finally:
        release.set()
        timer.cancel()
        holder.join(10)

    assert snapshot_sqlite_metrics()["locked_retries"] > before, (
        "启动路径没有走重试：busy_timeout 到点后应该退避重试而不是抛错"
    )
    with _connect(path) as raw:
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'ix_messages_workspace_session_created'"
        ).fetchone() is not None


def test_stale_fts_update_trigger_is_upgraded_once():
    """旧形状的触发器仍要被改写一次（定义门控不能吃掉升级路径）。"""

    init_database()
    with engine.begin() as connection:
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {_FTS_UPDATE_TRIGGER}")
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER {_FTS_UPDATE_TRIGGER}
            AFTER UPDATE OF content, status, role, session_id, workspace_id ON messages
            BEGIN
              DELETE FROM session_messages_fts WHERE message_id = old.id;
            END
            """
        )
    assert _STREAMING_MARKER not in (_stored_trigger_sql(_FTS_UPDATE_TRIGGER) or "")

    init_database()

    assert _STREAMING_MARKER in (_stored_trigger_sql(_FTS_UPDATE_TRIGGER) or "")


def test_precheck_is_empty_and_takes_no_snapshot_when_schema_is_current():
    """稳态：预检为空 → 不取写锁、不写库、不落快照（避免每次启动都堆备份）。"""

    init_database()
    assert describe_pending_sqlite_migrations() == []

    before = set(_snapshots())
    init_database()
    assert set(_snapshots()) == before, "稳态启动不应产生迁移快照"
    assert _STREAMING_MARKER in (_stored_trigger_sql(_FTS_UPDATE_TRIGGER) or "")


def test_precheck_reports_every_kind_of_pending_change():
    """旧库的待办要能被只读预检逐项报出来（供升级前 dry-run / 决定是否备份）。"""

    init_database()
    path = _database_path()
    with _connect(path) as raw:
        raw.execute("DROP TABLE IF EXISTS advisory_locks")
        raw.execute("DELETE FROM schema_revisions WHERE revision = ?", ("v1.8.0",))
        raw.execute(f"DROP TRIGGER IF EXISTS {_FTS_UPDATE_TRIGGER}")
        raw.execute(
            f"CREATE TRIGGER {_FTS_UPDATE_TRIGGER} "
            "AFTER UPDATE ON messages BEGIN SELECT 1; END"
        )

    pending = describe_pending_sqlite_migrations()
    joined = " | ".join(pending)
    assert "advisory_locks" in joined, joined
    assert "v1.8.0" in joined, joined
    assert _FTS_UPDATE_TRIGGER in joined, joined

    init_database()  # 复原，避免影响同会话其它用例
    assert describe_pending_sqlite_migrations() == []


def test_upgrade_takes_a_rollback_snapshot_of_the_pre_migration_state():
    """升级前快照必须存在，且是"迁移前"的状态（这才是能回滚的东西）。"""

    init_database()
    path = _database_path()
    directory = _backup_dir()
    before = set(_snapshots())

    # 造一个旧库：缺表 + 缺版本账本行
    with _connect(path) as raw:
        raw.execute("DROP TABLE IF EXISTS advisory_locks")
        raw.execute("DELETE FROM schema_revisions WHERE revision = ?", ("v1.8.0",))

    pending = describe_pending_sqlite_migrations()
    assert any("advisory_locks" in item for item in pending)
    assert any("v1.8.0" in item for item in pending)

    init_database()

    created = sorted(set(_snapshots()) - before)
    assert len(created) == 1, f"应恰好产生一份快照，实际 {created}"
    snapshot = created[0]
    assert snapshot.parent == directory

    with _connect(snapshot) as raw:
        # 快照 = 迁移前：表还没建、账本行还缺
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'advisory_locks'"
        ).fetchone() is None
        assert raw.execute(
            "SELECT 1 FROM schema_revisions WHERE revision = 'v1.8.0'"
        ).fetchone() is None
        # 但业务数据必须在（快照不是空壳）
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone() is not None

    with _connect(path) as raw:
        assert raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'advisory_locks'"
        ).fetchone() is not None
        assert raw.execute(
            "SELECT 1 FROM schema_revisions WHERE revision = 'v1.8.0'"
        ).fetchone() is not None
    assert describe_pending_sqlite_migrations() == []


def test_snapshot_retention_keeps_the_newest(monkeypatch, tmp_path):
    """保留策略只清理本机制自己的快照，且保留最新 N 份。"""

    settings = get_settings()
    monkeypatch.setattr(settings, "sqlite_migration_backup_keep", 2)
    directory = tmp_path / "migration-backups"
    directory.mkdir()
    for index in range(4):
        snapshot = directory / f"learngraph-v1.8.0-2026010{index}-000000.db"
        snapshot.write_bytes(b"x")
        snapshot.touch()
        # 拉开 mtime，避免同秒排序不稳定
        import os

        os.utime(snapshot, (1_700_000_000 + index, 1_700_000_000 + index))
    (directory / "unrelated.db").write_bytes(b"x")

    _prune_sqlite_migration_backups(directory)

    remaining = sorted(path.name for path in directory.glob("*.db"))
    assert remaining == ["learngraph-v1.8.0-20260102-000000.db", "learngraph-v1.8.0-20260103-000000.db", "unrelated.db"]


def test_snapshot_can_be_disabled(monkeypatch):
    """关掉安全网后不再落快照（给不想每次升级留副本的运维）。"""

    settings = get_settings()
    monkeypatch.setattr(settings, "sqlite_migration_backup_enabled", False)
    init_database()
    path = _database_path()
    with _connect(path) as raw:
        raw.execute("DROP TABLE IF EXISTS advisory_locks")
    before = set(_snapshots())

    init_database()

    assert set(_snapshots()) == before
    assert describe_pending_sqlite_migrations() == []
