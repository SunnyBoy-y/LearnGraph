"""Additive migration for durable voice result reliability and isolation."""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateColumn

from app.domain.models import (
    VoiceEventRecord,
    VoiceResultInboxRecord,
    VoiceSessionRecord,
    VoiceSpeechDeliveryRecord,
    VoiceTaskLinkRecord,
    VoiceTurnRecord,
)


def ensure_voice_reliability_columns(connection: Connection) -> None:
    """Add new voice columns in place and backfill tenant scope for legacy rows."""
    for table in (
        VoiceSessionRecord.__table__,
        VoiceTurnRecord.__table__,
        VoiceEventRecord.__table__,
        VoiceTaskLinkRecord.__table__,
    ):
        if not inspect(connection).has_table(table.name):
            continue
        existing = {
            str(column["name"])
            for column in inspect(connection).get_columns(table.name)
        }
        for column in table.columns:
            if column.name in existing:
                continue
            ddl = str(CreateColumn(column).compile(dialect=connection.dialect))
            if (
                not column.nullable
                and column.server_default is None
                and " DEFAULT " not in ddl.upper()
            ):
                # SQLite cannot add a NOT NULL column without a value for
                # existing rows. Mirror the model default with a database-side
                # value so legacy rows remain valid.
                type_name = column.type.__class__.__name__.casefold()
                default_sql = (
                    "1"
                    if "boolean" in type_name
                    else "0"
                    if any(token in type_name for token in ("integer", "float", "numeric"))
                    else "'[]'"
                    if "json" in type_name
                    else "''"
                )
                ddl = f"{ddl} DEFAULT {default_sql}"
            connection.exec_driver_sql(
                f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'
            )

    for table_name in (
        "voice_sessions",
        "voice_turns",
        "voice_events",
        "voice_task_links",
    ):
        if inspect(connection).has_table(table_name):
            connection.exec_driver_sql(
                f"UPDATE {table_name} SET tenant_id = COALESCE("
                "(SELECT tenant_id FROM workspaces "
                f"WHERE workspaces.id = {table_name}.workspace_id), tenant_id)"
            )


def apply_voice_reliability_migration(connection: Connection) -> None:
    """Create inbox/delivery tables after extending the session foundation."""
    ensure_voice_reliability_columns(connection)
    VoiceResultInboxRecord.__table__.create(bind=connection, checkfirst=True)
    VoiceSpeechDeliveryRecord.__table__.create(bind=connection, checkfirst=True)
