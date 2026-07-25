from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.schema import Table
from sqlalchemy.types import DateTime, JSON
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.errors import AppError
from app.domain.migration_models import MaintenanceLock, MigrationCheckpoint, MigrationExecution
from app.domain.models import MigrationJob, Workspace, utc_now
from app.providers.local.storage import LocalObjectStorageProvider, safe_filename
from app.repositories.audit import AuditRepository
from app.services.workspace_export import (
    WORKSPACE_EXPORT_FORMAT,
    WORKSPACE_EXPORT_SCHEMA_VERSION,
    _EXCLUDED_TABLES,
    _is_sensitive_key,
    _sha256,
)


_OPERATIONAL_TABLES = frozenset(
    {
        "infrastructure_bindings",
        "maintenance_locks",
        "migration_artifacts",
        "migration_checkpoints",
        "migration_executions",
        "migration_file_items",
        "migration_jobs",
    }
)
_RESTORE_EXCLUDED_TABLES = _EXCLUDED_TABLES | _OPERATIONAL_TABLES


def _safe_archive_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "" in path.parts:
        raise AppError(422, "backup_path_invalid", "Backup contains an unsafe archive path")
    return path


def _parse_json_member(archive: ZipFile, name: str) -> dict[str, Any]:
    try:
        value = json.loads(archive.read(name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
        raise AppError(422, "backup_manifest_invalid", "Backup contains invalid JSON metadata") from exc
    if not isinstance(value, dict):
        raise AppError(422, "backup_manifest_invalid", "Backup metadata must be a JSON object")
    return value


def _restore_order(table_names: set[str]) -> list[str]:
    dependencies: dict[str, set[str]] = {}
    for name in table_names:
        table = Base.metadata.tables[name]
        dependencies[name] = {
            foreign_key.column.table.name
            for foreign_key in table.foreign_keys
            if foreign_key.column.table.name in table_names and foreign_key.column.table.name != name
        }
    ordered: list[str] = []
    pending = set(table_names)
    while pending:
        ready = sorted(name for name in pending if not dependencies[name] & pending)
        if not ready:
            # Cycles are safe for upsert because the referenced rows may already
            # exist. Keep deterministic ordering rather than silently skipping.
            ready = [sorted(pending)[0]]
        ordered.extend(ready)
        pending.difference_update(ready)
    return ordered


class WorkspaceRestoreService:
    """Validate and merge a credential-free full workspace backup."""

    def __init__(
        self,
        db: Session,
        workspace: Workspace,
        actor_id: str,
        storage_root: Path,
        memory_root: Path,
        max_backup_bytes: int,
    ) -> None:
        self.db = db
        self.workspace = workspace
        self.workspace_id = workspace.id
        self.actor_id = actor_id
        self.storage_root = storage_root.resolve()
        self.memory_root = memory_root.resolve()
        self.max_backup_bytes = max_backup_bytes
        self.audit = AuditRepository(db, workspace.id)

    def restore(self, payload: bytes) -> dict[str, Any]:
        if len(payload) > self.max_backup_bytes:
            raise AppError(413, "backup_too_large", "Backup exceeds the configured restore limit")
        try:
            archive = ZipFile(BytesIO(payload))
        except BadZipFile as exc:
            raise AppError(422, "backup_invalid", "The uploaded file is not a valid ZIP backup") from exc

        names = archive.namelist()
        if len(names) != len(set(names)):
            raise AppError(422, "backup_duplicate_path", "Backup contains duplicate archive paths")
        for name in names:
            _safe_archive_path(name)
            info = archive.getinfo(name)
            if info.file_size > self.max_backup_bytes:
                raise AppError(413, "backup_member_too_large", "A backup member exceeds the configured restore limit")

        if "manifest.json" not in names:
            raise AppError(422, "backup_manifest_missing", "Backup manifest.json is missing")
        manifest = _parse_json_member(archive, "manifest.json")
        if manifest.get("format") != WORKSPACE_EXPORT_FORMAT:
            raise AppError(422, "backup_format_unsupported", "This backup format is not supported")
        if manifest.get("schema_version") != WORKSPACE_EXPORT_SCHEMA_VERSION:
            raise AppError(422, "backup_schema_unsupported", "This backup schema version is not supported")
        privacy = manifest.get("privacy")
        if not isinstance(privacy, dict) or privacy.get("credentials_included") is not False:
            raise AppError(422, "backup_privacy_invalid", "Backups containing credentials cannot be restored")

        table_records = self._read_table_records(archive, manifest)
        file_entries = self._read_file_entries(archive, manifest)
        memory_entries = self._read_memory_entries(archive, manifest)

        existing_lock = self.db.scalar(
            select(MaintenanceLock).where(
                MaintenanceLock.workspace_id == self.workspace_id,
                MaintenanceLock.status == "active",
            )
        )
        if existing_lock is not None:
            raise AppError(503, "workspace_maintenance", "Another migration or restore already owns the workspace")

        job = MigrationJob(
            workspace_id=self.workspace_id,
            source_kind="workspace_backup",
            target_kind="workspace_restore",
            status="RESTORING",
            report={"source_workspace_id": manifest.get("workspace", {}).get("id")},
        )
        self.db.add(job)
        self.db.flush()
        execution = MigrationExecution(
            workspace_id=self.workspace_id,
            job_id=job.id,
            resource_kind="workspace_restore",
            state="RESTORING",
            source_locator={"format": WORKSPACE_EXPORT_FORMAT},
            target_locator={"workspace_id": self.workspace_id},
        )
        lock = MaintenanceLock(
            workspace_id=self.workspace_id,
            job_id=job.id,
            scope="workspace",
            status="active",
            acquired_by=self.actor_id,
            acquired_at=utc_now(),
        )
        self.db.add_all([execution, lock])
        self.db.flush()
        restored_tables = 0
        restored_records = 0
        restored_files = 0
        restored_memory_files = 0
        written_paths: list[Path] = []
        try:
            for table_name in _restore_order(set(table_records)):
                if table_name in _RESTORE_EXCLUDED_TABLES:
                    continue
                table = Base.metadata.tables.get(table_name)
                if table is None or "workspace_id" not in table.c:
                    raise AppError(422, "backup_table_invalid", f"Table '{table_name}' is not workspace scoped")
                records = table_records[table_name]
                if not records:
                    continue
                for raw_record in records:
                    self._upsert_record(table, raw_record)
                    restored_records += 1
                restored_tables += 1

            self.db.flush()
            storage = LocalObjectStorageProvider(self.storage_root)
            file_rows = {
                str(row.get("id")): row
                for row in table_records.get("files", [])
                if isinstance(row, dict) and row.get("id")
            }
            for entry in file_entries:
                file_id = str(entry["file_id"])
                row = file_rows.get(file_id)
                if row is None:
                    raise AppError(422, "backup_file_record_missing", "Backup file metadata is incomplete")
                object_key = row.get("object_key")
                if not isinstance(object_key, str) or not object_key:
                    raise AppError(422, "backup_object_key_invalid", "Backup contains an invalid object key")
                destination = storage._resolve(object_key)
                content = archive.read(str(entry["path"]))
                if len(content) != int(entry["size_bytes"]) or _sha256(content) != entry["sha256"]:
                    raise AppError(422, "backup_file_integrity_failed", "A backup file failed SHA-256 verification")
                if destination.exists() and _sha256(destination.read_bytes()) == entry["sha256"]:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(f".{destination.name}.restore.tmp")
                temporary.write_bytes(content)
                temporary.replace(destination)
                written_paths.append(destination)
                restored_files += 1

            memory_workspace_root = (self.memory_root / safe_filename(self.workspace_id)).resolve()
            if self.memory_root not in memory_workspace_root.parents:
                raise AppError(422, "backup_memory_scope_invalid", "Workspace memory path escapes the managed root")
            for entry in memory_entries:
                relative = _safe_archive_path(str(entry["relative_path"]))
                destination = (memory_workspace_root / Path(*relative.parts)).resolve()
                if memory_workspace_root not in destination.parents:
                    raise AppError(422, "backup_memory_path_invalid", "Backup contains an invalid memory path")
                content = archive.read(str(entry["path"]))
                if len(content) != int(entry["size_bytes"]) or _sha256(content) != entry["sha256"]:
                    raise AppError(422, "backup_memory_integrity_failed", "A Memory file failed SHA-256 verification")
                if destination.exists() and _sha256(destination.read_bytes()) == entry["sha256"]:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(f".{destination.name}.restore.tmp")
                temporary.write_bytes(content)
                temporary.replace(destination)
                written_paths.append(destination)
                restored_memory_files += 1

            report = {
                "source_workspace_id": manifest.get("workspace", {}).get("id"),
                "tables": restored_tables,
                "records": restored_records,
                "files": restored_files,
                "memory_files": restored_memory_files,
                "mode": "merge",
            }
            job.status = "RESTORED"
            job.report = report
            execution.state = "RESTORED"
            execution.verification_report = report
            execution.committed_at = utc_now()
            lock.status = "released"
            lock.released_at = utc_now()
            self.db.add(
                MigrationCheckpoint(
                    workspace_id=self.workspace_id,
                    job_id=job.id,
                    sequence=1,
                    state="RESTORED",
                    status="completed",
                    metrics=report,
                    started_at=job.created_at or utc_now(),
                    finished_at=utc_now(),
                )
            )
            self.audit.record(
                actor_id=self.actor_id,
                action="workspace.restore",
                resource_type="workspace_backup",
                resource_id=job.id,
                details=report,
            )
            self.db.commit()
            return {"job_id": job.id, **report}
        except IntegrityError as exc:
            self.db.rollback()
            for path in written_paths:
                path.unlink(missing_ok=True)
            raise AppError(409, "backup_restore_conflict", "Backup records conflict with existing workspace data") from exc
        except Exception:
            self.db.rollback()
            for path in written_paths:
                path.unlink(missing_ok=True)
            raise
        finally:
            archive.close()

    def _read_table_records(self, archive: ZipFile, manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        entries = manifest.get("tables")
        if not isinstance(entries, list):
            raise AppError(422, "backup_manifest_invalid", "Backup table manifest is invalid")
        result: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("path"), str):
                raise AppError(422, "backup_manifest_invalid", "Backup table entry is invalid")
            table_name = entry["name"]
            path = str(_safe_archive_path(entry["path"]))
            if path not in archive.namelist() or _sha256(archive.read(path)) != entry.get("sha256"):
                raise AppError(422, "backup_table_integrity_failed", f"Backup table '{table_name}' failed integrity verification")
            payload = _parse_json_member(archive, path)
            records = payload.get("records")
            if payload.get("table") != table_name or not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
                raise AppError(422, "backup_table_invalid", f"Backup table '{table_name}' is invalid")
            result[table_name] = records
        return result

    def _read_file_entries(self, archive: ZipFile, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        entries = manifest.get("files", [])
        if not isinstance(entries, list):
            raise AppError(422, "backup_manifest_invalid", "Backup file manifest is invalid")
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("file_id") or not entry.get("path"):
                raise AppError(422, "backup_manifest_invalid", "Backup file entry is invalid")
            _safe_archive_path(str(entry["path"]))
        return [entry for entry in entries if isinstance(entry, dict)]

    def _read_memory_entries(self, archive: ZipFile, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        entries = manifest.get("memory", [])
        if not isinstance(entries, list):
            raise AppError(422, "backup_manifest_invalid", "Backup Memory manifest is invalid")
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("relative_path") or not entry.get("path"):
                raise AppError(422, "backup_manifest_invalid", "Backup Memory entry is invalid")
            _safe_archive_path(str(entry["path"]))
        return [entry for entry in entries if isinstance(entry, dict)]

    def _upsert_record(self, table: Table, raw_record: dict[str, Any]) -> None:
        if any(_is_sensitive_key(str(key)) for key in raw_record):
            raise AppError(422, "backup_sensitive_field", "Backup contains a sensitive field")
        values: dict[str, Any] = {}
        for column in table.columns:
            if column.name not in raw_record:
                continue
            value = raw_record[column.name]
            if column.name == "workspace_id":
                value = self.workspace_id
            if isinstance(column.type, DateTime) and isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(column.type, JSON) and isinstance(value, str):
                value = json.loads(value)
            values[column.name] = value
        missing = [
            column.name
            for column in table.columns
            if column.name not in values
            and not column.nullable
            and column.default is None
            and column.server_default is None
            and not column.primary_key
        ]
        if missing:
            raise AppError(422, "backup_record_incomplete", f"Backup record for '{table.name}' is missing required fields", {"fields": missing})
        primary_key = [column for column in table.primary_key.columns if column.name in values]
        if len(primary_key) != len(table.primary_key.columns):
            raise AppError(422, "backup_record_incomplete", f"Backup record for '{table.name}' has no complete primary key")
        predicate = and_(*(column == values[column.name] for column in primary_key))
        existing = self.db.execute(select(table).where(predicate)).first()
        if existing is None:
            self.db.execute(table.insert().values(**values))
            return
        updates = {key: value for key, value in values.items() if key not in {column.name for column in primary_key}}
        if updates:
            self.db.execute(table.update().where(predicate).values(**updates))
