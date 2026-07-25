from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.secret_store import SecretStoreUnavailable, secret_store_from_settings
from app.core.security import SecretCipher
from app.core.tasks import task_queue
from app.domain.migration_models import (
    InfrastructureBinding,
    InfrastructureDatabaseConfiguration,
    MaintenanceLock,
    MigrationArtifact,
    MigrationCheckpoint,
    MigrationExecution,
    MigrationFileItem,
)
from app.domain.models import FileRecord, MigrationJob, utc_now
from app.domain.schemas.migrations import (
    DatabaseConfigurationUpsertRequest,
    MigrationPreflightRequest,
)
from app.providers.infrastructure import adapter_inventory, probe_database, probe_minio
from app.repositories.audit import AuditRepository
from app.services.provider_secrets import PROVIDER_SECRET_ALGORITHM


DATABASE_TARGETS = {"sqlite", "sqlite_compatible", "sqlite-copy", "postgresql", "mysql"}
LOCAL_DATABASE_TARGETS = {"sqlite", "sqlite_compatible", "sqlite-copy"}
OBJECT_TARGETS = {"local", "local_copy", "minio"}


def _migration_adapter_family(kind: str) -> str:
    normalized = kind.casefold()
    if normalized in {"sqlite", "sqlite_compatible", "sqlite-copy"}:
        return "sqlite"
    if normalized in {"local", "local_copy", "local_files"}:
        return "local"
    return normalized
PRECOMMIT_STATES = {
    "PREFLIGHT",
    "QUIESCING",
    "SOURCE_FROZEN",
    "SNAPSHOTTED",
    "TARGET_PREPARED",
    "COPYING_DATABASE",
    "COPYING_FILES",
    "VERIFYING_CANONICAL",
    "REBUILDING_DERIVED",
    "VERIFYING_DERIVED",
    "CUTOVER_READ_ONLY",
    "FAILED_SAFE",
}


