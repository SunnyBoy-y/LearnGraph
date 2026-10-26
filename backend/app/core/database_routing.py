"""Stable SQLAlchemy bind and durable, one-way primary database activation.

Every existing Session factory keeps this Engine object, rather than a physical
SQLite Engine. A cutover drains checked-out connections without restarting the
API, SSE producers, task queue or preview process. A failed drain changes no
routing state. Once the SQLite source fence commits SQLite is never a
runtime fallback, including after a crash or a lost activation response.
"""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from contextlib import closing
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.engine.base import OptionEngine

from app.core.security import SecretCipher
from app.core.secret_store import secret_store_from_settings


class DatabaseActivationError(RuntimeError):
    pass


def state_root(settings: Any) -> Path:
    configured = getattr(settings, "database_migration_state_dir", "")
    return (Path(configured) if configured else settings.storage_root.parent / "database-migration").resolve()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}-{threading.get_ident()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(0o600)
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 19:
                    raise
                # A concurrent state reader/antivirus may briefly hold a
                # Windows file handle without FILE_SHARE_DELETE.
                time.sleep(0.01)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise DatabaseActivationError("Invalid database migration state; refusing SQLite fallback")
    return value


def encrypt_url(settings: Any, url: str) -> dict[str, Any]:
    store = secret_store_from_settings(settings)
    key = store.active_key(create=True)
    return {"url_ciphertext": SecretCipher(key.secret).encrypt(url), "key_provider": store.provider_name, "key_version": key.version}


def decrypt_url(settings: Any, record: dict[str, Any]) -> str:
    store = secret_store_from_settings(settings, provider_name=record["key_provider"])
    value = SecretCipher(store.key(int(record["key_version"])).secret).decrypt(record["url_ciphertext"])
    if make_url(value).get_backend_name() != "postgresql":
        raise DatabaseActivationError("Only PostgreSQL can be activated")
    return value


def postgres_engine(url: str, *, read_only: bool = False, settings: Any = None) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        raise DatabaseActivationError("Only PostgreSQL is a supported migration target")
    if parsed.drivername == "postgresql":
        url = parsed.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    connect_args: dict[str, Any] = {"connect_timeout": 5}
    if read_only:
        connect_args["options"] = (str(parsed.query.get("options", "")) + " -c default_transaction_read_only=on").strip()
    return create_engine(url, pool_pre_ping=True, pool_size=getattr(settings, "sqlite_pool_size", 15),
        max_overflow=getattr(settings, "sqlite_pool_max_overflow", 25), pool_timeout=getattr(settings, "sqlite_pool_timeout_seconds", 5),
        hide_parameters=True, connect_args=connect_args)


def source_is_sealed(path: str | Path) -> bool:
    path = Path(path)
    if not path.is_file():
        return False
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='_lg_database_fence'").fetchone():
            return False
        row = connection.execute("SELECT sealed FROM _lg_database_fence WHERE singleton=1").fetchone()
        return bool(row and row[0])


class RouteLease:
    """Cross-process transaction barrier for API workers and the preview reader."""

    def __init__(self, root: Path, *, exclusive: bool, timeout: float | None = None):
        root.mkdir(parents=True, exist_ok=True)
        self.handle = (root / "route.lock").open("a+b")
        if os.name == "nt":
            # Windows development uses one writer process. Linux Docker uses
            # shared flock leases across every worker, including preview.
            self.handle.close()
            self.handle = None
            return
        import fcntl
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(self.handle, operation | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError("Other database participants have active transactions")
                    time.sleep(0.01)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class OperationLease:
    """One migration executor for all API workers; OS releases it on crash."""

    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.handle = (root / "executor.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                if not self.handle.read(1):
                    self.handle.write(b"1")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise BlockingIOError("A primary database migration executor is running") from exc

    def close(self):
        if not self.handle.closed:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()


def recover_activation(settings: Any, record: dict[str, Any]) -> str:
    """Resolve commit uncertainty from durable facts, never from HTTP results."""
    source = record.get("source_path")
    if not source or not Path(source).is_file():
        raise DatabaseActivationError("Activation source proof is missing; SQLite fallback is forbidden")
    with closing(sqlite3.connect(Path(source).as_uri() + "?mode=ro", uri=True)) as frozen:
        try:
            fence = frozen.execute("SELECT sealed,job_id FROM _lg_database_fence WHERE singleton=1").fetchone()
        except sqlite3.Error as exc:
            raise DatabaseActivationError("Activation source fence is missing") from exc
        if not fence or fence[1] != record["job_id"]:
            raise DatabaseActivationError("Activation source identity mismatch")
    if not fence[0]:
        # PostgreSQL's marker is prepared before the final SQLite fence. Until
        # that fence commits no target business transaction can be accepted.
        (state_root(settings) / "active.json").unlink(missing_ok=True)
        return settings.database_url
    if not record.get("verified"):
        raise DatabaseActivationError("Verified activation proof is missing")
    directory = state_root(settings) / "backups" / record["job_id"]
    for filename, field in (("primary.sqlite3", "backup_sha256"), ("recovery.json", "recovery_sha256")):
        digest = hashlib.sha256()
        with (directory / filename).open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != record.get(field):
            raise DatabaseActivationError("Backup proof changed; activation recovery blocked")
    with closing(sqlite3.connect(Path(source).as_uri() + "?mode=ro", uri=True)) as frozen:
        fence_id = frozen.execute("SELECT job_id FROM _lg_database_fence WHERE singleton=1").fetchone()[0]
        source_commit = frozen.execute("SELECT COALESCE(MAX(id),0) FROM _lg_capture_commits").fetchone()[0]
        if fence_id != record["job_id"] or int(source_commit) != int(record["final_commit"]):
            raise DatabaseActivationError("Frozen source identity or checkpoint mismatch")
    url = decrypt_url(settings, record)
    target = postgres_engine(url)
    try:
        with target.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(684728190124)"))
            activated = connection.execute(text("SELECT job_id FROM _lg_database_activation WHERE singleton=1")).scalar()
            if activated != record["job_id"]:
                raise DatabaseActivationError("PostgreSQL activation identity mismatch")
            checkpoint = connection.execute(text("SELECT applied_commit FROM _lg_database_copy WHERE job_id=:job"), {"job": record["job_id"]}).scalar()
            if checkpoint is None or int(checkpoint) != int(record["final_commit"]):
                raise DatabaseActivationError("Activation checkpoint mismatch; SQLite remains fenced")
        record = {**record, "phase": "postgresql_active"}
        atomic_json(state_root(settings) / "active.json", record)
        return url
    except DatabaseActivationError:
        raise
    except Exception as exc:
        raise DatabaseActivationError("PostgreSQL activation requires recovery; SQLite fallback is forbidden") from exc
    finally:
        target.dispose()


def startup_url(settings: Any) -> str:
    with RouteLease(state_root(settings), exclusive=False):
        return _startup_url(settings)


def _startup_url(settings: Any) -> str:
    restoration = read_json(state_root(settings) / "site-restore.json")
    if restoration and restoration.get("state") not in {"COMPLETED", "ROLLED_BACK"}:
        raise DatabaseActivationError("Offline site restore was interrupted; run site_restore --recover-pending --server-stopped before starting services")
    record = read_json(state_root(settings) / "active.json")
    if record:
        if record.get("phase") == "postgresql_active":
            url = decrypt_url(settings, record)
            target = None
            try:
                target = postgres_engine(url, read_only=True)
                with target.connect() as connection:
                    if connection.execute(text("SELECT job_id FROM _lg_database_activation WHERE singleton=1")).scalar_one() != record["job_id"]:
                        raise DatabaseActivationError("Active PostgreSQL identity mismatch")
            except Exception as exc:
                raise DatabaseActivationError("Active PostgreSQL unavailable or replaced; SQLite fallback is forbidden") from exc
            finally:
                if target is not None:
                    target.dispose()
            return url
        return recover_activation(settings, record)
    configured = make_url(settings.database_url)
    if configured.get_backend_name() == "sqlite" and configured.database and configured.database != ":memory:":
        if source_is_sealed(Path(configured.database).resolve()):
            raise DatabaseActivationError("SQLite has been permanently migrated; restore the activation manifest, not the SQLite route")
    return settings.database_url


class PrimaryDatabaseEngine(Engine):
    """An Engine-compatible stable bind, shared by captured Session factories."""

    def __init__(self, physical: Engine, settings: Any):
        super().__init__(physical.pool, physical.dialect, physical.url, echo=physical.echo, hide_parameters=True)
        self.physical = physical
        self.settings = settings
        self._condition = threading.Condition(threading.RLock())
        self._borrowed = 0
        self._barrier = False
        self._uncertain = False
        self._last_manifest_check = 0.0
        self._manifest_identity = ""
        self._track(physical)

    def _track(self, physical: Engine) -> None:
        @event.listens_for(physical.pool, "checkout")
        def checkout(_dbapi, record, _proxy):
            with self._condition:
                self._borrowed += 1
                record.info["_lg_primary_borrowed"] = True

        def release(_dbapi, record, *_args):
            with self._condition:
                lease = record.info.pop("_lg_route_lease", None)
                if lease is not None:
                    lease.close()
                if record.info.pop("_lg_primary_borrowed", False):
                    self._borrowed -= 1
                    self._condition.notify_all()

        event.listen(physical.pool, "checkin", release)
        event.listen(physical.pool, "invalidate", release)

    def _refresh_route(self) -> None:
        # The preview is a separate, read-only process using the same manifest.
        if self.physical.dialect.name == "postgresql":
            return
        self._last_manifest_check = time.monotonic()
        record = read_json(state_root(self.settings) / "active.json")
        if record and record.get("phase") == "activation_pending":
            recover_activation(self.settings, record)
            record = read_json(state_root(self.settings) / "active.json")
        if record and record.get("phase") == "postgresql_active" and record["job_id"] != self._manifest_identity:
            self.activate(postgres_engine(decrypt_url(self.settings, record), read_only=self.settings.sqlite_read_only, settings=self.settings), record["job_id"])

    def connect(self):
        with self._condition:
            while self._barrier:
                self._condition.wait(0.1)
            if self._uncertain:
                raise DatabaseActivationError("Database activation is recovering; SQLite fallback is forbidden")
            lease = RouteLease(state_root(self.settings), exclusive=False) if self.physical.dialect.name == "sqlite" else None
            try:
                self._refresh_route()
                connection = self.physical.connect()
                connection._lg_physical_dispatch = connection.dispatch
                connection.dispatch = type(connection.dispatch)(connection.dispatch, self.dispatch)
                connection._has_events = connection._has_events or self._has_events
                # SessionTransaction caches a bind by Connection.engine. Keep
                # the public identity here as well, or a second flush would
                # open a second SQLite transaction and lock against itself.
                connection.engine = self
                if lease is not None:
                    connection.info["_lg_route_lease"] = lease
                return connection
            except BaseException:
                if lease is not None:
                    lease.close()
                raise

    def raw_connection(self):
        with self._condition:
            while self._barrier:
                self._condition.wait(0.1)
            if self._uncertain:
                raise DatabaseActivationError("Database activation is recovering")
            lease = RouteLease(state_root(self.settings), exclusive=False) if self.physical.dialect.name == "sqlite" else None
            try:
                self._refresh_route()
                connection = self.physical.raw_connection()
                if lease is not None:
                    connection.info["_lg_route_lease"] = lease
                return connection
            except BaseException:
                if lease is not None:
                    lease.close()
                raise

    def execution_options(self, **options):
        return RoutingOptionEngine(self, options)

    @contextmanager
    def drain(self, timeout: float = 0.75):
        with self._condition:
            self._barrier = True
            deadline = time.monotonic() + timeout
            while self._borrowed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._barrier = False
                    self._condition.notify_all()
                    raise TimeoutError("Active database transactions did not drain; source remains active")
                self._condition.wait(remaining)
        try:
            with RouteLease(state_root(self.settings), exclusive=True, timeout=max(0, deadline - time.monotonic())):
                yield
        finally:
            with self._condition:
                self._barrier = False
                self._condition.notify_all()

    def activate(self, target: Engine, job_id: str) -> None:
        with self._condition:
            if target.dialect.name != "postgresql":
                raise DatabaseActivationError("Reverse database migration is forbidden")
            if self.physical.dialect.name == "postgresql":
                if self._manifest_identity and self._manifest_identity != job_id:
                    raise DatabaseActivationError("A primary database is already active")
                self._uncertain = False
                return
            if not self.settings.sqlite_read_only:
                install_postgres_writer_order(target)
            previous = self.physical
            self._track(target)
            self.physical = target
            self.pool, self.dialect, self.url = target.pool, target.dialect, target.url
            self.settings.database_url = target.url.render_as_string(hide_password=False)
            database_module = sys.modules.get("app.core.database")
            if database_module is not None and getattr(database_module, "engine", None) is self:
                database_module.is_sqlite = False
                database_module.database_url = target.url
            self._manifest_identity = job_id
            self._uncertain = False
            previous.dispose()

    def activation_uncertain(self) -> None:
        self._uncertain = True

    def dispose(self, close: bool = True) -> None:
        self.physical.dispose(close=close)
        self.pool = self.physical.pool


class RoutingOptionEngine(OptionEngine):
    @property
    def url(self):
        return self._proxied.url

    @url.setter
    def url(self, _value):
        pass

    @property
    def dialect(self):
        return self._proxied.dialect

    @dialect.setter
    def dialect(self, _value):
        pass

    def connect(self):
        connection = self._proxied.connect().execution_options(**self._execution_options)
        connection.dispatch = type(connection._lg_physical_dispatch)(connection._lg_physical_dispatch, self.dispatch)
        connection.engine = self
        return connection

    def raw_connection(self):
        return self._proxied.raw_connection()

    def execution_options(self, **options):
        return RoutingOptionEngine(self._proxied, {**self._execution_options, **options})


def install_postgres_writer_order(engine: Engine) -> None:
    """Preserve SQLite's write ordering for event positions and durable leases."""
    if getattr(engine, "_lg_writer_order_installed", False):
        return
    engine._lg_writer_order_installed = True
    @event.listens_for(engine, "before_cursor_execute")
    def before(connection, _cursor, statement, _parameters, _context, _many):
        prefix = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        write = prefix in {"INSERT", "UPDATE", "DELETE", "REPLACE"}
        if prefix == "WITH":
            import re
            write = bool(re.search(r"\b(?:INSERT|UPDATE|DELETE)\b", statement, re.I))
        if write and not connection.info.get("_lg_pg_writer"):
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(684728190123)")
            connection.info["_lg_pg_writer"] = True

    def clear(connection):
        connection.info.pop("_lg_pg_writer", None)
    event.listen(engine, "commit", clear)
    event.listen(engine, "rollback", clear)
