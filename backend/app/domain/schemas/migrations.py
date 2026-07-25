from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator


class MigrationPreflightRequest(BaseModel):
    source_kind: str = Field(min_length=1, max_length=80)
    target_kind: str = Field(min_length=1, max_length=80)
    resource_kind: Literal["database", "object_storage"] | None = None
    target_name: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("source_kind", "target_kind")
    @classmethod
    def normalize_kind(cls, value: str) -> str:
        normalized = value.strip().casefold()
        return "postgresql" if normalized == "postgres" else normalized

    @field_validator("target_name")
    @classmethod
    def safe_target_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized.replace("-", "").replace("_", "").isalnum():
            raise ValueError("target_name accepts only letters, numbers, '-' and '_'")
        return normalized


class MigrationConfirmRequest(BaseModel):
    confirm: Literal[True]


class MigrationCheckpointView(BaseModel):
    sequence: int
    state: str
    status: str
    metrics: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class MigrationJobView(BaseModel):
    id: str
    workspace_id: str
    source_kind: str
    target_kind: str
    status: str
    report: dict[str, Any]
    resource_kind: str
    can_rollback: bool
    reverse_migration_required: bool
    maintenance_active: bool
    checkpoints: list[MigrationCheckpointView]
    created_at: datetime
    updated_at: datetime


class AdapterStatusView(BaseModel):
    provider_kind: str
    capability: str
    status: str
    configured: bool
    driver_available: bool
    connection_verified: bool
    details: dict[str, Any]


class DatabaseConfigurationUpsertRequest(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr | None = None
    ssl_mode: Literal["disable", "prefer", "require"] = "prefer"

    @field_validator("host", "database_name", "username")
    @classmethod
    def normalize_database_field(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Database connection fields cannot be blank")
        if any(character in normalized for character in ("\r", "\n", "\x00")):
            raise ValueError("Database connection fields contain invalid characters")
        return normalized


class DatabaseConfigurationView(BaseModel):
    provider_kind: Literal["postgresql", "mysql"]
    host: str
    port: int
    database_name: str
    username: str
    ssl_mode: Literal["disable", "prefer", "require"]
    password_configured: bool
    status: str
    driver_available: bool
    connection_verified: bool
    last_error_code: str | None = None
    last_verified_at: datetime | None = None
    updated_at: datetime


class BackupRestoreView(BaseModel):
    job_id: str
    source_workspace_id: str | None = None
    tables: int
    records: int
    files: int
    memory_files: int
    mode: str
