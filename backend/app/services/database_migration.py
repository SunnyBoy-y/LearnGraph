"""Administrator-initiated, forward-only SQLite -> PostgreSQL main DB copy.

The HTTP API never runs a migration synchronously. Durable execution state is
kept outside the database being copied. All application traffic stays on the
source until backup, baseline equality, transaction replay and target checks
pass. The sandboxd database and object storage are not migration resources.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from itertools import groupby
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Index, JSON, LargeBinary, MetaData, Table, UniqueConstraint, inspect, text
from sqlalchemy.engine import Engine, make_url

from app.core.database_routing import (
    DatabaseActivationError, PrimaryDatabaseEngine, atomic_json, decrypt_url,
    encrypt_url, install_postgres_writer_order, postgres_engine, read_json,
    state_root, OperationLease, recover_activation,
)
from app.core.errors import AppError
from app.core.sqlite_capture import SQLiteCapture, quoted
from app.core.secret_store import MasterKeyNotFound, secret_store_from_settings


_controllers: dict[str, "DatabaseMigrationController"] = {}
_controllers_lock = threading.Lock()
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
_ARCHIVES = {"message_stream_events_archive": "message_stream_events", "sandbox_agent_events_archive": "sandbox_agent_events"}
_FTS = {"memory_search_fts", "document_chunks_fts", "session_messages_fts"}
_FTS_TRIGGERS = {"session_messages_fts_insert", "session_messages_fts_update", "session_messages_fts_delete", "session_messages_fts_session_update"}


def _source_tables(connection: sqlite3.Connection) -> dict[str, list[str]]:
    tables: dict[str, list[str]] = {}
    for name, sql in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"):
        if name.startswith(("sqlite_", "_lg_")) or name in _FTS or any(name.startswith(fts + "_") for fts in _FTS):
            continue
        if sql and "CREATE VIRTUAL TABLE" in sql.upper():
            raise ValueError(f"Unsupported virtual table: {name}")
        tables[name] = [row[1] for row in connection.execute(f"PRAGMA table_xinfo({quoted(name)})")]
    return tables


def _schema_fingerprint(connection: sqlite3.Connection) -> str:
    rows = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT GLOB '_lg_*' AND tbl_name NOT GLOB '_lg_*' AND name NOT GLOB 'sqlite_*' ORDER BY type,name").fetchall()
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def _capture_fingerprint(connection: sqlite3.Connection) -> str:
    rows = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name GLOB '_lg_capture_*' ORDER BY name").fetchall()
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def _predicate(value: Any) -> str:
    return re.sub(r'\s+|"', '', str('' if value is None else value)).casefold().strip('()')


def _unmapped_indexes(connection: sqlite3.Connection, metadata: MetaData, columns: dict) -> list[str]:
    issues = []
    for name in columns:
        if name not in metadata.tables:
            continue
        table = metadata.tables[name]
        unique = {(tuple(c.name for c in table.primary_key), '')}
        unique.update((tuple(c.name for c in constraint.columns), '') for constraint in table.constraints if isinstance(constraint, UniqueConstraint))
        unique.update((tuple(c.name for c in index.columns), _predicate(index.dialect_options['sqlite'].get('where'))) for index in table.indexes if index.unique)
        declared = {index.name for index in table.indexes}
        for _seq, index_name, is_unique, _origin, partial in connection.execute(f"PRAGMA index_list({quoted(name)})"):
            names = tuple(row[2] for row in connection.execute(f"PRAGMA index_info({quoted(index_name)})"))
            ddl = connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index_name,)).fetchone()
            predicate = _predicate(re.split(r'\bWHERE\b', ddl[0], maxsplit=1, flags=re.I)[1]) if partial and ddl and ddl[0] else ''
            # Startup's legacy additive migration creates redundant IS NOT
            # NULL unique indexes. Ordinary UNIQUE already ignores NULL keys
            # in both engines, so preserve the declared constraint once.
            guard_pattern = '(?:' + '|'.join(re.escape(str(c).casefold()) + 'isnotnull' for c in names) + ')'
            null_guard = bool(predicate) and bool(re.fullmatch(guard_pattern + '(?:and' + guard_pattern + ')*', predicate.replace('(', '').replace(')', '')))
            mapped_unique = (names, predicate) in unique or null_guard and (names, '') in unique
            if is_unique and not mapped_unique or not is_unique and index_name not in declared and (partial or None in names):
                issues.append(name + '.' + index_name)
    return issues


def _value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$bytes": bytes(value).hex()}
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return {"$decimal": str(value.normalize())}
    if isinstance(value, float) and not math.isfinite(value):
        return {"$float": repr(value)}
    return value


def _row_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: _value(v) for k, v in row.items()}, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _convert(row: dict[str, Any], table: Table) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in row.items():
        column = table.c[name]
        kind = column.type
        if value is None:
            if not column.nullable and not column.primary_key:
                raise ValueError(f"{table.name}.{name}: unexpected NULL")
        elif isinstance(kind, Boolean):
            if value not in (0, 1, False, True):
                raise ValueError(f"{table.name}.{name}: invalid boolean")
            value = bool(value)
        elif isinstance(kind, DateTime):
            if not isinstance(value, datetime):
                value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            value = value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
        elif isinstance(kind, Date):
            value = value if isinstance(value, date) else date.fromisoformat(str(value))
        elif isinstance(kind, JSON):
            # Preserve the original JSON text, rather than round-tripping through
            # the ORM or JSONB (which could discard duplicate keys/formatting).
            if not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False, allow_nan=False)
            json.loads(value)
        elif isinstance(kind, LargeBinary):
            value = bytes(value)
        elif isinstance(value, str):
            if "\x00" in value:
                raise ValueError(f"{table.name}.{name}: PostgreSQL cannot store NUL")
            length = getattr(kind, "length", None)
            if length and len(value) > length:
                raise ValueError(f"{table.name}.{name}: value exceeds declared length")
        result[name] = value
    return result


def _target_select(table: Table, columns: list[str]) -> str:
    # psycopg normally decodes JSON to Python objects. ::text keeps the original
    # serialized JSON for lossless, raw-storage verification.
    return ",".join(f"{quoted(name)}::text AS {quoted(name)}" if isinstance(table.c[name].type, JSON) else quoted(name) for name in columns)


def _digest_rows(rows, directory: Path) -> tuple[int, str]:
    """External sort of fixed-size hashes: bounded RAM even for large databases."""
    chunks: list[Path] = []
    batch: list[str] = []
    count = 0
    for row in rows:
        batch.append(_row_hash(row))
        count += 1
        if len(batch) == 30_000:
            chunk = directory / f"{len(chunks)}.hashes"
            chunk.write_text("\n".join(sorted(batch)) + "\n", encoding="ascii")
            chunks.append(chunk)
            batch = []
    if not chunks:
        return count, hashlib.sha256("\n".join(sorted(batch)).encode()).hexdigest()
    if batch:
        chunk = directory / f"{len(chunks)}.hashes"
        chunk.write_text("\n".join(sorted(batch)) + "\n", encoding="ascii")
        chunks.append(chunk)
    handles = [chunk.open(encoding="ascii") for chunk in chunks]
    digest = hashlib.sha256()
    try:
        for value in heapq.merge(*handles):
            digest.update(value.rstrip("\n").encode())
            digest.update(b"\n")
    finally:
        for handle in handles:
            handle.close()
        for chunk in chunks:
            chunk.unlink()
    return count, digest.hexdigest()


def _bounded_rows(cursor, max_bytes=8 * 1024 * 1024):
    """Bound a copy batch by bytes as well as row count (large JSON/text)."""
    rows, size = [], 0
    for row in cursor:
        rows.append(row)
        size += sum(len(value.encode('utf-8')) if isinstance(value, str) else len(value) if isinstance(value, bytes) else 16 for value in row)
        if len(rows) >= 500 or size >= max_bytes:
            break
    return rows


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while part := handle.read(1024 * 1024):
            digest.update(part)
    return digest.hexdigest()


def _online_snapshot(source: Path, destination: Path, progress=None) -> None:
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as live, closing(sqlite3.connect(destination)) as copy:
        # Pin a WAL read snapshot. Without an explicit read transaction the
        # backup API may restart every page batch under sustained source writes.
        live.execute("BEGIN")
        live.execute("SELECT count(*) FROM sqlite_master").fetchone()
        live.backup(copy, pages=256, progress=progress, sleep=0.01)
    destination.chmod(0o600)


class DatabaseMigrationController:
    def __init__(self, settings: Any, router: PrimaryDatabaseEngine, capture: SQLiteCapture | None, metadata: MetaData):
        self.settings, self.router, self.capture, self.metadata = settings, router, capture, metadata
        self.root = state_root(settings)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._activate_requested = threading.Event()
        self._target: Engine | None = None
        self._tables: dict[str, Table] = {}
        self._columns: dict[str, list[str]] = {}

    @property
    def state_path(self) -> Path:
        return self.root / "job.json"

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = read_json(self.state_path)
            public = {k: v for k, v in (state or {}).items() if k not in {"target_secret", "source_path", "source_schema"}}
            public.update({
                "active_database": self.router.physical.dialect.name,
                "direction": "sqlite_to_postgresql",
                "irreversible": self.router.physical.dialect.name == "postgresql" or bool(read_json(self.root / "active.json")),
                "agent_configured": bool(self.settings.database_migration_agent_url),
                "agent_auto_start": bool(getattr(self.settings, "database_migration_agent_auto_start", False)),
                "running": bool(self._thread and self._thread.is_alive()),
            })
            if public["active_database"] == "postgresql":
                public["state"] = "RECOVERING" if self.router._uncertain else "COMPLETED"
                if not self.router._uncertain:
                    public.pop("error", None)
            if not public["running"]:
                try:
                    lease = OperationLease(self.root)
                    lease.close()
                except BlockingIOError:
                    public["running"] = True
            return public

    def _phase(self, state: str, **metrics: Any) -> dict[str, Any]:
        with self._lock:
            record = read_json(self.state_path) or {}
            record.update(metrics)
            record.update(state=state, updated_at=datetime.now(timezone.utc).isoformat())
            events = list(record.get("events", []))
            if not events or events[-1]["state"] != state:
                events.append({"sequence": len(events) + 1, "state": state, "at": record["updated_at"]})
            record["events"] = events
            atomic_json(self.state_path, record)
            return record

    def preflight(self) -> dict[str, Any]:
        maintenance = read_json(self.root / "update.json")
        restoration = read_json(self.root / "site-restore.json")
        if maintenance or restoration and restoration.get("state") not in {"COMPLETED", "ROLLED_BACK"}:
            raise AppError(409, "database_maintenance_busy", "请先完成服务更新或全站恢复")
        if self.router.physical.dialect.name != "sqlite" or read_json(self.root / "active.json"):
            raise AppError(409, "database_already_migrated", "主业务库已启用 PostgreSQL，不支持反向迁移")
        url = self.router.physical.url
        if not url.database or url.database == ":memory:":
            raise AppError(409, "database_migration_source_invalid", "在线迁移需要持久化的 SQLite 主业务库")
        source = Path(url.database).resolve()
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as connection:
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            violations = connection.execute("PRAGMA foreign_key_check").fetchone()
            try:
                columns = _source_tables(connection)
            except ValueError as exc:
                raise AppError(409, "database_migration_schema_unsupported", str(exc)) from exc
            unknown = set(columns) - set(self.metadata.tables) - set(_ARCHIVES)
            missing = set(self.metadata.tables) - set(columns)
            mismatched = [name for name, names in columns.items() if name in self.metadata.tables and set(names) != set(self.metadata.tables[name].c.keys())]
            extra_triggers = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'") if row[0] not in _FTS_TRIGGERS and not row[0].startswith("_lg_capture_")]
            views = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='view'")]
            unmapped_checks = []
            for name in columns:
                ddl = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0] or ''
                expected = sum(isinstance(c, CheckConstraint) for c in self.metadata.tables[name].constraints) if name in self.metadata.tables else 0
                if len(re.findall(r'\bCHECK\s*\(', ddl, flags=re.I)) != expected:
                    unmapped_checks.append(name)
            maintenance = connection.execute("SELECT COUNT(*) FROM maintenance_locks WHERE status='active'").fetchone()[0] if "maintenance_locks" in columns else 0
            unmapped_indexes = _unmapped_indexes(connection, self.metadata, columns)
            checks = [
                {"key": "source_integrity", "passed": integrity == ["ok"] and not violations},
                {"key": "source_wal", "passed": connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal", "detail": "在线迁移要求 WAL，备份持有一致性读快照时仍允许业务写入"},
                {"key": "source_catalogue", "passed": not unknown and not missing, "unknown_tables": sorted(unknown), "missing_tables": sorted(missing)},
                {"key": "source_columns", "passed": not mismatched, "tables": mismatched},
                {"key": "source_custom_objects", "passed": not extra_triggers and not views and not unmapped_checks, "unmapped_triggers": extra_triggers, "unmapped_views": views, "unmapped_checks": unmapped_checks},
                {"key": "source_indexes", "passed": not unmapped_indexes, "unmapped_indexes": unmapped_indexes},
                {"key": "maintenance_conflicts", "passed": not maintenance, "active_locks": maintenance},
                {"key": "json_capture", "passed": connection.execute("SELECT json_valid('{\"check\":null}')").fetchone()[0] == 1},
                {"key": "backup_required", "passed": True, "detail": "备份完成并验证后才允许修改捕获结构或迁移"},
            ]
        try:
            secret_store_from_settings(self.settings).active_key(create=True)
            checks.append({"key": "deployment_key", "passed": True})
        except Exception:
            checks.append({"key": "deployment_key", "passed": False})
        updater_safe = True
        if self.settings.update_agent_url:
            try:
                token = Path(self.settings.update_agent_token_file).read_text().strip()
                with httpx.Client(timeout=5, trust_env=False) as client:
                    response = client.get(self.settings.update_agent_url.rstrip("/") + "/v1/status", headers={"Authorization": "Bearer " + token})
                    response.raise_for_status()
                    updater = response.json()
                updater_safe = updater.get("auto_install") is False and all(job.get("status") in {"succeeded", "failed", "rolled_back", "rollback_failed"} for job in updater.get("jobs", []))
            except Exception:
                updater_safe = False
        checks.append({"key": "updater_interlock", "passed": updater_safe, "detail": "旧 SQLite 更新器必须停止自动安装，且没有正在运行的更新任务"})
        return {"checks": checks, "ready": all(item["passed"] for item in checks), "tables": len(columns), "direction": "sqlite_to_postgresql"}

    def start(self, *, actor_id: str, workspace_id: str, managed: bool, target_url: str | None, auto_activate: bool) -> dict[str, Any]:
        lease = self._claim()
        try:
            return self._start(actor_id=actor_id, workspace_id=workspace_id, managed=managed, target_url=target_url, auto_activate=auto_activate, lease=lease)
        except BaseException:
            lease.close()
            raise

    def _claim(self) -> OperationLease:
        try:
            return OperationLease(self.root)
        except BlockingIOError as exc:
            raise AppError(409, "database_migration_busy", "已有主业务库迁移正在执行") from exc

    def _start(self, *, actor_id: str, workspace_id: str, managed: bool, target_url: str | None, auto_activate: bool, lease: OperationLease) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise AppError(409, "database_migration_busy", "已有主业务库迁移正在执行")
            prior = read_json(self.state_path)
            if prior and prior.get("state") not in _TERMINAL:
                raise AppError(409, "database_migration_pending", "请先恢复、切换或取消已有迁移")
            report = self.preflight()
            if not report["ready"]:
                raise AppError(409, "database_migration_preflight_failed", "预检未全部通过，主业务库仍使用 SQLite", report)
            if managed and not self.settings.database_migration_agent_url:
                raise AppError(409, "database_agent_unavailable", "请使用包含数据库迁移代理的 Docker 部署，或配置外部 PostgreSQL")
            if managed:
                if self.settings.database_migration_agent_auto_start:
                    self._control_agent("status")
                else:
                    self._agent_connection()
            if not managed and (not target_url or make_url(target_url).get_backend_name() != "postgresql"):
                raise AppError(422, "postgresql_configuration_required", "请先保存并验证 PostgreSQL 连接配置")
            job_id = uuid.uuid4().hex
            self._cancel.clear()
            self._activate_requested.clear()
            atomic_json(self.state_path, {"id": job_id, "state": "QUEUED", "actor_id": actor_id, "workspace_id": workspace_id,
                "managed": managed, "agent_auto_start": managed and self.settings.database_migration_agent_auto_start,
                "auto_activate": auto_activate, "source_path": str(Path(self.router.physical.url.database).resolve()),
                "target_secret": encrypt_url(self.settings, target_url) if target_url else None,
                "checks": report["checks"], "created_at": datetime.now(timezone.utc).isoformat(), "events": []})
            self._launch(self._run, lease=lease)
        return self.status()

    def _launch(self, operation, *, lease: OperationLease | None = None) -> None:
        ownership = lease or self._claim()
        def execute():
            try:
                operation()
            finally:
                ownership.close()
        self._thread = threading.Thread(target=execute, name="primary-database-migration", daemon=True)
        self._thread.start()

    def _ensure_not_cancelled(self, *, force: bool = False) -> None:
        if force or time.monotonic() - getattr(self, "_last_cancel_check", 0) > 0.2:
            self._last_cancel_check = time.monotonic()
            request = read_json(self.root / "cancel.json")
            if request and request.get("job_id") == (read_json(self.state_path) or {}).get("id"):
                self._cancel.set()
        if self._cancel.is_set():
            raise InterruptedError("Migration cancelled before activation")

    def _backup(self, source: Path, job: dict[str, Any]) -> Path:
        directory = self.root / "backups" / job["id"]
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        from app.services.backup_io import require_space
        # Backup, restored verification copy, consistent copy snapshot and WAL.
        require_space(directory, source.stat().st_size * 4)
        path = directory / "primary.sqlite3"
        self._phase("BACKING_UP", backup_verified=False)
        _online_snapshot(source, path, progress=lambda _status, remaining, total: self._backup_progress(remaining, total))
        self._phase("VERIFYING_BACKUP")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"] or connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Backup integrity verification failed")
            source_tables = _source_tables(connection)
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {quoted(table)}").fetchone()[0] for table in source_tables}
        # Opening a restored copy independently also verifies the restore path.
        with TemporaryDirectory(prefix="restore-check-", dir=directory) as temporary:
            restored = Path(temporary) / "restore.sqlite3"
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as original, closing(sqlite3.connect(restored)) as copy:
                original.backup(copy)
            if _file_hash(path) != _file_hash(restored):
                raise ValueError("Backup restore verification failed")
        digest = _file_hash(path)
        store = secret_store_from_settings(self.settings)
        active = store.active_key()
        try:
            identity = store.identity_key()
        except MasterKeyNotFound:
            identity = None
        recovery = {"provider": store.provider_name, "active_version": active.version,
            "keys": {str(version): store.key(version).secret for version in (range(1, active.version + 1) if store.provider_name == "keyring" else [active.version])},
            "identity": identity, "memory_event_master_key": self.settings.memory_event_master_key,
            "app_version": self.settings.app_version, "scope": "main_database_only"}
        atomic_json(directory / "recovery.json", recovery)
        # Verify decryption material as well as database restore, before capture.
        persisted = read_json(directory / "recovery.json")
        if persisted != recovery:
            raise ValueError("Backup recovery material verification failed")
        recovery_hash = _file_hash(directory / "recovery.json")
        atomic_json(directory / "manifest.json", {"job_id": job["id"], "sha256": digest, "recovery_sha256": recovery_hash, "bytes": path.stat().st_size, "tables": counts, "restore_verified": True})
        self._phase("BACKUP_VERIFIED", backup_verified=True, backup_sha256=digest, recovery_sha256=recovery_hash, backup_bytes=path.stat().st_size)
        return path

    def _backup_progress(self, remaining: int, total: int) -> None:
        if not hasattr(self, "_last_backup_progress") or time.monotonic() - self._last_backup_progress > 0.3 or remaining == 0:
            self._last_backup_progress = time.monotonic()
            self._phase("BACKING_UP", backup_pages_copied=total - remaining, backup_pages_total=total)
        self._ensure_not_cancelled()

    def _agent_connection(self) -> tuple[str, dict[str, str]]:
        base = self.settings.database_migration_agent_url.rstrip("/")
        try:
            token = Path(self.settings.database_migration_agent_token_file).read_text(encoding="utf-8").strip()
            if not base or len(token) < 32:
                raise ValueError("Migration agent is not configured")
            headers = {"Authorization": f"Bearer {token}"}
            with httpx.Client(timeout=3, trust_env=False) as client:
                client.get(base + "/v1/health", headers=headers).raise_for_status()
            return base, headers
        except (OSError, ValueError, httpx.HTTPError) as exc:
            raise AppError(
                409, "database_agent_unavailable",
                "数据库迁移代理未启动。请在宿主机执行 "
                "node scripts/compose.mjs up -d --no-deps database-migration-agent，"
                "等待健康后重试；迁移完成后执行 "
                "node scripts/compose.mjs stop database-migration-agent。也可使用外部 PostgreSQL。",
            ) from exc

    def _control_agent(self, operation: str, job_id: str | None = None) -> dict:
        from app.providers.remote.sandboxd_backend import SandboxdBackend
        try:
            result = SandboxdBackend(self.settings).migration_agent(operation, job_id)
            if operation == "status" and not result.get("enabled"):
                raise ValueError("Managed database control is disabled")
            return result
        except Exception:
            raise AppError(503, "database_agent_control_unavailable",
                "数据库迁移服务自动启停不可用，请检查 sandboxd 管理凭据并更新沙箱管理服务。") from None

    def _stop_managed_agent(self, job: dict) -> None:
        try:
            self._control_agent("stop", job["id"])
            state = "stopped"
        except AppError:
            # The daemon's persisted lease remains the crash-safe cleanup path.
            state = "cleanup_pending"
        current = read_json(self.state_path) or {}
        self._phase(current.get("state", "FAILED"), agent_state=state)

    def _managed_target(self, job: dict[str, Any]) -> str:
        automatic = job.get("agent_auto_start", False)
        try:
            if automatic:
                self._phase("STARTING_AGENT", agent_state="starting")
                self._control_agent("start", job["id"])
                deadline = time.monotonic() + 90
                while True:
                    self._ensure_not_cancelled()
                    try:
                        self._agent_connection()
                        break
                    except AppError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("数据库迁移服务启动超时") from None
                        self._cancel.wait(0.5)
                self._phase("AGENT_READY", agent_state="running")
            return self._provision_managed_target(job, automatic=automatic)
        finally:
            if automatic:
                self._stop_managed_agent(job)

    def _provision_managed_target(self, job: dict[str, Any], *, automatic: bool) -> str:
        base, headers = self._agent_connection()
        self._phase("PULLING_IMAGE")
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.post(f"{base}/v1/postgres/{job['id']}", headers=headers)
            response.raise_for_status()
            heartbeat = time.monotonic()
            while True:
                self._ensure_not_cancelled()
                if automatic and time.monotonic() - heartbeat >= 15:
                    self._control_agent("start", job["id"])
                    heartbeat = time.monotonic()
                response = client.get(f"{base}/v1/postgres/{job['id']}", headers=headers)
                response.raise_for_status()
                value = response.json()
                if value.get("state") == "failed":
                    raise RuntimeError("Managed PostgreSQL provisioning failed")
                if value.get("state") == "ready":
                    self._phase("TARGET_RUNNING", image_digest=value.get("image_digest"))
                    return str(value["database_url"])
                self._phase("STARTING_POSTGRESQL" if value.get("state") == "starting" else "PULLING_IMAGE", image_progress=value.get("progress", {}))
                self._cancel.wait(0.5)

    def _prepare_target(self, target: Engine, snapshot: sqlite3.Connection, job_id: str) -> None:
        columns = _source_tables(snapshot)
        metadata = MetaData()
        for name in columns:
            if name in self.metadata.tables:
                table = self.metadata.tables[name].to_metadata(metadata)
                missing = set(table.c.keys()) - set(columns[name])
                extra = set(columns[name]) - set(table.c.keys())
                if missing or extra:
                    # Unknown legacy columns must never silently disappear.
                    raise ValueError(f"{name}: schema columns require reconciliation (missing={sorted(missing)}, extra={sorted(extra)})")
            else:
                source = self.metadata.tables[_ARCHIVES[name]]
                from sqlalchemy import Column
                if not set(columns[name]) <= set(source.c.keys()):
                    raise ValueError(f"{name}: unknown archive columns")
                Table(name, metadata, *(Column(c, source.c[c].type.copy(), nullable=True) for c in columns[name]))
            table = metadata.tables[name]
            declared = {index.name for index in table.indexes}
            for _seq, index_name, unique, origin, partial in snapshot.execute(f"PRAGMA index_list({quoted(name)})"):
                if origin != 'c' or unique or partial or index_name in declared:
                    continue
                names = [row[2] for row in snapshot.execute(f"PRAGMA index_info({quoted(index_name)})")]
                if not names or any(c is None or c not in table.c for c in names):
                    raise ValueError(f"{name}: unsupported source index")
                Index(index_name, *(table.c[c] for c in names))
        inspector = inspect(target)
        existing = inspector.get_table_names() + inspector.get_view_names() + inspector.get_materialized_view_names() + inspector.get_sequence_names()
        if existing:
            raise ValueError("Target database must be empty; existing data will not be overwritten")
        for table in metadata.tables.values():
            for foreign_key in table.foreign_key_constraints:
                foreign_key.deferrable = True
                foreign_key.initially = "IMMEDIATE"
        with target.begin() as connection:
            metadata.create_all(connection)
            connection.execute(text("CREATE TABLE _lg_database_copy(job_id TEXT PRIMARY KEY, applied_commit BIGINT NOT NULL)"))
            connection.execute(text("CREATE TABLE _lg_database_activation(singleton INTEGER PRIMARY KEY CHECK(singleton=1),job_id TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO _lg_database_copy(job_id,applied_commit) VALUES(:job,-1)"), {"job": job_id})
        self._tables = dict(metadata.tables)
        self._columns = columns

    def _insert_sql(self, table: Table, columns: list[str], *, upsert: bool = False) -> str:
        sql = f"INSERT INTO {quoted(table.name)} ({','.join(quoted(c) for c in columns)}) VALUES ({','.join(':p'+str(i) for i in range(len(columns)))})"
        primary = [column.name for column in table.primary_key]
        if upsert and primary:
            updates = [name for name in columns if name not in primary]
            sql += f" ON CONFLICT ({','.join(quoted(c) for c in primary)}) "
            sql += "DO UPDATE SET " + ",".join(f"{quoted(c)}=EXCLUDED.{quoted(c)}" for c in updates) if updates else "DO NOTHING"
        return sql

    def _copy(self, snapshot: sqlite3.Connection, target: Engine, job_id: str) -> int:
        copied = 0
        # One transaction with deferred FKs handles cycles without disabling
        # integrity or granting replication/superuser privileges to the copier.
        with target.begin() as connection:
            connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
            for position, (name, columns) in enumerate(self._columns.items(), 1):
                self._ensure_not_cancelled()
                table = self._tables[name]
                cursor = snapshot.execute(f"SELECT * FROM {quoted(name)}")
                sql = text(self._insert_sql(table, columns))
                table_rows = 0
                while batch := _bounded_rows(cursor):
                    values = []
                    for row in batch:
                        normalized = _convert(dict(zip(columns, row)), table)
                        values.append({"p" + str(i): normalized[col] for i, col in enumerate(columns)})
                    connection.execute(sql, values)
                    copied += len(batch)
                    table_rows += len(batch)
                    self._phase("COPYING", current_table=name, tables_copied=position - 1, tables_total=len(self._columns), rows_copied=copied, current_table_rows=table_rows)
                    self._ensure_not_cancelled()
            initial = int(snapshot.execute("SELECT COALESCE(MAX(id),0) FROM _lg_capture_commits").fetchone()[0])
            connection.execute(text("UPDATE _lg_database_copy SET applied_commit=:position WHERE job_id=:job"), {"job": job_id, "position": initial})
        self._phase("VERIFYING_DATA", tables_copied=len(self._columns), rows_copied=copied)
        return initial

    def _verify_baseline(self, snapshot: sqlite3.Connection, target: Engine) -> None:
        verified: dict[str, Any] = {}
        with target.connect() as connection, TemporaryDirectory(prefix="row-hashes-", dir=self.root) as temporary:
            for name, columns in self._columns.items():
                self._ensure_not_cancelled()
                table = self._tables[name]
                source_rows = (_convert(dict(zip(columns, row)), table) for row in snapshot.execute(f"SELECT * FROM {quoted(name)}"))
                left = _digest_rows(source_rows, Path(temporary))
                # SQLAlchemy's default growing server cursor buffer reaches
                # 1,000 rows: hundreds of MiB for large JSON records.
                rows = connection.execution_options(yield_per=16).execute(text(f"SELECT {_target_select(table, columns)} FROM {quoted(name)}")).mappings()
                right = _digest_rows((dict(row) for row in rows), Path(temporary))
                if left != right:
                    raise ValueError(f"{name}: canonical row count or content hash mismatch")
                verified[name] = {"rows": left[0], "sha256": left[1]}
                self._phase("VERIFYING_DATA", tables_verified=len(verified), current_table=name)
        atomic_json(self.root / "verification.json", {"tables": verified, "all_match": True})
        self._phase("BASELINE_VERIFIED", baseline_verified=True)

    def _delete_row(self, connection, table: Table, row: dict[str, Any]) -> None:
        primary = [c.name for c in table.primary_key] or list(row)
        clauses = " AND ".join(f"{quoted(c)}{'::text' if isinstance(table.c[c].type, JSON) else ''} IS NOT DISTINCT FROM :p{i}" for i, c in enumerate(primary))
        # CTAS archives have no primary key and can contain duplicate rows.
        deleted = connection.execute(text(f"DELETE FROM {quoted(table.name)} WHERE ctid IN (SELECT ctid FROM {quoted(table.name)} WHERE {clauses} LIMIT 1)"), {"p" + str(i): row[c] for i, c in enumerate(primary)}).rowcount
        if deleted != 1:
            raise ValueError(f"{table.name}: incremental delete verification failed")

    def _verify_target(self, target: Engine) -> None:
        with target.connect() as connection:
            for setting in ("fsync", "full_page_writes", "synchronous_commit"):
                if connection.exec_driver_sql("SHOW " + setting).scalar_one() != "on":
                    raise ValueError(f"Target durability setting {setting} must be on")
            if connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "off":
                raise ValueError("Target must accept writes")
            if connection.execute(text("SELECT count(*) FROM pg_constraint WHERE connamespace=current_schema()::regnamespace AND NOT convalidated")).scalar_one():
                raise ValueError("Target has unvalidated constraints")
            if connection.execute(text("SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relnamespace=current_schema()::regnamespace AND NOT i.indisvalid")).scalar_one():
                raise ValueError("Target has invalid indexes")
            index_counts = dict(connection.execute(text("SELECT tablename,count(*) FROM pg_indexes WHERE schemaname=current_schema() GROUP BY tablename")).all())
            for name, table in self._tables.items():
                actual = inspect(connection).get_columns(name)
                if {col["name"] for col in actual} != set(self._columns[name]):
                    raise ValueError(f"{name}: target columns differ")
                if len(inspect(connection).get_foreign_keys(name)) != len(table.foreign_key_constraints):
                    raise ValueError(f"{name}: target foreign keys differ")
                if any(not table.c[col["name"]].nullable and col["nullable"] for col in actual):
                    raise ValueError(f"{name}: target nullability differs")
                from sqlalchemy import UniqueConstraint
                expected_indexes = len(table.indexes) + bool(list(table.primary_key)) + sum(isinstance(c, UniqueConstraint) for c in table.constraints)
                if index_counts.get(name, 0) < expected_indexes:
                    raise ValueError(f"{name}: target indexes are missing")
            connection.execute(text("CREATE TEMP TABLE lg_migration_probe(id INTEGER PRIMARY KEY, payload TEXT) ON COMMIT DROP"))
            connection.execute(text("INSERT INTO lg_migration_probe VALUES(1,'数据库 100%_literal')"))
            if connection.execute(text("SELECT payload FROM lg_migration_probe WHERE id=1")).scalar_one() != "数据库 100%_literal":
                raise ValueError("Target write/read probe failed")
            connection.rollback()
        self._phase("VERIFYING_TARGET", target_verified=True)

    def _replay(self, source: sqlite3.Connection, target: Engine, job_id: str, position: int, *, until: int | None = None) -> int:
        commits = source.execute("SELECT id FROM _lg_capture_commits WHERE id>? AND id<=? ORDER BY id LIMIT 500", (position, until if until is not None else 2**63 - 1)).fetchall()
        if not commits:
            return position
        high = int(commits[-1][0])
        query = ("SELECT r.table_name,r.operation,r.old_row,r.new_row FROM _lg_capture_rows r JOIN _lg_capture_commits c ON c.txid=r.txid WHERE c.id>? AND c.id<=? ORDER BY c.id,r.id", (position, high))
        with closing(source.execute(*query)) as changes, target.begin() as connection:
            connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
            while batch := _bounded_rows(changes):
                self._replay_changes(connection, batch)
            # The checkpoint advances only with the entire ordered prefix of
            # complete source transactions, never after an individual chunk.
            connection.execute(text("UPDATE _lg_database_copy SET applied_commit=:position WHERE job_id=:job"), {"position": high, "job": job_id})
        return high

    def _replay_changes(self, connection, changes) -> None:
        touched: dict[str, dict[str, tuple[dict, bool]]] = {}
        commands = []
        bags: dict[tuple[str, str], list] = {}
        for name, operation, old_text, new_text in changes:
            self._ensure_not_cancelled()
            if name not in self._tables:
                raise ValueError(f"Unmapped journal table: {name}")
            table, columns = self._tables[name], self._columns[name]
            old = _convert(SQLiteCapture.decode(old_text), table) if old_text else None
            new = _convert(SQLiteCapture.decode(new_text), table) if new_text else None
            primary = [c.name for c in table.primary_key]
            if not primary:
                for row, delta in ((old, -1), (new, 1)):
                    if row is not None:
                        key = (name, _row_hash(row))
                        if key not in bags:
                            clauses = " AND ".join(f"{quoted(c)}{'::text' if isinstance(table.c[c].type, JSON) else ''} IS NOT DISTINCT FROM :p{i}" for i, c in enumerate(columns))
                            sql = text(f"SELECT COUNT(*) FROM {quoted(name)} WHERE {clauses}")
                            params = {"p" + str(i): row[c] for i, c in enumerate(columns)}
                            bags[key] = [sql, params, connection.execute(sql, params).scalar_one(), 0]
                        bags[key][3] += delta
            if old is not None and (operation == "DELETE" or not primary or any(old[c] != new[c] for c in primary)):
                commands.append(("delete", name, old))
                if primary:
                    touched.setdefault(name, {})[_row_hash({c: old[c] for c in primary})] = (old, False)
            if new is not None:
                commands.append((self._insert_sql(table, columns, upsert=bool(primary)), name, new))
                if primary:
                    touched.setdefault(name, {})[_row_hash({c: new[c] for c in primary})] = (new, True)
        # Execute each bounded chunk in source order. The caller commits only
        # after every chunk in the complete source transaction prefix passes.
        # executemany uses psycopg's pipeline for adjacent matching commands.
        for (sql, name), group in groupby(commands, key=lambda command: command[:2]):
            table, columns = self._tables[name], self._columns[name]
            rows = [command[2] for command in group]
            if sql == "delete":
                for row in rows:
                    self._delete_row(connection, table, row)
            else:
                connection.execute(text(sql), [{"p" + str(i): row[col] for i, col in enumerate(columns)} for row in rows])
        for name, expected_by_key in touched.items():
            table = self._tables[name]
            primary = [c.name for c in table.primary_key]
            expected_items = list(expected_by_key.items())
            for start in range(0, len(expected_items), 200):
                items = expected_items[start:start + 200]
                parameters, clauses = {}, []
                for i, (_key, (expected, _exists)) in enumerate(items):
                    clauses.append("(" + " AND ".join(f"{quoted(c)} IS NOT DISTINCT FROM :k{i}_{j}" for j, c in enumerate(primary)) + ")")
                    parameters.update({f"k{i}_{j}": expected[c] for j, c in enumerate(primary)})
                actual_rows = connection.execute(text(f"SELECT {_target_select(table, self._columns[name])} FROM {quoted(name)} WHERE " + " OR ".join(clauses)), parameters).mappings()
                actual = {_row_hash({c: row[c] for c in primary}): dict(row) for row in actual_rows}
                for key, (expected, exists) in items:
                    row = actual.get(key)
                    if exists and (row is None or _row_hash(row) != _row_hash(expected)) or not exists and row is not None:
                        raise ValueError(f"{name}: incremental verification failed")
        for (name, _key), (sql, params, before, delta) in bags.items():
            if connection.execute(sql, params).scalar_one() != before + delta:
                raise ValueError(f"{name}: archive multiplicity verification failed")

    def _run(self) -> None:
        job = read_json(self.state_path)
        source_path = Path(job["source_path"])
        try:
            if job.get("backup_verified"):
                self._verify_saved_backup(job)
            else:
                self._backup(source_path, job)
            self._ensure_not_cancelled()
            target_url = self._managed_target(job) if job["managed"] else decrypt_url(self.settings, job["target_secret"])
            self._phase("PREPARING_TARGET", target_secret=encrypt_url(self.settings, target_url))
            target = self._target = postgres_engine(target_url, settings=self.settings)
            with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
                self._columns = _source_tables(source)
                fingerprint = _schema_fingerprint(source)
            if self.capture is None:
                raise ValueError("Source capture is unavailable")
            self.capture.install(source_path, self._columns, job_id=job["id"])
            with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
                capture_fingerprint = _capture_fingerprint(source)
            self._phase("SNAPSHOTTING", source_schema=fingerprint, capture_schema=capture_fingerprint)
            snapshot_path = self.root / f"{job['id']}.snapshot.sqlite3"
            _online_snapshot(source_path, snapshot_path)
            with closing(sqlite3.connect(snapshot_path.as_uri() + "?mode=ro", uri=True)) as snapshot:
                self._prepare_target(target, snapshot, job["id"])
                position = self._copy(snapshot, target, job["id"])
                self._verify_baseline(snapshot, target)
            from app.core.postgresql_search import install_search
            install_search(target)
            self._verify_target(target)
            self._phase("CATCHING_UP", search_verified=True)
            self._follow_source(source_path, target, job, position)
        except InterruptedError:
            if self.capture and not read_json(self.root / "active.json"):
                self._stop_capture(source_path)
            self._phase("CANCELLED")
        except Exception as exc:
            if read_json(self.root / "active.json"):
                try:
                    if self._recover_route():
                        return
                except Exception:
                    pass
            phase = "RECOVERING" if read_json(self.root / "active.json") else "FAILED"
            message = exc.message if isinstance(exc, AppError) else str(exc) if isinstance(exc, (ValueError, TimeoutError, DatabaseActivationError)) else type(exc).__name__
            self._phase(phase, error=message, all_checks_passed=False)

    def _follow_source(self, source_path: Path, target: Engine, job: dict[str, Any], position: int) -> None:
        """Keep replaying while an administrator reviews READY, avoiding stale gaps."""
        final_verified = False
        while True:
            with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
                while True:
                    self._ensure_not_cancelled()
                    position = self._replay(source, target, job["id"], position)
                    high = int(source.execute("SELECT COALESCE(MAX(id),0) FROM _lg_capture_commits").fetchone()[0])
                    self._phase("READY_TO_ACTIVATE" if final_verified and high - position <= 10 else "CATCHING_UP", source_commit=high, applied_commit=position, pending_transactions=high - position)
                    if position > high:
                        raise ValueError("Source journal was rewound; activation forbidden")
                    if high - position <= 10:
                        break
            if not final_verified:
                # Recheck the entire source prefix after the initial catch-up,
                # rather than inferring complete equality from row counts.
                verification_path = self.root / f"{job['id']}.final.sqlite3"
                _online_snapshot(source_path, verification_path)
                with closing(sqlite3.connect(verification_path.as_uri() + "?mode=ro", uri=True)) as snapshot:
                    high = int(snapshot.execute("SELECT COALESCE(MAX(id),0) FROM _lg_capture_commits").fetchone()[0])
                    with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as live:
                        while position < high:
                            position = self._replay(live, target, job["id"], position, until=high)
                    self._verify_baseline(snapshot, target)
                final_verified = True
                continue
            self._phase("READY_TO_ACTIVATE", all_checks_passed=True)
            activation = read_json(self.root / "activate.json")
            if job["auto_activate"] or self._activate_requested.is_set() or activation and activation.get("job_id") == job["id"]:
                self._activate()
                return
            self._cancel.wait(0.5)
            self._ensure_not_cancelled()

    def commit(self) -> dict[str, Any]:
        with self._lock:
            job = read_json(self.state_path)
            if not job or job.get("state") != "READY_TO_ACTIVATE" or not job.get("all_checks_passed"):
                raise AppError(409, "database_activation_not_ready", "备份、数据和兼容检查必须全部通过才能切换")
            self._activate_requested.set()
            atomic_json(self.root / "activate.json", {"job_id": job["id"]})
            if not self._thread or not self._thread.is_alive():
                try:
                    self._launch(self._resume)
                except AppError as exc:
                    if exc.code != "database_migration_busy":
                        raise
        return self.status()

    def _activate(self) -> None:
        job = read_json(self.state_path)
        if not all(job.get(key) for key in ("backup_verified", "baseline_verified", "search_verified", "target_verified", "all_checks_passed")):
            raise ValueError("Required migration gates have not all passed")
        self._verify_saved_backup(job)
        target = self._target or postgres_engine(decrypt_url(self.settings, job["target_secret"]), settings=self.settings)
        source_path = Path(job["source_path"])
        self._phase("ACTIVATING")
        self._activate_with_target(job, target, source_path)

    def _verify_saved_backup(self, job: dict) -> None:
        backup = self.root / "backups" / job["id"] / "primary.sqlite3"
        if _file_hash(backup) != job["backup_sha256"]:
            raise ValueError("Verified backup changed; activation blocked")
        if _file_hash(backup.with_name("recovery.json")) != job["recovery_sha256"]:
            raise ValueError("Backup recovery material changed; activation blocked")

    def _activate_with_target(self, job: dict, target: Engine, source_path: Path) -> None:
        verification_path = self.root / f"{job['id']}.final.sqlite3"
        with closing(sqlite3.connect(verification_path.as_uri() + "?mode=ro", uri=True)) as snapshot:
            verified_capture_row = int(snapshot.execute("SELECT COALESCE(MAX(id),0) FROM _lg_capture_rows").fetchone()[0])
        # Bounded attempts leave existing requests running. A long-lived reader
        # postpones activation instead of being killed or receiving a 503.
        attempt = 0
        while True:
            self._ensure_not_cancelled()
            # Catch up outside the barrier, including writes made during READY.
            with target.connect() as connection:
                position = int(connection.execute(text("SELECT applied_commit FROM _lg_database_copy WHERE job_id=:job"), {"job": job["id"]}).scalar_one())
            with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
                position = self._replay(source, target, job["id"], position)
            try:
                with self.router.drain(float(self.settings.database_migration_cutover_wait_seconds)):
                    self._ensure_not_cancelled(force=True)
                    deadline = time.monotonic() + float(self.settings.database_migration_cutover_wait_seconds)
                    with closing(sqlite3.connect(source_path, timeout=1)) as source:
                        source.execute("BEGIN IMMEDIATE")
                        if _schema_fingerprint(source) != job["source_schema"]:
                            raise ValueError("Source schema changed during migration")
                        if _capture_fingerprint(source) != job["capture_schema"]:
                            raise ValueError("Source capture triggers changed during migration")
                        if source.execute("SELECT 1 FROM _lg_capture_rows r LEFT JOIN _lg_capture_commits c ON c.txid=r.txid WHERE r.id>? AND c.txid IS NULL LIMIT 1", (verified_capture_row,)).fetchone():
                            raise ValueError("An untracked SQLite writer committed outside the application transaction boundary")
                        verified_capture_row = int(source.execute("SELECT COALESCE(MAX(id),0) FROM _lg_capture_rows").fetchone()[0])
                        if time.monotonic() > deadline:
                            raise TimeoutError("Source journal validation exceeded cutover budget")
                        high = int(source.execute("SELECT COALESCE(MAX(id),0) FROM _lg_capture_commits").fetchone()[0])
                        with target.connect() as connection:
                            position = int(connection.execute(text("SELECT applied_commit FROM _lg_database_copy WHERE job_id=:job"), {"job": job["id"]}).scalar_one())
                        while position < high:
                            position = self._replay(source, target, job["id"], position, until=high)
                            if time.monotonic() > deadline:
                                raise TimeoutError("Final replay exceeded cutover budget; source remains active")
                        if position != high:
                            raise ValueError("Source and target commit checkpoints differ")
                        with target.begin() as connection:
                            for table in self._tables.values():
                                for column in table.primary_key:
                                    if column.autoincrement is True:
                                        connection.execute(text("SELECT setval(pg_get_serial_sequence(:table,:column), GREATEST(COALESCE((SELECT MAX(" + quoted(column.name) + ") FROM " + quoted(table.name) + "),0)+1,1),false)"), {"table": table.name, "column": column.name})
                        record = {"phase": "activation_pending", "job_id": job["id"], "source_path": str(source_path), "final_commit": high,
                            "backup_sha256": job["backup_sha256"], "recovery_sha256": job["recovery_sha256"], "verified": True, **job["target_secret"]}
                        atomic_json(self.root / "active.json", record)
                        self.router.activation_uncertain()
                        # Commit every remote prerequisite before the local
                        # point of no return. The prepared marker alone never
                        # authorizes target traffic without the SQLite fence.
                        with target.begin() as connection:
                            marker = connection.execute(text("INSERT INTO _lg_database_activation(singleton,job_id) VALUES(1,:job) ON CONFLICT(singleton) DO UPDATE SET job_id=EXCLUDED.job_id WHERE _lg_database_activation.job_id=EXCLUDED.job_id RETURNING job_id"), {"job": job["id"]}).scalar()
                            if marker != job["id"]:
                                raise ValueError("Target activation identity mismatch")
                        source.execute("UPDATE _lg_database_fence SET sealed=1,job_id=? WHERE singleton=1", (job["id"],))
                        source.commit()
                    try:
                        install_postgres_writer_order(target)
                        self.router.activate(target, job["id"])
                        atomic_json(self.root / "active.json", {**record, "phase": "postgresql_active"})
                        self._phase("COMPLETED", active_database="postgresql", irreversible=True, final_commit=high, pending_transactions=0, waiting_for_transactions=False)
                        return
                    except BaseException:
                        # A durable pending intent plus the committed PG marker
                        # is sufficient for restart recovery. A final manifest
                        # rewrite failure must not interrupt an active PG route.
                        if self.router.physical.dialect.name == "postgresql":
                            self._phase("COMPLETED", activation_manifest_pending=True)
                            return
                        self.router.activation_uncertain()
                        raise
            except TimeoutError:
                self._phase("ACTIVATING", waiting_for_transactions=True, activation_attempt=attempt + 1)
                attempt += 1
                self._cancel.wait(0.25)

    def _recover_route(self) -> bool:
        record = read_json(self.root / "active.json")
        url = recover_activation(self.settings, record)
        if not read_json(self.root / "active.json"):
            self.router._uncertain = False
            return False
        target = self._target or postgres_engine(url, settings=self.settings)
        install_postgres_writer_order(target)
        self.router.activate(target, record["job_id"])
        self._phase("COMPLETED", all_checks_passed=True, pending_transactions=0)
        return True

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            if read_json(self.root / "active.json") or self.router.physical.dialect.name == "postgresql":
                raise AppError(409, "database_migration_irreversible", "PostgreSQL 激活不可撤销，不支持迁回 SQLite")
            self._cancel.set()
            job = read_json(self.state_path)
            if job:
                atomic_json(self.root / "cancel.json", {"job_id": job["id"]})
            if not self._thread or not self._thread.is_alive():
                try:
                    lease = self._claim()
                except AppError:
                    return self.status()
                try:
                    if job and self.capture:
                        self._stop_capture(Path(job["source_path"]))
                    self._phase("CANCELLED")
                finally:
                    lease.close()
        return self.status()

    def _stop_capture(self, path: Path) -> None:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
            columns = _source_tables(source)
            installed = source.execute("SELECT 1 FROM sqlite_master WHERE name='_lg_database_fence'").fetchone()
        if installed and self.capture:
            self.capture.cancel(path, columns)

    def resume(self) -> dict[str, Any]:
        with self._lock:
            job = read_json(self.state_path)
            if self._thread and self._thread.is_alive():
                return self.status()
            if not job or job.get("state") == "CANCELLED" or read_json(self.root / "active.json"):
                raise AppError(409, "database_resume_unavailable", "此任务不支持恢复增量同步")
            ownership = self._claim()
            try:
                self._cancel.clear()
                self._phase("RESUMING", error=None, all_checks_passed=False)
                self._launch(self._resume, lease=ownership)
            except BaseException:
                ownership.close()
                raise
        return self.status()

    def _resume(self) -> None:
        job = read_json(self.state_path)
        try:
            snapshot_path = self.root / f"{job['id']}.snapshot.sqlite3"
            if not snapshot_path.exists() or not job.get("target_secret"):
                self._run()
                return
            self._verify_saved_backup(job)
            target = self._target = postgres_engine(decrypt_url(self.settings, job["target_secret"]), settings=self.settings)
            with closing(sqlite3.connect(snapshot_path.as_uri() + "?mode=ro", uri=True)) as snapshot:
                self._columns = _source_tables(snapshot)
                metadata = MetaData()
                for name in self._columns:
                    if name in self.metadata.tables:
                        self.metadata.tables[name].to_metadata(metadata)
                    else:
                        from sqlalchemy import Column
                        parent = self.metadata.tables[_ARCHIVES[name]]
                        Table(name, metadata, *(Column(c, parent.c[c].type.copy(), nullable=True) for c in self._columns[name]))
                self._tables = dict(metadata.tables)
            with target.connect() as connection:
                position = connection.execute(text("SELECT applied_commit FROM _lg_database_copy WHERE job_id=:job"), {"job": job["id"]}).scalar_one()
            if not job.get("baseline_verified"):
                with closing(sqlite3.connect(snapshot_path.as_uri() + "?mode=ro", uri=True)) as snapshot:
                    if position == -1:
                        position = self._copy(snapshot, target, job["id"])
                    self._verify_baseline(snapshot, target)
                from app.core.postgresql_search import install_search
                install_search(target)
                self._phase("CATCHING_UP", search_verified=True)
            self._verify_target(target)
            self._follow_source(Path(job["source_path"]), target, job, int(position))
        except InterruptedError:
            self._stop_capture(Path(job["source_path"]))
            self._phase("CANCELLED")
        except Exception as exc:
            if read_json(self.root / "active.json"):
                try:
                    if self._recover_route():
                        return
                except Exception:
                    pass
            self._phase("RECOVERING" if read_json(self.root / "active.json") else "FAILED", all_checks_passed=False, error=type(exc).__name__)

    def recover_worker(self) -> None:
        """Resume only work already explicitly authorized by an administrator."""
        try:
            job = read_json(self.state_path)
        except (OSError, ValueError, DatabaseActivationError):
            # The routing manifest/fence were already checked before engine
            # creation. An unreadable progress journal must not stop an
            # otherwise authoritative SQLite service from starting.
            import logging
            logging.getLogger(__name__).error("Primary database migration progress is unreadable; automatic replay was not started")
            return
        if job and job.get("state") not in _TERMINAL and not read_json(self.root / "active.json"):
            try:
                self.resume()
            except AppError as exc:
                if exc.code != "database_migration_busy":
                    raise


def controller(settings: Any) -> DatabaseMigrationController:
    from app.core import database
    key = str(state_root(settings))
    with _controllers_lock:
        if key not in _controllers:
            _controllers[key] = DatabaseMigrationController(settings, database.engine, database.sqlite_capture, database.Base.metadata)
        return _controllers[key]
