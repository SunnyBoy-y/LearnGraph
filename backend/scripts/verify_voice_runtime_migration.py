"""Offline validation of the additive P1-A voice schema migration."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from app.core.migrations import _voice_runtime_owner_and_playback


def main() -> int:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        # A compact representation of a pre-P1-A database. The migration must
        # work without requiring the full application schema or a data rewrite.
        connection.exec_driver_sql(
            "CREATE TABLE voice_sessions ("
            "id VARCHAR(64) PRIMARY KEY, workspace_id VARCHAR(64), "
            "tenant_id VARCHAR(64), owner_user_id VARCHAR(64), status VARCHAR(24), "
            "runtime_owner_id VARCHAR(160))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE voice_speech_deliveries ("
            "id VARCHAR(64) PRIMARY KEY, workspace_id VARCHAR(64), "
            "tenant_id VARCHAR(64), voice_session_id VARCHAR(64), "
            "result_id VARCHAR(64), subagent_id VARCHAR(120), "
            "requirement_version INTEGER, result_version INTEGER, "
            "idempotency_key VARCHAR(160), speech_id VARCHAR(64), "
            "status VARCHAR(24), claimed_at DATETIME, completed_at DATETIME, "
            "interrupted_at DATETIME, failed_at DATETIME, playback_cursor_ms INTEGER, "
            "error_code VARCHAR(80), snapshot JSON)"
        )
        _voice_runtime_owner_and_playback(connection)
        _voice_runtime_owner_and_playback(connection)

        session_columns = {
            column["name"] for column in inspect(connection).get_columns("voice_sessions")
        }
        delivery_columns = {
            column["name"]
            for column in inspect(connection).get_columns("voice_speech_deliveries")
        }
        assert {"runtime_owner_heartbeat_at", "runtime_owner_lease_until"}.issubset(
            session_columns
        )
        assert {"playback_status", "playback_ended_at"}.issubset(delivery_columns)
    engine.dispose()
    print("PASS: P1-A voice migration is additive and idempotent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

