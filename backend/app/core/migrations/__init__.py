from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.migration_models import SchemaRevision
from app.domain.models import SubAppInteractionEvent, utc_now


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    revision: str
    description: str
    apply: Any

    @property
    def checksum(self) -> str:
        source = f"{self.revision}\0{self.description}".encode("utf-8")
        return hashlib.sha256(source).hexdigest()


def _memory_foundation(connection: Connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    capability = "unicode"
    try:
        connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search_fts USING fts5("
            "document_id UNINDEXED, tenant_id UNINDEXED, workspace_id UNINDEXED, "
            "subject, content, keywords, memory_type, entity_aliases, tokenize='trigram')"
        )
        capability = "trigram"
    except Exception:
        connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search_fts USING fts5("
            "document_id UNINDEXED, tenant_id UNINDEXED, workspace_id UNINDEXED, "
            "subject, content, keywords, memory_type, entity_aliases)"
        )
        capability = "unicode"
    # Record tokenizer capability for operators; last_error column is reused as a
    # small status slot for this non-positional projector marker.
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS memory_projection_checkpoints ("
        "projector_name VARCHAR(120) NOT NULL PRIMARY KEY, "
        "projection_version INTEGER NOT NULL DEFAULT 1, "
        "last_global_position INTEGER NOT NULL DEFAULT 0, "
        "lease_owner VARCHAR(120), "
        "lease_until DATETIME, "
        "last_error TEXT NOT NULL DEFAULT '', "
        "updated_at DATETIME NOT NULL)"
    )
    # capability is a controlled token (trigram|unicode), never user input.
    connection.exec_driver_sql(
        "INSERT OR REPLACE INTO memory_projection_checkpoints("
        "projector_name, projection_version, last_global_position, last_error, updated_at) "
        f"VALUES ('memory_search_fts_capability', 1, 0, '{capability}', CURRENT_TIMESTAMP)"
    )


def _memory_outbox_lease_generation(connection: Connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    columns = {
        column["name"]
        for column in inspect(connection).get_columns("memory_projection_outbox")
    }
    if "lease_generation" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE memory_projection_outbox "
            "ADD COLUMN lease_generation INTEGER NOT NULL DEFAULT 0"
        )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_memory_outbox_claim "
        "ON memory_projection_outbox (status, available_at, lease_until)"
    )


def _record_ledger_baseline(connection: Connection) -> None:
    """No-op: the v1.0.0 row this migration inserts is the ledger baseline.

    Both fresh and migration-era databases end with this as the latest
    revision, matching CURRENT_SCHEMA_REVISION in app.core.database, so the
    startup revision check no longer warns on every boot.
    """
    del connection


def _sandbox_backend_resource_ref(connection: Connection) -> None:
    """Additive columns that let existing sessions route to their owning backend.

    New sessions record the backend that created them (``docker`` today,
    ``sandboxd`` after the control-plane migration). Existing rows keep their
    legacy ``backend_session_ref`` untouched; the new columns are nullable (or
    defaulted) so no data rewrite is required.
    """
    sandbox_columns = {
        column["name"] for column in inspect(connection).get_columns("sandbox_sessions")
    }
    if "backend_resource_ref" not in sandbox_columns:
        connection.exec_driver_sql(
            "ALTER TABLE sandbox_sessions ADD COLUMN backend_resource_ref VARCHAR(255)"
        )
    if "backend_protocol_version" not in sandbox_columns:
        connection.exec_driver_sql(
            "ALTER TABLE sandbox_sessions ADD COLUMN backend_protocol_version VARCHAR(40)"
        )
    mcp_columns = {
        column["name"] for column in inspect(connection).get_columns("mcp_runner_sessions")
    }
    if "backend_id" not in mcp_columns:
        connection.exec_driver_sql(
            "ALTER TABLE mcp_runner_sessions "
            "ADD COLUMN backend_id VARCHAR(80) NOT NULL DEFAULT 'docker'"
        )


def _subapp_interaction_events(connection: Connection) -> None:
    """Create the additive sub-application interaction event ledger."""

    SubAppInteractionEvent.__table__.create(bind=connection, checkfirst=True)


