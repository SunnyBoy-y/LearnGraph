from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domain.models import TimestampMixin, WorkspaceScopedMixin, new_id


class MigrationExecution(Base, TimestampMixin, WorkspaceScopedMixin):
    """Durable execution envelope for one database or object-storage migration."""

    __tablename__ = "migration_executions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "job_id", name="uq_migration_execution_job"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("migration_jobs.id", ondelete="CASCADE"), index=True
    )
    resource_kind: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[str] = mapped_column(String(48), default="DRAFT", index=True)
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    target_locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verification_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    can_rollback: Mapped[bool] = mapped_column(Boolean, default=True)
    reverse_migration_required: Mapped[bool] = mapped_column(Boolean, default=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class MigrationCheckpoint(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "migration_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "job_id", "sequence", name="uq_migration_checkpoint_sequence"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("migration_jobs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(48), index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MigrationFileItem(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "migration_file_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "job_id", "file_id", name="uq_migration_file_item"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("migration_jobs.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[str] = mapped_column(String(36), index=True)
    source_key: Mapped[str] = mapped_column(String(1000))
    target_key: Mapped[str] = mapped_column(String(1000))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class MigrationArtifact(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "migration_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("migration_jobs.id", ondelete="CASCADE"), index=True
    )
    artifact_kind: Mapped[str] = mapped_column(String(48), index=True)
    locator: Mapped[str] = mapped_column(String(1200))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    retained_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MaintenanceLock(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "maintenance_locks"
    __table_args__ = (
        UniqueConstraint("workspace_id", "job_id", name="uq_workspace_maintenance_job"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("migration_jobs.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(32), default="workspace")
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    queue_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    acquired_by: Mapped[str] = mapped_column(String(64))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InfrastructureBinding(Base, TimestampMixin, WorkspaceScopedMixin):
    """Provider pointer. Locators contain no passwords, tokens, or access keys."""

    __tablename__ = "infrastructure_bindings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "capability", "role", name="uq_infrastructure_binding_role"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    capability: Mapped[str] = mapped_column(String(40), index=True)
    provider_kind: Mapped[str] = mapped_column(String(48), index=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    write_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    migration_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class InfrastructureDatabaseConfiguration(Base, TimestampMixin, WorkspaceScopedMixin):
    """Workspace-scoped database endpoint with an encrypted password."""

    __tablename__ = "infrastructure_database_configurations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider_kind",
            name="uq_infrastructure_database_configuration_provider",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_kind: Mapped[str] = mapped_column(String(48), index=True)
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer)
    database_name: Mapped[str] = mapped_column(String(128))
    username: Mapped[str] = mapped_column(String(128))
    ssl_mode: Mapped[str] = mapped_column(String(24), default="prefer")
    password_ciphertext: Mapped[str] = mapped_column(Text)
    password_algorithm: Mapped[str] = mapped_column(
        String(40), default="fernet_sha256_v1"
    )
    key_provider: Mapped[str] = mapped_column(String(32), default="environment")
    key_version: Mapped[int] = mapped_column(Integer, default=1, index=True)
    secret_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="unverified", index=True)
    driver_available: Mapped[bool] = mapped_column(Boolean, default=True)
    connection_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