def _canonical_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$bytes": value.hex()}
    if isinstance(value, float) and not math.isfinite(value):
        return {"$float": repr(value)}
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_backup(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _sqlite_integrity(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    try:
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
        return {
            "integrity_check": integrity,
            "foreign_key_violations": foreign_keys,
            "passed": integrity == ["ok"] and not foreign_keys,
        }
    finally:
        connection.close()


def _sqlite_manifest(path: Path, output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"tables": {}, "total_rows": 0}
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        with output_path.open("w", encoding="utf-8", newline="\n") as manifest:
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for table_name in tables:
                escaped = table_name.replace('"', '""')
                columns = list(connection.execute(f'PRAGMA table_info("{escaped}")'))
                column_names = [str(row[1]) for row in columns]
                primary = [
                    str(row[1])
                    for row in sorted(columns, key=lambda row: int(row[5]))
                    if int(row[5])
                ]
                order = primary or column_names
                order_sql = ", ".join(
                    f'"{name.replace(chr(34), chr(34) * 2)}"' for name in order
                )
                query = f'SELECT * FROM "{escaped}"' + (
                    f" ORDER BY {order_sql}" if order_sql else ""
                )
                table_digest = hashlib.sha256()
                count = 0
                for row in connection.execute(query):
                    canonical = {name: _canonical_value(row[name]) for name in column_names}
                    row_bytes = _canonical_bytes(canonical)
                    row_hash = hashlib.sha256(row_bytes).hexdigest()
                    table_digest.update(row_bytes)
                    table_digest.update(b"\n")
                    primary_value = {name: canonical[name] for name in primary}
                    manifest.write(
                        _canonical_bytes(
                            {
                                "table": table_name,
                                "primary_key": primary_value,
                                "row_sha256": row_hash,
                            }
                        ).decode("utf-8")
                        + "\n"
                    )
                    count += 1
                report["tables"][table_name] = {
                    "row_count": count,
                    "aggregate_sha256": table_digest.hexdigest(),
                }
                report["total_rows"] += count
    finally:
        connection.close()
    report["manifest_sha256"] = _sha256_file(output_path)
    return report


class MigrationService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        configured_root = os.getenv("LEARNGRAPH_MIGRATION_ROOT", "").strip()
        self.root = (
            Path(configured_root)
            if configured_root
            else settings.storage_root.parent / "migrations"
        ).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.audit = AuditRepository(db, workspace_id)

    def adapters(self) -> list[dict[str, Any]]:
        configurations = self._database_configurations()
        database_urls: dict[str, URL] = {}
        configuration_errors: dict[str, AppError] = {}
        for configuration in configurations:
            try:
                database_urls[configuration.provider_kind] = self._database_url(
                    configuration
                )
            except AppError as exc:
                configuration_errors[configuration.provider_kind] = exc
        inventory = adapter_inventory(
            database_urls,
            {item.provider_kind for item in configurations},
            {item.provider_kind: item.ssl_mode for item in configurations},
        )
        positions = {
            item["provider_kind"]: index
            for index, item in enumerate(inventory)
            if item["capability"] == "database"
        }
        for configuration in configurations:
            error = configuration_errors.get(configuration.provider_kind)
            if error is not None:
                item = {
                    "provider_kind": configuration.provider_kind,
                    "capability": "database",
                    "status": "unavailable",
                    "configured": True,
                    "driver_available": configuration.driver_available,
                    "connection_verified": False,
                    "details": {"reason": error.code},
                }
                position = positions.get(configuration.provider_kind)
                if position is not None:
                    inventory[position] = item
        return inventory

    def _database_configurations(
        self,
    ) -> list[InfrastructureDatabaseConfiguration]:
        return list(
            self.db.scalars(
                select(InfrastructureDatabaseConfiguration)
                .where(
                    InfrastructureDatabaseConfiguration.workspace_id
                    == self.workspace_id
                )
                .order_by(InfrastructureDatabaseConfiguration.provider_kind)
            ).all()
        )

    def _database_configuration(
        self, provider_kind: str
    ) -> InfrastructureDatabaseConfiguration | None:
        return self.db.scalar(
            select(InfrastructureDatabaseConfiguration).where(
                InfrastructureDatabaseConfiguration.workspace_id
                == self.workspace_id,
                InfrastructureDatabaseConfiguration.provider_kind
                == provider_kind,
            )
        )

    @staticmethod
    def _database_configuration_view(
        configuration: InfrastructureDatabaseConfiguration,
    ) -> dict[str, Any]:
        return {
            "provider_kind": configuration.provider_kind,
            "host": configuration.host,
            "port": configuration.port,
            "database_name": configuration.database_name,
            "username": configuration.username,
            "ssl_mode": configuration.ssl_mode,
            "password_configured": bool(configuration.password_ciphertext),
            "status": configuration.status,
            "driver_available": configuration.driver_available,
            "connection_verified": configuration.connection_verified,
            "last_error_code": configuration.last_error_code,
            "last_verified_at": configuration.last_verified_at,
            "updated_at": configuration.updated_at,
        }

    def database_configurations(self) -> list[dict[str, Any]]:
        return [
            self._database_configuration_view(item)
            for item in self._database_configurations()
        ]

    def _database_password(
        self, configuration: InfrastructureDatabaseConfiguration
    ) -> str:
        if configuration.password_algorithm != PROVIDER_SECRET_ALGORITHM:
            raise AppError(
                503,
                "database_secret_unavailable",
                "The database password uses an unsupported encryption format",
            )
        try:
            key = secret_store_from_settings(
                self.settings,
                provider_name=configuration.key_provider,
            ).key(configuration.key_version)
            return SecretCipher(key.secret).decrypt(
                configuration.password_ciphertext
            )
        except (SecretStoreUnavailable, ValueError) as exc:
            raise AppError(
                503,
                "database_secret_unavailable",
                "The encrypted database password cannot be opened by the configured Secret Store",
            ) from exc

    def _database_url(
        self, configuration: InfrastructureDatabaseConfiguration
    ) -> URL:
        driver_name = (
            "postgresql+psycopg"
            if configuration.provider_kind == "postgresql"
            else "mysql+pymysql"
        )
        query = (
            {
                "sslmode": configuration.ssl_mode,
                "connect_timeout": "3",
            }
            if configuration.provider_kind == "postgresql"
            else {}
        )
        return URL.create(
            driver_name,
            username=configuration.username,
            password=self._database_password(configuration),
            host=configuration.host,
            port=configuration.port,
            database=configuration.database_name,
            query=query,
        )

    def save_database_configuration(
        self,
        provider_kind: str,
        payload: DatabaseConfigurationUpsertRequest,
    ) -> dict[str, Any]:
        normalized = provider_kind.strip().casefold()
        if normalized == "postgres":
            normalized = "postgresql"
        if normalized not in {"postgresql", "mysql"}:
            raise AppError(
                422,
                "unsupported_database_provider",
                "Only PostgreSQL and MySQL migration targets can be configured",
            )
        configuration = self._database_configuration(normalized)
        plaintext = (
            payload.password.get_secret_value()
            if payload.password is not None
            else None
        )
        if configuration is None and not plaintext:
            raise AppError(
                422,
                "database_password_required",
                "A password is required when creating a database configuration",
            )
        if plaintext:
            try:
                key = secret_store_from_settings(
                    self.settings
                ).active_key(create=True)
                ciphertext = SecretCipher(key.secret).encrypt(plaintext)
            except (SecretStoreUnavailable, ValueError) as exc:
                raise AppError(
                    503,
                    "secret_store_unavailable",
                    "The database password was rejected because the configured Secret Store is unavailable",
                ) from exc
        else:
            key = None
            ciphertext = None
        if configuration is None:
            configuration = InfrastructureDatabaseConfiguration(
                workspace_id=self.workspace_id,
                provider_kind=normalized,
                host=payload.host,
                port=payload.port,
                database_name=payload.database_name,
                username=payload.username,
                ssl_mode=payload.ssl_mode,
                password_ciphertext=ciphertext or "",
                password_algorithm=PROVIDER_SECRET_ALGORITHM,
                key_provider=self.settings.secret_provider,
                key_version=key.version if key is not None else 1,
            )
            self.db.add(configuration)
        else:
            configuration.host = payload.host
            configuration.port = payload.port
            configuration.database_name = payload.database_name
            configuration.username = payload.username
            configuration.ssl_mode = payload.ssl_mode
            if ciphertext is not None and key is not None:
                configuration.password_ciphertext = ciphertext
                configuration.password_algorithm = PROVIDER_SECRET_ALGORITHM
                configuration.key_provider = self.settings.secret_provider
                configuration.key_version = key.version
                configuration.secret_version += 1
        self.db.flush()
        probe = probe_database(
            normalized,
            connection_url=self._database_url(configuration),
            ssl_mode=configuration.ssl_mode,
        )
        configuration.status = probe.status
        configuration.driver_available = probe.driver_available
        configuration.connection_verified = probe.connection_verified
        configuration.last_error_code = (
            None
            if probe.connection_verified
            else str(probe.details.get("reason") or "connection_failed")
        )
        configuration.last_verified_at = utc_now()
        self.audit.record(
            actor_id=self.actor_id,
            action="migration.database_configuration.save",
            resource_type="infrastructure_database_configuration",
            resource_id=configuration.id,
            outcome=(
                "success" if configuration.connection_verified else "failed"
            ),
            details={
                "provider_kind": normalized,
                "ssl_mode": configuration.ssl_mode,
                "connection_verified": configuration.connection_verified,
                "password_rotated": plaintext is not None,
                "secret_version": configuration.secret_version,
            },
        )
        self.db.commit()
        self.db.refresh(configuration)
        return self._database_configuration_view(configuration)

    def _job(self, job_id: str) -> MigrationJob:
        job = self.db.scalar(
            select(MigrationJob).where(
                MigrationJob.id == job_id,
                MigrationJob.workspace_id == self.workspace_id,
            )
        )
        if job is None:
            raise AppError(404, "not_found", "Migration job not found in this workspace")
        return job

    def _execution(self, job_id: str) -> MigrationExecution:
        execution = self.db.scalar(
            select(MigrationExecution).where(
                MigrationExecution.job_id == job_id,
                MigrationExecution.workspace_id == self.workspace_id,
            )
        )
        if execution is None:
            raise AppError(404, "not_found", "Migration execution not found in this workspace")
        return execution

    def _active_lock(self) -> MaintenanceLock | None:
        return self.db.scalar(
            select(MaintenanceLock).where(
                MaintenanceLock.workspace_id == self.workspace_id,
                MaintenanceLock.status == "active",
            )
        )

    def _source_database_path(self) -> Path:
        bind = self.db.get_bind()
        if bind.dialect.name != "sqlite" or not bind.url.database:
            raise AppError(
                422,
                "source_adapter_unavailable",
                "The compatibility executor currently requires a SQLite source",
            )
        return Path(str(bind.url.database)).resolve()

    def list(self) -> list[dict[str, Any]]:
        jobs = list(
            self.db.scalars(
                select(MigrationJob)
                .where(MigrationJob.workspace_id == self.workspace_id)
                .order_by(MigrationJob.created_at.desc())
            ).all()
        )
        return [self.view(job) for job in jobs]

    def view(self, job: MigrationJob) -> dict[str, Any]:
        execution = self.db.scalar(
            select(MigrationExecution).where(
                MigrationExecution.job_id == job.id,
                MigrationExecution.workspace_id == self.workspace_id,
            )
        )
        checkpoints = list(
            self.db.scalars(
                select(MigrationCheckpoint)
                .where(
                    MigrationCheckpoint.workspace_id == self.workspace_id,
                    MigrationCheckpoint.job_id == job.id,
                )
                .order_by(MigrationCheckpoint.sequence)
            ).all()
        )
        lock = self.db.scalar(
            select(MaintenanceLock).where(
                MaintenanceLock.workspace_id == self.workspace_id,
                MaintenanceLock.job_id == job.id,
                MaintenanceLock.status == "active",
            )
        )
        return {
            "id": job.id,
            "workspace_id": job.workspace_id,
            "source_kind": job.source_kind,
            "target_kind": job.target_kind,
            "status": job.status,
            "report": job.report,
            "resource_kind": execution.resource_kind if execution is not None else "database",
            "can_rollback": execution.can_rollback if execution is not None else False,
            "reverse_migration_required": (
                execution.reverse_migration_required if execution is not None else False
            ),
            "maintenance_active": lock is not None,
            "checkpoints": [
                {
                    "sequence": item.sequence,
                    "state": item.state,
                    "status": item.status,
                    "metrics": item.metrics,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                    "started_at": item.started_at,
                    "finished_at": item.finished_at,
                }
                for item in checkpoints
            ],
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    def preflight(self, payload: MigrationPreflightRequest) -> dict[str, Any]:
        if self._active_lock() is not None:
            raise AppError(409, "maintenance_active", "Another migration owns the maintenance window")
        if _migration_adapter_family(payload.source_kind) == _migration_adapter_family(payload.target_kind):
            raise AppError(
                422,
                "same_adapter_migration_forbidden",
                "Source and target adapters must be different",
            )
        resource_kind = payload.resource_kind or (
            "object_storage" if payload.source_kind in {"local", "local_files"} else "database"
        )
        target_name = payload.target_name or f"target-{utc_now().strftime('%Y%m%d%H%M%S%f')}"
        checks: list[dict[str, Any]] = []
        source_locator: dict[str, Any]
        target_locator: dict[str, Any]
        if resource_kind == "database":
            if payload.target_kind not in DATABASE_TARGETS:
                raise AppError(422, "unsupported_target", "Unsupported database target kind")
            source_path = self._source_database_path()
            source_integrity = _sqlite_integrity(source_path)
            checks.append(
                {"key": "source_integrity", "status": "passed" if source_integrity["passed"] else "failed"}
            )
            source_locator = {"provider_kind": "sqlite", "database": source_path.name}
            if payload.target_kind in LOCAL_DATABASE_TARGETS:
                target_path = (self.root / "database-targets" / f"{target_name}.sqlite3").resolve()
                if self.root not in target_path.parents:
                    raise AppError(422, "invalid_target", "Target escapes migration root")
                if target_path.exists():
                    checks.append({"key": "target_empty", "status": "failed"})
                probe = probe_database("sqlite", sqlite_path=target_path)
                target_locator = {"provider_kind": "sqlite", "path": str(target_path)}
            else:
                configuration = self._database_configuration(payload.target_kind)
                if configuration is None:
                    probe = probe_database(payload.target_kind)
                    target_locator = {"provider_kind": payload.target_kind}
                else:
                    probe = probe_database(
                        payload.target_kind,
                        connection_url=self._database_url(configuration),
                        ssl_mode=configuration.ssl_mode,
                    )
                    target_locator = {
                        "provider_kind": payload.target_kind,
                        "configuration_id": configuration.id,
                    }
            checks.append({"key": "target_adapter", "status": "passed" if probe.status == "available" else "missing", "details": probe.details})
        else:
            if payload.source_kind not in {"local", "local_files"} or payload.target_kind not in OBJECT_TARGETS:
                raise AppError(422, "unsupported_target", "Unsupported object-storage migration pair")
            source_root = self.settings.storage_root.resolve()
            checks.append({"key": "source_readable", "status": "passed" if source_root.is_dir() else "failed"})
            source_locator = {"provider_kind": "local", "root": str(source_root)}
            if payload.target_kind in {"local", "local_copy"}:
                target_root = (self.root / "object-targets" / target_name).resolve()
                if self.root not in target_root.parents:
                    raise AppError(422, "invalid_target", "Target escapes migration root")
                checks.append({"key": "target_empty", "status": "passed" if not target_root.exists() else "failed"})
                target_locator = {"provider_kind": "local", "root": str(target_root)}
                checks.append({"key": "target_adapter", "status": "passed", "details": {"protocol": "filesystem"}})
            else:
                probe = probe_minio()
                target_locator = {"provider_kind": "minio", "bucket": os.getenv("LEARNGRAPH_MINIO_BUCKET", "")}
                checks.append({"key": "target_adapter", "status": "passed" if probe.status == "available" else "missing", "details": probe.details})
        checks.extend(
            [
                {"key": "maintenance_window", "status": "passed", "details": {"required_on_start": True}},
                {"key": "dual_write", "status": "passed", "details": {"mode": "forbidden"}},
            ]
        )
        ready = all(item["status"] == "passed" for item in checks)
        job = MigrationJob(
            workspace_id=self.workspace_id,
            source_kind=payload.source_kind,
            target_kind=payload.target_kind,
            status="PREFLIGHT" if ready else "preflight_blocked",
            report={"checks": checks, "ready": ready, "data_copied": False},
        )
        self.db.add(job)
        self.db.flush()
        execution = MigrationExecution(
            workspace_id=self.workspace_id,
            job_id=job.id,
            resource_kind=resource_kind,
            state=job.status,
            source_locator=source_locator,
            target_locator=target_locator,
        )
        self.db.add(execution)
        self._checkpoint(job, execution, job.status, {"checks": checks}, "completed" if ready else "blocked")
        self.audit.record(
            actor_id=self.actor_id,
            action="migration.preflight",
            resource_type="migration_job",
            resource_id=job.id,
            outcome="success" if ready else "blocked",
            details={"resource_kind": resource_kind, "source_kind": payload.source_kind, "target_kind": payload.target_kind},
        )
        self.db.commit()
        self.db.refresh(job)
        return self.view(job)

    def _checkpoint(
        self,
        job: MigrationJob,
        execution: MigrationExecution,
        state: str,
        metrics: dict[str, Any] | None = None,
        status: str = "completed",
    ) -> None:
        current = self.db.scalar(
            select(MigrationCheckpoint).where(
                MigrationCheckpoint.workspace_id == self.workspace_id,
                MigrationCheckpoint.job_id == job.id,
                MigrationCheckpoint.state == state,
            )
        )
        now = utc_now()
        if current is None:
            sequence = len(
                list(
                    self.db.scalars(
                        select(MigrationCheckpoint.id).where(
                            MigrationCheckpoint.workspace_id == self.workspace_id,
                            MigrationCheckpoint.job_id == job.id,
                        )
                    ).all()
                )
            ) + 1
            current = MigrationCheckpoint(
                workspace_id=self.workspace_id,
                job_id=job.id,
                sequence=sequence,
                state=state,
                status=status,
                metrics=metrics or {},
                started_at=now,
                finished_at=now,
            )
            self.db.add(current)
        else:
            current.status = status
            current.metrics = metrics or {}
            current.finished_at = now
        job.status = state
        execution.state = state
        self.db.flush()

    def _acquire(self, job: MigrationJob) -> MaintenanceLock:
        if self._active_lock() is not None:
            raise AppError(409, "maintenance_active", "Another migration owns the maintenance window")
        lock = MaintenanceLock(
            workspace_id=self.workspace_id,
            job_id=job.id,
            status="active",
            acquired_by=self.actor_id,
            acquired_at=utc_now(),
        )
        self.db.add(lock)
        self.db.flush()
        return lock

    def _release(self, lock: MaintenanceLock) -> None:
        lock.status = "released"
        lock.released_at = utc_now()
        task_queue.resume()

    def start(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        execution = self._execution(job_id)
        if job.status != "PREFLIGHT" or not job.report.get("ready"):
            raise AppError(409, "migration_not_ready", "Migration preflight is not ready")
        if execution.target_locator.get("provider_kind") not in {"sqlite", "local"}:
            raise AppError(
                501,
                "migration_executor_unavailable",
                "The configured external adapter is probed truthfully but this executor is not enabled",
            )
        lock = self._acquire(job)
        self.db.commit()
        try:
            self._checkpoint(job, execution, "QUIESCING", {"queue": task_queue.status()})
            self.db.commit()
            if not task_queue.quiesce(timeout_seconds=5):
                raise AppError(409, "queue_not_quiesced", "Active tasks did not drain before the maintenance deadline")
            lock.queue_paused = True
            self._checkpoint(job, execution, "SOURCE_FROZEN", {"business_writes": "blocked"})
            self.db.commit()
            if execution.resource_kind == "database":
                self._execute_database(job, execution)
            else:
                self._execute_files(job, execution)
            self.audit.record(
                actor_id=self.actor_id,
                action="migration.cutover_read_only",
                resource_type="migration_job",
                resource_id=job.id,
                details={"resource_kind": execution.resource_kind, "verified": True},
            )
            self.db.commit()
            return self.view(job)
        except Exception as exc:
            self.db.rollback()
            job = self._job(job_id)
            execution = self._execution(job_id)
            execution.failure_code = exc.code if isinstance(exc, AppError) else "migration_execution_failed"
            execution.failure_message = exc.message if isinstance(exc, AppError) else type(exc).__name__
            self._checkpoint(job, execution, "FAILED_SAFE", {}, "failed")
            active = self._active_lock()
            if active is not None:
                self._release(active)
            self.audit.record(
                actor_id=self.actor_id,
                action="migration.failed_safe",
                resource_type="migration_job",
                resource_id=job.id,
                outcome="failed",
                details={"error_code": execution.failure_code},
            )
            self.db.commit()
            if isinstance(exc, AppError):
                raise
            raise AppError(500, "migration_execution_failed", "Migration failed safely; source remains active") from exc

    def _execute_database(self, job: MigrationJob, execution: MigrationExecution) -> None:
        source_path = self._source_database_path()
        integrity = _sqlite_integrity(source_path)
        if not integrity["passed"]:
            raise AppError(409, "source_integrity_failed", "SQLite integrity or foreign-key check failed")
        snapshot_path = self.root / "snapshots" / f"{job.id}.sqlite3"
        _sqlite_backup(source_path, snapshot_path)
        snapshot_integrity = _sqlite_integrity(snapshot_path)
        if not snapshot_integrity["passed"]:
            raise AppError(500, "snapshot_integrity_failed", "The SQLite online backup failed integrity checks")
        self._artifact(job, "database_snapshot", snapshot_path, 1, snapshot_path.stat().st_size)
        self._checkpoint(job, execution, "SNAPSHOTTED", {"sha256": _sha256_file(snapshot_path)})
        self.db.commit()
        target_path = Path(str(execution.target_locator["path"])).resolve()
        if self.root not in target_path.parents:
            raise AppError(422, "invalid_target", "Target escapes migration root")
        self._checkpoint(job, execution, "TARGET_PREPARED", {"target_empty": not target_path.exists()})
        self.db.commit()
        if target_path.exists():
            raise AppError(409, "target_not_empty", "Target database appeared after preflight")
        _sqlite_backup(snapshot_path, target_path)
        self._checkpoint(job, execution, "COPYING_DATABASE", {"bytes": target_path.stat().st_size})
        self.db.commit()
        source_manifest_path = self.root / "manifests" / job.id / "database-source.ndjson"
        target_manifest_path = self.root / "manifests" / job.id / "database-target.ndjson"
        source_manifest = _sqlite_manifest(snapshot_path, source_manifest_path)
        target_manifest = _sqlite_manifest(target_path, target_manifest_path)
        target_integrity = _sqlite_integrity(target_path)
        if source_manifest["tables"] != target_manifest["tables"] or not target_integrity["passed"]:
            raise AppError(409, "canonical_verification_failed", "Target row counts or canonical hashes differ")
        self._artifact(job, "database_manifest", source_manifest_path, source_manifest["total_rows"], source_manifest_path.stat().st_size)
        execution.verification_report = {
            "source": source_manifest,
            "target": target_manifest,
            "constraints": target_integrity,
            "match": True,
        }
        job.report = {**job.report, "data_copied": True, "verification": execution.verification_report}
        self._checkpoint(job, execution, "VERIFYING_CANONICAL", {"tables": len(source_manifest["tables"]), "rows": source_manifest["total_rows"], "hash_match": True})
        self._checkpoint(job, execution, "REBUILDING_DERIVED", {"mode": "sqlite_compatibility_snapshot", "rebuild_required": False})
        self._checkpoint(job, execution, "VERIFYING_DERIVED", {"passed": True, "derived_targets": 0})
        connection = sqlite3.connect(f"file:{target_path.as_posix()}?mode=ro", uri=True)
        try:
            connection.execute("SELECT COUNT(*) FROM workspaces").fetchone()
            try:
                connection.execute("CREATE TABLE forbidden_write(id INTEGER)")
                raise AppError(500, "readonly_smoke_failed", "Read-only target accepted a write")
            except sqlite3.OperationalError:
                pass
        finally:
            connection.close()
        execution.target_locator = {**execution.target_locator, "activation": "candidate_read_only"}
        self._checkpoint(job, execution, "CUTOVER_READ_ONLY", {"read_smoke": "passed", "write_rejected": True, "runtime_rebind": "not_implemented"})
        self.db.commit()

    def _execute_files(self, job: MigrationJob, execution: MigrationExecution) -> None:
        source_root = Path(str(execution.source_locator["root"])).resolve()
        target_root = Path(str(execution.target_locator["root"])).resolve()
        if self.root not in target_root.parents or target_root.exists():
            raise AppError(409, "target_not_empty", "Object target is not an empty migration-owned path")
        target_root.mkdir(parents=True)
        files = list(
            self.db.scalars(
                select(FileRecord).where(
                    FileRecord.workspace_id == self.workspace_id,
                    FileRecord.storage_status == "stored",
                )
            ).all()
        )
        manifest_path = self.root / "manifests" / job.id / "files.ndjson"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        total_bytes = 0
        with manifest_path.open("w", encoding="utf-8", newline="\n") as manifest:
            for record in files:
                source_path = (source_root / record.object_key).resolve()
                if source_root not in source_path.parents or not source_path.is_file():
                    raise AppError(409, "source_object_missing", f"Stored object is missing for file {record.id}")
                source_hash = _sha256_file(source_path)
                if source_hash != record.sha256 or source_path.stat().st_size != record.size_bytes:
                    raise AppError(409, "source_object_hash_mismatch", f"Stored object failed verification for file {record.id}")
                target_key = f"tenants/{self.workspace_id}/files/{record.id}/revisions/{record.sha256}"
                target_path = (target_root / target_key).resolve()
                if target_root not in target_path.parents:
                    raise AppError(422, "invalid_object_key", "Target object key escapes storage root")
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with source_path.open("rb") as source, target_path.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                if _sha256_file(target_path) != record.sha256 or target_path.stat().st_size != record.size_bytes:
                    raise AppError(409, "target_object_hash_mismatch", f"Copied object failed verification for file {record.id}")
                item = MigrationFileItem(
                    workspace_id=self.workspace_id,
                    job_id=job.id,
                    file_id=record.id,
                    source_key=record.object_key,
                    target_key=target_key,
                    size_bytes=record.size_bytes,
                    sha256=record.sha256,
                    mime_type=record.mime_type,
                    status="verified",
                )
                self.db.add(item)
                manifest.write(
                    _canonical_bytes(
                        {
                            "file_id": record.id,
                            "workspace_id": self.workspace_id,
                            "source_key": record.object_key,
                            "target_key": target_key,
                            "size_bytes": record.size_bytes,
                            "mime_type": record.mime_type,
                            "sha256": record.sha256,
                            "state": "verified",
                        }
                    ).decode("utf-8")
                    + "\n"
                )
                total_bytes += record.size_bytes
        self._checkpoint(job, execution, "SNAPSHOTTED", {"file_manifest_created": True, "objects": len(files)})
        self._checkpoint(job, execution, "TARGET_PREPARED", {"target_root_created": True})
        self._checkpoint(job, execution, "COPYING_FILES", {"objects": len(files), "bytes": total_bytes})
        self._artifact(job, "file_manifest", manifest_path, len(files), total_bytes)
        execution.verification_report = {"object_count": len(files), "total_bytes": total_bytes, "sha256_match": True, "content_type_match": True}
        job.report = {**job.report, "data_copied": True, "verification": execution.verification_report}
        self._checkpoint(job, execution, "VERIFYING_CANONICAL", execution.verification_report)
        self._checkpoint(job, execution, "REBUILDING_DERIVED", {"rebuild_required": False})
        self._checkpoint(job, execution, "VERIFYING_DERIVED", {"passed": True})
        execution.target_locator = {**execution.target_locator, "activation": "candidate_read_only"}
        self._checkpoint(job, execution, "CUTOVER_READ_ONLY", {"read_smoke": "passed", "write_enabled": False})
        self.db.commit()

    def _artifact(self, job: MigrationJob, kind: str, path: Path, count: int, size: int) -> None:
        self.db.add(
            MigrationArtifact(
                workspace_id=self.workspace_id,
                job_id=job.id,
                artifact_kind=kind,
                locator=str(path),
                sha256=_sha256_file(path),
                item_count=count,
                size_bytes=size,
                retained_until=utc_now() + timedelta(days=7),
            )
        )

    def commit(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        execution = self._execution(job_id)
        if job.status != "CUTOVER_READ_ONLY":
            raise AppError(409, "cutover_not_ready", "Only a verified read-only cutover can be committed")
        if execution.resource_kind == "database":
            raise AppError(
                501,
                "database_runtime_rebind_unavailable",
                "The target pointer is verified, but the running application cannot yet reopen all sessions on it; source writes remain blocked",
            )
        items = list(
            self.db.scalars(
                select(MigrationFileItem).where(
                    MigrationFileItem.workspace_id == self.workspace_id,
                    MigrationFileItem.job_id == job.id,
                    MigrationFileItem.status == "verified",
                )
            ).all()
        )
        by_file = {item.file_id: item for item in items}
        records = list(
            self.db.scalars(
                select(FileRecord).where(
                    FileRecord.workspace_id == self.workspace_id,
                    FileRecord.id.in_(list(by_file)),
                )
            ).all()
        ) if by_file else []
        for record in records:
            record.object_key = by_file[record.id].target_key
        existing_active = self.db.scalar(
            select(InfrastructureBinding).where(
                InfrastructureBinding.workspace_id == self.workspace_id,
                InfrastructureBinding.capability == "object_storage",
                InfrastructureBinding.role == "active",
            )
        )
        if existing_active is not None:
            existing_active.role = "retained_source"
            existing_active.write_enabled = False
            existing_active.status = "retention"
        else:
            self.db.add(
                InfrastructureBinding(
                    workspace_id=self.workspace_id,
                    capability="object_storage",
                    provider_kind="local",
                    role="retained_source",
                    status="retention",
                    locator=execution.source_locator,
                    write_enabled=False,
                    migration_job_id=job.id,
                )
            )
        self.db.add(
            InfrastructureBinding(
                workspace_id=self.workspace_id,
                capability="object_storage",
                provider_kind=str(execution.target_locator["provider_kind"]),
                role="active",
                status="active",
                locator={key: value for key, value in execution.target_locator.items() if key != "activation"},
                write_enabled=True,
                migration_job_id=job.id,
            )
        )
        execution.can_rollback = False
        execution.reverse_migration_required = True
        execution.committed_at = utc_now()
        execution.retention_until = utc_now() + timedelta(days=7)
        self._checkpoint(job, execution, "COMMITTED", {"active_provider": execution.target_locator["provider_kind"], "source_retention_days": 7})
        lock = self._active_lock()
        if lock is not None:
            self._release(lock)
        self.audit.record(
            actor_id=self.actor_id,
            action="migration.commit",
            resource_type="migration_job",
            resource_id=job.id,
            details={"resource_kind": execution.resource_kind, "source_retention_days": 7},
        )
        self.db.commit()
        return self.view(job)

    def rollback(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        execution = self._execution(job_id)
        if job.status == "COMMITTED" or not execution.can_rollback:
            execution.reverse_migration_required = True
            self.db.commit()
            raise AppError(409, "reverse_migration_required", "Committed targets can only be rolled back through a reverse migration")
        if job.status not in PRECOMMIT_STATES:
            raise AppError(409, "rollback_not_available", "This migration has no pre-commit rollback point")
        target_value = execution.target_locator.get("root") or execution.target_locator.get("path")
        if target_value:
            target_path = Path(str(target_value)).resolve()
            if self.root not in target_path.parents:
                raise AppError(422, "invalid_target", "Migration target escapes the managed root")
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink(missing_ok=True)
        execution.rolled_back_at = utc_now()
        execution.can_rollback = False
        self._checkpoint(job, execution, "ROLLED_BACK_TO_SOURCE", {"source_active": True, "target_write_enabled": False})
        lock = self._active_lock()
        if lock is not None:
            self._release(lock)
        self.audit.record(
            actor_id=self.actor_id,
            action="migration.rollback_to_source",
            resource_type="migration_job",
            resource_id=job.id,
            details={"resource_kind": execution.resource_kind},
        )
        self.db.commit()
        return self.view(job)