def _sandbox_execution_pool(connection: Connection) -> None:
    """Create execution-pool tables (instance/workspace/job/reservation).

    These are additive; existing sandbox_sessions/tasks/commands remain the
    compatible runtime view during the migration window.
    """
    from app.domain.models import (
        SandboxInstance,
        SandboxJob,
        SandboxReservation,
        SandboxWorkspace,
    )

    for table in (
        SandboxInstance.__table__,
        SandboxWorkspace.__table__,
        SandboxJob.__table__,
        SandboxReservation.__table__,
    ):
        table.create(bind=connection, checkfirst=True)
    job_columns = {
        column["name"] for column in inspect(connection).get_columns("sandbox_jobs")
    }
    if "payload_json" not in job_columns:
        connection.exec_driver_sql(
            "ALTER TABLE sandbox_jobs ADD COLUMN payload_json JSON NOT NULL DEFAULT '{}'"
        )
    execution_columns = {
        column["name"] for column in inspect(connection).get_columns("sandbox_executions")
    }
    for column, ddl in (
        ("job_id", "ALTER TABLE sandbox_executions ADD COLUMN job_id VARCHAR(36)"),
        ("instance_id", "ALTER TABLE sandbox_executions ADD COLUMN instance_id VARCHAR(36)"),
        ("reservation_id", "ALTER TABLE sandbox_executions ADD COLUMN reservation_id VARCHAR(36)"),
        ("workspace_key", "ALTER TABLE sandbox_executions ADD COLUMN workspace_key VARCHAR(120)"),
        ("cancel_requested_at", "ALTER TABLE sandbox_executions ADD COLUMN cancel_requested_at DATETIME"),
        ("finished_reason", "ALTER TABLE sandbox_executions ADD COLUMN finished_reason VARCHAR(40)"),
    ):
        if column not in execution_columns:
            connection.exec_driver_sql(ddl)
    command_columns = {
        column["name"] for column in inspect(connection).get_columns("sandbox_agent_commands")
    }
    if "job_id" not in command_columns:
        connection.exec_driver_sql(
            "ALTER TABLE sandbox_agent_commands ADD COLUMN job_id VARCHAR(36)"
        )
    if "instance_id" not in command_columns:
        connection.exec_driver_sql(
            "ALTER TABLE sandbox_agent_commands ADD COLUMN instance_id VARCHAR(36)"
        )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_sandbox_executions_job ON sandbox_executions (job_id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_sandbox_agent_commands_job ON sandbox_agent_commands (job_id)"
    )


def _sandbox_toolkit_tables(connection: Connection) -> None:
    """Create sandbox_todos / sandbox_kernels for the sandbox toolkit tools."""

    from app.domain.models import SandboxKernel, SandboxTodo

    SandboxTodo.__table__.create(bind=connection, checkfirst=True)
    SandboxKernel.__table__.create(bind=connection, checkfirst=True)


MIGRATIONS = (
    SchemaMigration("0001_memory_foundation", "Create event-store FTS projection", _memory_foundation),
    SchemaMigration(
        "0002_memory_outbox_lease_generation",
        "Add generation-aware outbox lease ownership",
        _memory_outbox_lease_generation,
    ),
    SchemaMigration(
        "0003_subapp_interaction_events",
        "Create sub-application interaction event ledger",
        _subapp_interaction_events,
    ),
    # Keep revision/description in sync with CURRENT_SCHEMA_REVISION /
    # CURRENT_SCHEMA_DESCRIPTION in app.core.database.
    SchemaMigration(
        "v1.0.0",
        "Schema Revision Ledger baseline — all tables up to P0 sandbox hardening",
        _record_ledger_baseline,
    ),
    SchemaMigration(
        "v1.1.0",
        "Additive sandbox backend routing columns for mixed-backend drain",
        _sandbox_backend_resource_ref,
    ),
    SchemaMigration(
        "v1.2.0",
        "Execution pool: sandbox instances/workspaces/jobs/reservations",
        _sandbox_execution_pool,
    ),
    SchemaMigration(
        "v1.3.0",
        "Sandbox toolkit: sandbox_todos and sandbox_kernels tables",
        _sandbox_toolkit_tables,
    ),
)


def apply_schema_migrations(connection: Connection) -> None:
    """Apply ordered, checksum-verified migrations after ORM tables exist."""

    if "schema_revisions" not in inspect(connection).get_table_names():
        SchemaRevision.__table__.create(bind=connection, checkfirst=True)
    rows = {
        row[0]: row[1]
        for row in connection.exec_driver_sql(
            "SELECT revision, checksum FROM schema_revisions"
        ).fetchall()
    }
    for migration in MIGRATIONS:
        prior = rows.get(migration.revision)
        if prior is not None:
            if prior != migration.checksum:
                raise RuntimeError(
                    f"Schema migration checksum mismatch: {migration.revision}"
                )
            continue
        migration.apply(connection)
        connection.execute(
            SchemaRevision.__table__.insert().values(
                revision=migration.revision,
                checksum=migration.checksum,
                description=migration.description,
                applied_at=utc_now(),
            )
        )
