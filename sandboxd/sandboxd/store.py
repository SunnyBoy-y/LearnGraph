"""sandboxd durable state store (single-node SQLite, WAL).

Owns: sandbox records, idempotency ledger, execution audit. Bytes live in
Docker named volumes; Docker labels carry reconciliation metadata. The store
is intentionally independent of the LearnGraph application database.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class SandboxRecord:
    sandbox_id: str
    deployment_id: str
    owner_scope: str
    owner_workspace_id: str
    owner_session_id: str
    session_id: str
    workspace_key: str
    runtime_kind: str
    state: str
    volume_name: str
    container_id: str | None
    image_digest: str
    runner_abi: str
    policy_digest: str | None
    egress_network: str | None
    limits_json: str
    ttl_seconds: int
    expires_at: str
    created_at: str
    updated_at: str
    last_used_at: str

    @property
    def limits(self) -> dict[str, Any]:
        try:
            value = json.loads(self.limits_json)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    key_scope: str
    key: str
    operation: str
    payload_hash: str
    state: str
    result_json: str | None
    error_code: str | None


class SandboxdStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sandboxes (
                    sandbox_id TEXT PRIMARY KEY,
                    deployment_id TEXT NOT NULL,
                    owner_scope TEXT NOT NULL,
                    owner_workspace_id TEXT NOT NULL,
                    owner_session_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    workspace_key TEXT NOT NULL,
                    runtime_kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    volume_name TEXT NOT NULL,
                    container_id TEXT,
                    image_digest TEXT NOT NULL,
                    runner_abi TEXT NOT NULL,
                    policy_digest TEXT,
                    egress_network TEXT,
                    limits_json TEXT NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_sandboxes_owner
                    ON sandboxes (deployment_id, owner_scope, state);
                CREATE INDEX IF NOT EXISTS ix_sandboxes_expiry
                    ON sandboxes (deployment_id, expires_at);

                CREATE TABLE IF NOT EXISTS idempotency (
                    key_scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (key_scope, key)
                );

                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    sandbox_id TEXT NOT NULL,
                    deployment_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    timed_out INTEGER NOT NULL DEFAULT 0,
                    truncated INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER,
                    argv_digest TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_executions_sandbox
                    ON executions (sandbox_id, started_at);

                CREATE TABLE IF NOT EXISTS runtime_records (
                    runtime_kind TEXT PRIMARY KEY,
                    image_digest TEXT NOT NULL,
                    runner_abi TEXT NOT NULL,
                    source TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    smoke_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def ping(self) -> bool:
        try:
            with self._lock, self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:  # noqa: BLE001
            return False

    # --- sandboxes ---------------------------------------------------------

    def insert_sandbox(self, record: SandboxRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sandboxes (
                    sandbox_id, deployment_id, owner_scope, owner_workspace_id,
                    owner_session_id, session_id, workspace_key, runtime_kind,
                    state, volume_name, container_id, image_digest, runner_abi,
                    policy_digest, egress_network, limits_json, ttl_seconds,
                    expires_at, created_at, updated_at, last_used_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.sandbox_id, record.deployment_id, record.owner_scope,
                    record.owner_workspace_id, record.owner_session_id,
                    record.session_id, record.workspace_key, record.runtime_kind,
                    record.state, record.volume_name, record.container_id,
                    record.image_digest, record.runner_abi, record.policy_digest,
                    record.egress_network, record.limits_json, record.ttl_seconds,
                    record.expires_at, record.created_at, record.updated_at,
                    record.last_used_at,
                ),
            )

    def get_sandbox(self, sandbox_id: str) -> SandboxRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
            ).fetchone()
        return self._row_to_sandbox(row)

    def get_sandbox_by_session(self, deployment_id: str, session_id: str) -> SandboxRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sandboxes WHERE deployment_id = ? AND session_id = ? ORDER BY created_at DESC LIMIT 1",
                (deployment_id, session_id),
            ).fetchone()
        return self._row_to_sandbox(row)

    def list_sandboxes(self, deployment_id: str) -> list[SandboxRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sandboxes WHERE deployment_id = ? ORDER BY created_at",
                (deployment_id,),
            ).fetchall()
        return [self._row_to_sandbox(row) for row in rows if row is not None]

    def update_sandbox(self, sandbox_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "state", "container_id", "policy_digest", "egress_network",
            "expires_at", "updated_at", "last_used_at", "runner_abi",
            "image_digest",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported sandbox update fields: {sorted(unknown)}")
        fields["updated_at"] = _utc_now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [*fields.values(), sandbox_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE sandboxes SET {assignments} WHERE sandbox_id = ?", values)

    def delete_sandbox(self, sandbox_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,))

    def count_active(self, deployment_id: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM sandboxes WHERE deployment_id = ? AND state NOT IN ('DELETING', 'ERROR')",
                (deployment_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _row_to_sandbox(row: sqlite3.Row | None) -> SandboxRecord | None:
        if row is None:
            return None
        return SandboxRecord(
            sandbox_id=row["sandbox_id"],
            deployment_id=row["deployment_id"],
            owner_scope=row["owner_scope"],
            owner_workspace_id=row["owner_workspace_id"],
            owner_session_id=row["owner_session_id"],
            session_id=row["session_id"],
            workspace_key=row["workspace_key"],
            runtime_kind=row["runtime_kind"],
            state=row["state"],
            volume_name=row["volume_name"],
            container_id=row["container_id"],
            image_digest=row["image_digest"],
            runner_abi=row["runner_abi"],
            policy_digest=row["policy_digest"],
            egress_network=row["egress_network"],
            limits_json=row["limits_json"],
            ttl_seconds=row["ttl_seconds"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_used_at=row["last_used_at"],
        )

    # --- idempotency -------------------------------------------------------

    def begin_idempotent(
        self, key_scope: str, key: str, operation: str, payload_hash: str
    ) -> tuple[bool, IdempotencyRecord | None]:
        """Atomically claim an idempotency slot.

        Returns ``(True, None)`` when the caller may proceed, or
        ``(False, existing)`` when the key was already used.
        """
        now = _utc_now()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM idempotency WHERE key_scope = ? AND key = ?",
                (key_scope, key),
            ).fetchone()
            if existing is not None:
                return False, IdempotencyRecord(
                    key_scope=existing["key_scope"],
                    key=existing["key"],
                    operation=existing["operation"],
                    payload_hash=existing["payload_hash"],
                    state=existing["state"],
                    result_json=existing["result_json"],
                    error_code=existing["error_code"],
                )
            conn.execute(
                "INSERT INTO idempotency (key_scope, key, operation, payload_hash, state, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (key_scope, key, operation, payload_hash, "in_progress", now, now),
            )
            return True, None

    def complete_idempotent(
        self,
        key_scope: str,
        key: str,
        *,
        state: str,
        result_json: str | None = None,
        error_code: str | None = None,
    ) -> None:
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE idempotency SET state = ?, result_json = ?, error_code = ?, updated_at = ? "
                "WHERE key_scope = ? AND key = ?",
                (state, result_json, error_code, now, key_scope, key),
            )

    # --- executions --------------------------------------------------------

    def create_execution(
        self,
        *,
        execution_id: str,
        sandbox_id: str,
        deployment_id: str,
        operation: str,
        argv_digest: str,
    ) -> None:
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO executions (execution_id, sandbox_id, deployment_id, operation, status, argv_digest, started_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (execution_id, sandbox_id, deployment_id, operation, "running", argv_digest, now),
            )

    def finish_execution(
        self,
        *,
        execution_id: str,
        status: str,
        exit_code: int | None,
        timed_out: bool,
        truncated: bool,
        latency_ms: int | None,
    ) -> None:
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE executions SET status = ?, exit_code = ?, timed_out = ?, truncated = ?, latency_ms = ?, finished_at = ? "
                "WHERE execution_id = ?",
                (status, exit_code, 1 if timed_out else 0, 1 if truncated else 0, latency_ms, now, execution_id),
            )

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    # --- runtimes (bootstrap records) --------------------------------------

    def upsert_runtime(
        self,
        *,
        runtime_kind: str,
        image_digest: str,
        runner_abi: str,
        source: str,
        labels: dict[str, Any],
        smoke_status: str,
    ) -> None:
        now = _utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runtime_records ("
                "runtime_kind, image_digest, runner_abi, source, labels_json, "
                "smoke_status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    runtime_kind,
                    image_digest,
                    runner_abi,
                    source,
                    json.dumps(labels, sort_keys=True),
                    smoke_status,
                    now,
                    now,
                ),
            )

    def get_runtime(self, runtime_kind: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_records WHERE runtime_kind = ?", (runtime_kind,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        try:
            record["labels"] = json.loads(record.get("labels_json") or "{}")
        except json.JSONDecodeError:
            record["labels"] = {}
        return record

    def list_runtimes(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime_records ORDER BY runtime_kind"
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            try:
                record["labels"] = json.loads(record.get("labels_json") or "{}")
            except json.JSONDecodeError:
                record["labels"] = {}
            result.append(record)
        return result
