from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.errors import AppError
from app.domain import extension_models as _extension_models  # noqa: F401
from app.domain.models import FileRecord, Workspace, utc_now
from app.providers.local.storage import LocalObjectStorageProvider, safe_filename
from app.repositories.audit import AuditRepository


WORKSPACE_EXPORT_FORMAT = "learngraph.workspace-export"
WORKSPACE_EXPORT_SCHEMA_VERSION = "1.0"

# Credentials and encrypted deletion-recovery material are absent from both the
# manifest and archive rather than being exported as partially redacted rows.
_EXCLUDED_TABLES = frozenset(
    {
        "provider_secrets",
        "provider_response_states",
        "memory_deletion_recoveries",
        "mcp_server_credentials",
        "infrastructure_database_configurations",
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "current_password",
        "new_password",
        "token_hash",
        "access_token",
        "refresh_token",
        "bearer_token",
        "id_token",
        "session_token",
        "token",
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "api_key",
        "api_key_plaintext",
        "client_secret",
        "private_key",
        "credentials",
        "secret",
        "ciphertext",
        "encrypted_payload",
        "recovery_key",
        "master_key",
    }
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_PATH_FRAGMENT = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>]+"
)
_POSIX_PATH_FRAGMENT = re.compile(
    r"(?<![:/A-Za-z0-9])/(?:[^/\s\"'<>]+/)*[^/\s\"'<>]+"
)
_BEARER_TOKEN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_URI_USERINFO = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)"
    r"(?P<username>[^:/\s]+):[^@\s/]+@"
)


def _is_sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return bool(
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("_token")
        or normalized.startswith("token_")
        or any(
            marker in normalized
            for marker in (
                "password",
                "api_key",
                "client_secret",
                "private_key",
                "ciphertext",
                "encrypted_payload",
            )
        )
    )


def _looks_like_absolute_path(value: str) -> bool:
    stripped = value.strip()
    return bool(
        stripped.startswith(("/", "\\\\"))
        or _WINDOWS_ABSOLUTE_PATH.match(stripped)
    )


def _safe_text(value: str) -> str:
    if _looks_like_absolute_path(value):
        return "[redacted:absolute-path]"
    redacted = _WINDOWS_PATH_FRAGMENT.sub("[redacted:absolute-path]", value)
    redacted = _POSIX_PATH_FRAGMENT.sub("[redacted:absolute-path]", redacted)
    redacted = _BEARER_TOKEN.sub("Bearer [redacted]", redacted)
    return _URI_USERINFO.sub(
        lambda match: (
            f"{match.group('scheme')}{match.group('username')}:[redacted]@"
        ),
        redacted,
    )


