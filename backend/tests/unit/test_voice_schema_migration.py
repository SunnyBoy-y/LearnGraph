from sqlalchemy import create_engine

from app.core.migrations import _voice_runtime_owner_and_playback
from app.domain.models import VoiceTaskLinkRecord


def test_voice_runtime_migration_adds_task_approval_columns_to_existing_rows() -> None:
    engine = create_engine("sqlite:///:memory:")
    added_columns = {
        "capability_id",
        "approval_status",
        "approval_request_json",
        "approval_expires_at",
    }

    with engine.begin() as connection:
        legacy_columns = [
            f'"{column.name}" {column.type}'
            for column in VoiceTaskLinkRecord.__table__.columns
            if column.name not in added_columns
        ]
        connection.exec_driver_sql(
            f'CREATE TABLE "voice_task_links" ({", ".join(legacy_columns)})'
        )
        connection.exec_driver_sql(
            'INSERT INTO "voice_task_links" ("id") VALUES ("legacy")'
        )

        _voice_runtime_owner_and_playback(connection)
        _voice_runtime_owner_and_playback(connection)

        columns = {
            row[1]: (row[3], row[4])
            for row in connection.exec_driver_sql(
                'PRAGMA table_info("voice_task_links")'
            ).fetchall()
        }
        assert columns["capability_id"] == (0, None)
        assert columns["approval_status"] == (1, "'none'")
        assert columns["approval_request_json"] == (1, "'{}'")
        assert columns["approval_expires_at"] == (0, None)

    engine.dispose()
