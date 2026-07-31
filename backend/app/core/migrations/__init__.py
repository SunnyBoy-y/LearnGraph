from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.domain.memory_event_models import SchemaRevision, utc_now


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
    try:
        connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search_fts USING fts5("
            "document_id UNINDEXED, tenant_id UNINDEXED, workspace_id UNINDEXED, "
            "subject, content, keywords, memory_type, entity_aliases, tokenize='trigram')"
        )
    except Exception:
        connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search_fts USING fts5("
            "document_id UNINDEXED, tenant_id UNINDEXED, workspace_id UNINDEXED, "
            "subject, content, keywords, memory_type, entity_aliases)"
        )


MIGRATIONS = (
    SchemaMigration("0001_memory_foundation", "Create event-store FTS projection", _memory_foundation),
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