def _safe_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return None
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str):
            return _safe_text(value)
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, datetime):
        normalized = (
            value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        )
        return normalized.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return "[redacted:binary]"
    if isinstance(value, dict):
        return {
            str(item_key): _safe_value(item_value, key=str(item_key))
            for item_key, item_value in sorted(
                value.items(), key=lambda item: str(item[0])
            )
            if not _is_sensitive_key(str(item_key))
        }
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item) for item in value]
    return str(value)


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class WorkspaceExportService:
    """Create a workspace-isolated, credential-free, open ZIP archive."""

    def __init__(
        self,
        db: Session,
        workspace: Workspace,
        actor_id: str,
        storage_root: Path,
        max_upload_bytes: int,
        memory_root: Path | None = None,
    ) -> None:
        self.db = db
        self.workspace = workspace
        self.workspace_id = workspace.id
        self.actor_id = actor_id
        self.storage = LocalObjectStorageProvider(storage_root)
        self.max_upload_bytes = max_upload_bytes
        self.memory_root = memory_root.resolve() if memory_root is not None else None
        self.audit = AuditRepository(db, workspace.id)

    def export_zip(self) -> bytes:
        table_entries: list[dict[str, Any]] = []
        file_entries: list[dict[str, Any]] = []
        memory_entries: list[dict[str, Any]] = []
        archive_buffer = BytesIO()

        with ZipFile(archive_buffer, "w", compression=ZIP_DEFLATED) as archive:
            for table in sorted(
                Base.metadata.tables.values(), key=lambda item: item.name
            ):
                if "workspace_id" not in table.c or table.name in _EXCLUDED_TABLES:
                    continue
                order_columns = list(table.primary_key.columns)
                statement = select(table).where(
                    table.c.workspace_id == self.workspace_id
                )
                if order_columns:
                    statement = statement.order_by(*order_columns)
                rows = [
                    {
                        column.name: _safe_value(
                            row._mapping[column.name], key=column.name
                        )
                        for column in table.columns
                        if not _is_sensitive_key(column.name)
                    }
                    for row in self.db.execute(statement)
                ]
                path = f"data/{table.name}.json"
                payload = _json_bytes(
                    {
                        "format": WORKSPACE_EXPORT_FORMAT,
                        "schema_version": WORKSPACE_EXPORT_SCHEMA_VERSION,
                        "table": table.name,
                        "records": rows,
                    }
                )
                archive.writestr(path, payload)
                table_entries.append(
                    {
                        "name": table.name,
                        "path": path,
                        "record_count": len(rows),
                        "sha256": _sha256(payload),
                    }
                )

            file_records = self.db.scalars(
                select(FileRecord)
                .where(FileRecord.workspace_id == self.workspace_id)
                .order_by(FileRecord.id)
            ).all()
            workspace_storage_prefix = safe_filename(self.workspace_id)
            for record in file_records:
                object_parts = PurePosixPath(
                    record.object_key.replace("\\", "/")
                ).parts
                if not object_parts or object_parts[0] != workspace_storage_prefix:
                    raise AppError(
                        409,
                        "workspace_export_file_scope_invalid",
                        "A stored file does not belong to the selected workspace storage scope",
                        {"file_id": record.id},
                    )
                try:
                    payload = self.storage.read_bytes(
                        record.object_key,
                        limit_bytes=max(self.max_upload_bytes, record.size_bytes),
                    )
                except FileNotFoundError as exc:
                    raise AppError(
                        409,
                        "workspace_export_file_missing",
                        "A stored file is missing; a complete workspace export cannot be created",
                        {"file_id": record.id},
                    ) from exc
                digest = _sha256(payload)
                if len(payload) != record.size_bytes or digest != record.sha256:
                    raise AppError(
                        409,
                        "workspace_export_file_integrity_failed",
                        "A stored file failed size or SHA-256 verification",
                        {"file_id": record.id},
                    )
                path = f"uploads/{record.id}/{safe_filename(record.original_name)}"
                archive.writestr(path, payload)
                file_entries.append(
                    {
                        "file_id": record.id,
                        "original_name": _safe_text(record.original_name),
                        "path": path,
                        "size_bytes": len(payload),
                        "sha256": digest,
                    }
                )

            if self.memory_root is not None:
                memory_workspace_root = (
                    self.memory_root / safe_filename(self.workspace_id)
                ).resolve()
                if self.memory_root not in memory_workspace_root.parents:
                    raise AppError(
                        409,
                        "workspace_export_memory_scope_invalid",
                        "The workspace memory path escapes the managed memory root",
                    )
                if memory_workspace_root.exists():
                    for source_path in sorted(memory_workspace_root.rglob("*")):
                        if not source_path.is_file() or source_path.name.endswith(".tmp"):
                            continue
                        relative = source_path.relative_to(memory_workspace_root)
                        payload = source_path.read_bytes()
                        archive_path = (
                            PurePosixPath("memory") / PurePosixPath(relative.as_posix())
                        ).as_posix()
                        archive.writestr(archive_path, payload)
                        memory_entries.append(
                            {
                                "path": archive_path,
                                "relative_path": relative.as_posix(),
                                "size_bytes": len(payload),
                                "sha256": _sha256(payload),
                            }
                        )

            manifest = {
                "format": WORKSPACE_EXPORT_FORMAT,
                "schema_version": WORKSPACE_EXPORT_SCHEMA_VERSION,
                "exported_at": utc_now().isoformat(),
                "workspace": {
                    "id": _safe_text(self.workspace.id),
                    "name": _safe_text(self.workspace.name),
                    "description": _safe_text(self.workspace.description),
                    "kind": _safe_text(self.workspace.workspace_kind),
                    "created_at": _safe_value(self.workspace.created_at),
                    "updated_at": _safe_value(self.workspace.updated_at),
                },
                "tables": table_entries,
                "files": file_entries,
                "memory": memory_entries,
                "counts": {
                    "tables": len(table_entries),
                    "records": sum(
                        item["record_count"] for item in table_entries
                    ),
                    "files": len(file_entries),
                    "file_bytes": sum(item["size_bytes"] for item in file_entries),
                    "memory_files": len(memory_entries),
                    "memory_bytes": sum(item["size_bytes"] for item in memory_entries),
                },
                "privacy": {
                    "credentials_included": False,
                    "authentication_records_included": False,
                    "encrypted_recovery_material_included": False,
                    "absolute_paths_included": False,
                },
            }
            archive.writestr("manifest.json", _json_bytes(manifest))

        payload = archive_buffer.getvalue()
        self.audit.record(
            actor_id=self.actor_id,
            action="workspace.export",
            resource_type="workspace_export",
            resource_id=self.workspace_id,
            details={
                "format": "open_json_zip",
                "schema_version": WORKSPACE_EXPORT_SCHEMA_VERSION,
                "table_count": len(table_entries),
                "record_count": sum(
                    item["record_count"] for item in table_entries
                ),
                "file_count": len(file_entries),
                "memory_file_count": len(memory_entries),
                "archive_size_bytes": len(payload),
                "archive_sha256": _sha256(payload),
            },
        )
        self.db.commit()
        return payload
