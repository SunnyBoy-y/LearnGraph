from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
logger = logging.getLogger(__name__)
# Wait long enough for concurrent request + scheduler writers. The previous
# 5s budget was shorter than multi-second chat/memory commits under load.
SQLITE_BUSY_TIMEOUT_MS = 30_000
SQLITE_BUSY_TIMEOUT_SECONDS = SQLITE_BUSY_TIMEOUT_MS / 1_000

database_url = make_url(settings.database_url)
is_sqlite = database_url.get_backend_name() == "sqlite"
if is_sqlite and database_url.database and database_url.database != ":memory:":
    Path(database_url.database).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={
        "check_same_thread": False,
        # pysqlite lock wait (seconds). Mirrors PRAGMA busy_timeout below.
        "timeout": SQLITE_BUSY_TIMEOUT_SECONDS,
    }
    if is_sqlite
    else {},
)


def _configure_sqlite_connection(dbapi_connection: Any, _: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        if cursor.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("SQLite foreign key enforcement could not be enabled")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")

        database_path = cursor.execute("PRAGMA database_list").fetchone()[2]
        is_network_path = str(database_path).startswith(("\\\\", "//"))
        # WAL is required for concurrent readers/writers on a local desktop DB.
        # The previous 3.51.3 gate kept Python's bundled 3.45.x on rollback
        # journal mode, which surfaces as OperationalError: database is locked
        # under normal multi-request auth/session traffic.
        if database_path and not is_network_path and database_path != ":memory:":
            journal_mode = cursor.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(journal_mode).casefold() != "wal":
                logger.warning(
                    "SQLite WAL mode requested but journal_mode=%s; concurrent writes may lock",
                    journal_mode,
                )
            else:
                cursor.execute("PRAGMA synchronous=NORMAL")
        elif is_network_path:
            logger.warning(
                "SQLite database is on a network path; keeping rollback journal mode"
            )
    finally:
        cursor.close()


if is_sqlite:
    event.listen(engine, "connect", _configure_sqlite_connection)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def init_database() -> None:
    from app.domain import extension_models, migration_models, models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_skill_package_columns()


def ensure_sqlite_session_search_projection(connection: Any) -> None:
    """Create and repair the SQLite FTS5 projection for persisted chat messages.

    ``messages`` and ``chat_sessions`` remain the business fact source.  The
    virtual table is an application-managed projection maintained by triggers;
    startup also backfills rows written before the projection existed.
    """

    try:
        connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS session_messages_fts USING fts5("
            "message_id UNINDEXED, workspace_id UNINDEXED, session_id UNINDEXED, "
            "title, search_terms, raw_content, tokenize='trigram')"
        )
    except Exception:
        connection.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS session_messages_fts USING fts5("
            "message_id UNINDEXED, workspace_id UNINDEXED, session_id UNINDEXED, "
            "title, search_terms, raw_content)"
        )

    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS session_messages_fts_insert
        AFTER INSERT ON messages
        WHEN new.status = 'completed'
        BEGIN
          INSERT INTO session_messages_fts(
            message_id, workspace_id, session_id, title, search_terms, raw_content
          )
          SELECT new.id, new.workspace_id, new.session_id, s.title,
                 coalesce(s.title, '') || ' ' || coalesce(new.role, '') || ' ' ||
                 coalesce(s.goal_id, '') || ' ' || coalesce(s.graph_id, '') || ' ' ||
                 coalesce(new.content, ''),
                 coalesce(new.content, '')
            FROM chat_sessions AS s
           WHERE s.id = new.session_id AND s.workspace_id = new.workspace_id;
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS session_messages_fts_update
        AFTER UPDATE OF content, status, role, session_id, workspace_id ON messages
        BEGIN
          DELETE FROM session_messages_fts WHERE message_id = old.id;
          INSERT INTO session_messages_fts(
            message_id, workspace_id, session_id, title, search_terms, raw_content
          )
          SELECT new.id, new.workspace_id, new.session_id, s.title,
                 coalesce(s.title, '') || ' ' || coalesce(new.role, '') || ' ' ||
                 coalesce(s.goal_id, '') || ' ' || coalesce(s.graph_id, '') || ' ' ||
                 coalesce(new.content, ''),
                 coalesce(new.content, '')
            FROM chat_sessions AS s
           WHERE new.status = 'completed'
             AND s.id = new.session_id
             AND s.workspace_id = new.workspace_id;
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS session_messages_fts_delete
        AFTER DELETE ON messages
        BEGIN
          DELETE FROM session_messages_fts WHERE message_id = old.id;
        END
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TRIGGER IF NOT EXISTS session_messages_fts_session_update
        AFTER UPDATE OF title, goal_id, graph_id ON chat_sessions
        BEGIN
          DELETE FROM session_messages_fts WHERE session_id = old.id;
          INSERT INTO session_messages_fts(
            message_id, workspace_id, session_id, title, search_terms, raw_content
          )
          SELECT m.id, m.workspace_id, m.session_id, new.title,
                 coalesce(new.title, '') || ' ' || coalesce(m.role, '') || ' ' ||
                 coalesce(new.goal_id, '') || ' ' || coalesce(new.graph_id, '') || ' ' ||
                 coalesce(m.content, ''),
                 coalesce(m.content, '')
            FROM messages AS m
           WHERE m.session_id = new.id
             AND m.workspace_id = new.workspace_id
             AND m.status = 'completed';
        END
        """
    )
    connection.exec_driver_sql(
        """
        DELETE FROM session_messages_fts
         WHERE message_id NOT IN (
           SELECT id FROM messages WHERE status = 'completed'
         )
        """
    )
    connection.exec_driver_sql(
        """
        INSERT INTO session_messages_fts(
          message_id, workspace_id, session_id, title, search_terms, raw_content
        )
        SELECT m.id, m.workspace_id, m.session_id, s.title,
               coalesce(s.title, '') || ' ' || coalesce(m.role, '') || ' ' ||
               coalesce(s.goal_id, '') || ' ' || coalesce(s.graph_id, '') || ' ' ||
               coalesce(m.content, ''),
               coalesce(m.content, '')
          FROM messages AS m
          JOIN chat_sessions AS s
            ON s.id = m.session_id AND s.workspace_id = m.workspace_id
         WHERE m.status = 'completed'
           AND NOT EXISTS (
             SELECT 1 FROM session_messages_fts AS f WHERE f.message_id = m.id
           )
        """
    )


def _ensure_sqlite_skill_package_columns() -> None:
    """Additive columns for D-077 skill packages on existing SQLite databases."""

    if not is_sqlite:
        return
    columns = {
        "kind": "VARCHAR(40) DEFAULT 'declarative_review'",
        "package_format": "VARCHAR(40) DEFAULT 'declarative_json'",
        "content_hash": "VARCHAR(64) DEFAULT ''",
        "origin_type": "VARCHAR(40) DEFAULT 'user_import'",
        "origin_ref": "VARCHAR(500) DEFAULT ''",
        "origin_hash": "VARCHAR(64) DEFAULT ''",
        "has_scripts": "BOOLEAN DEFAULT 0",
        "locale_source": "VARCHAR(32) DEFAULT ''",
        "is_official": "BOOLEAN DEFAULT 0",
    }
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(skills)").fetchall()
        }
        if not existing:
            return
        for name, ddl in columns.items():
            if name not in existing:
                connection.exec_driver_sql(f"ALTER TABLE skills ADD COLUMN {name} {ddl}")
    if is_sqlite:
        _apply_sqlite_additive_migrations()
        _verify_sqlite_metadata_shape()


def _apply_sqlite_additive_migrations() -> None:
    """Keep local MVP databases readable when additive fields are introduced.

    This is intentionally limited to nullable/defaulted columns. Destructive
    schema changes stay behind the documented maintenance-window migration API.
    """

    additions = {
        "auth_sessions": {
            "device_id": "VARCHAR(128) NOT NULL DEFAULT ''",
        },
        "workspaces": {
            "organization_id": "VARCHAR(64)",
            "workspace_kind": "VARCHAR(32) NOT NULL DEFAULT 'personal'",
        },
        "chat_sessions": {
            "status": "VARCHAR(32) NOT NULL DEFAULT 'active'",
            "closed_at": "DATETIME",
            "project_id": "VARCHAR(36)",
            "archived_at": "DATETIME",
            "pinned": "BOOLEAN NOT NULL DEFAULT 0",
            "session_kind": "VARCHAR(32) NOT NULL DEFAULT 'main'",
            "writeback_policy": "VARCHAR(32) NOT NULL DEFAULT 'normal'",
            "context_capsule": "JSON NOT NULL DEFAULT '{}'",
            "activity_summary": "VARCHAR(240)",
            "memory_recall_enabled": "BOOLEAN NOT NULL DEFAULT 1",
            "memory_learning_enabled": "BOOLEAN NOT NULL DEFAULT 1",
        },
        "graph_nodes": {
            "external_concept_id": "VARCHAR(255)",
            "target_weight": "INTEGER NOT NULL DEFAULT 50",
        },
        "goals": {
            "target_weight": "INTEGER NOT NULL DEFAULT 50",
            "deadline_at": "DATETIME",
            "availability": "JSON NOT NULL DEFAULT '{}'",
            "preferences": "JSON NOT NULL DEFAULT '{}'",
        },
        "graph_change_sets": {
            "component_part_id": "VARCHAR(36)",
            "confirmed_revision": "INTEGER",
            "result": "JSON NOT NULL DEFAULT '{}'",
            "provider_trace": "JSON NOT NULL DEFAULT '{}'",
            "reviewed_by": "VARCHAR(64)",
            "reviewed_at": "DATETIME",
            "rejection_reason": "TEXT NOT NULL DEFAULT ''",
        },
        "file_text_chunks": {
            "document_revision_id": "VARCHAR(36)",
            "locator_json": "JSON NOT NULL DEFAULT '{}'",
            "section_path": "JSON NOT NULL DEFAULT '[]'",
            "token_count": "INTEGER NOT NULL DEFAULT 0",
        },
        "retrieval_traces": {
            "query_text": "TEXT NOT NULL DEFAULT ''",
        },
        "research_jobs": {
            "estimated_cost_cny": "FLOAT NOT NULL DEFAULT 0.0",
            "actual_cost_cny": "FLOAT NOT NULL DEFAULT 0.0",
            "provider_task_id": "VARCHAR(160)",
            "approval_status": "VARCHAR(40) NOT NULL DEFAULT 'not_required'",
            "source_scope": "JSON NOT NULL DEFAULT '[]'",
            "allowed_domains": "JSON NOT NULL DEFAULT '[]'",
            "error_message": "TEXT",
            "billing_snapshot": "JSON NOT NULL DEFAULT '{}'",
        },
        "action_items": {
            "roadmap_id": "VARCHAR(36)",
            "day_index": "INTEGER NOT NULL DEFAULT 1",
            "duration_minutes": "INTEGER NOT NULL DEFAULT 30",
        },
        "roadmaps": {
            "graph_revision": "INTEGER",
            "planning_snapshot": "JSON NOT NULL DEFAULT '{}'",
        },
        "document_jobs": {
            "execution_token": "VARCHAR(36) NOT NULL DEFAULT ''",
        },
        "mastery_review_jobs": {
            "dedupe_key": "VARCHAR(160)",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "started_at": "DATETIME",
            "completed_at": "DATETIME",
            "last_error": "TEXT NOT NULL DEFAULT ''",
        },
        "mastery_session_states": {
            "pending_node_counts": "JSON NOT NULL DEFAULT '{}'",
            "enqueued_version": "INTEGER NOT NULL DEFAULT 0",
        },
        "mastery_message_activities": {
            "activity_version": "INTEGER NOT NULL DEFAULT 0",
        },
        "context_summaries": {
            "kind": "VARCHAR(24) NOT NULL DEFAULT 'mechanical'",
        },
        "memory_records": {
            "session_id": "VARCHAR(36)",
            "scope_type": "VARCHAR(24) NOT NULL DEFAULT 'workspace'",
            "scope_id": "VARCHAR(64)",
            "goal_id": "VARCHAR(36)",
            "node_id": "VARCHAR(36)",
            "record_kind": "VARCHAR(64) NOT NULL DEFAULT 'semantic_memory'",
            "merge_strategy": "VARCHAR(40) NOT NULL DEFAULT 'UNION'",
            "zone": "VARCHAR(24) NOT NULL DEFAULT 'topics'",
            "state": "VARCHAR(24) NOT NULL DEFAULT 'active'",
            "source_ids": "JSON NOT NULL DEFAULT '[]'",
            "structured_payload": "JSON NOT NULL DEFAULT '{}'",
            # Existing rows predate atomic provenance and remain excluded from
            # the new profile until a migration/review promotes them.
            "atom_schema_version": "INTEGER NOT NULL DEFAULT 0",
            "canonical_key": "VARCHAR(240) NOT NULL DEFAULT ''",
            "atom_kind": "VARCHAR(64) NOT NULL DEFAULT 'fact'",
            "ledger_status": "VARCHAR(32) NOT NULL DEFAULT 'active'",
            "temporal_status": "VARCHAR(32) NOT NULL DEFAULT 'timeless'",
            "summary_eligibility": "VARCHAR(32) NOT NULL DEFAULT 'legacy_review'",
            "valid_from": "DATETIME",
            "valid_until": "DATETIME",
            "event_at": "DATETIME",
            "next_review_at": "DATETIME",
            "last_verified_at": "DATETIME",
            "timezone_name": "VARCHAR(80) NOT NULL DEFAULT 'Asia/Shanghai'",
            "evidence_ids": "JSON NOT NULL DEFAULT '[]'",
            "confidence": "FLOAT NOT NULL DEFAULT 0.7",
            "importance": "FLOAT NOT NULL DEFAULT 0.5",
            "strength": "FLOAT NOT NULL DEFAULT 0.5",
            "access_count": "INTEGER NOT NULL DEFAULT 0",
            "confirmation_count": "INTEGER NOT NULL DEFAULT 0",
            "successful_use_count": "INTEGER NOT NULL DEFAULT 0",
            "last_accessed_at": "DATETIME",
            "resolution_status": "VARCHAR(40) NOT NULL DEFAULT 'none'",
            "decay_policy": "VARCHAR(40) NOT NULL DEFAULT 'SLOW'",
            "supersedes_id": "VARCHAR(64)",
            "provider_id": "VARCHAR(80) NOT NULL DEFAULT 'local_workspace_markdown'",
            "provider_binding_id": "VARCHAR(160)",
            "deleted_at": "DATETIME",
            "recoverable_until": "DATETIME",
            "content_destroyed_at": "DATETIME",
        },
        "provider_secrets": {
            "algorithm": "VARCHAR(40) NOT NULL DEFAULT 'fernet_sha256_v1'",
            "key_provider": "VARCHAR(32) NOT NULL DEFAULT 'environment'",
            "key_version": "INTEGER NOT NULL DEFAULT 1",
            "secret_version": "INTEGER NOT NULL DEFAULT 1",
            "rotated_at": "DATETIME",
            "revoked_at": "DATETIME",
            "revoked_by": "VARCHAR(64)",
        },
        "usage_events": {
            "cost_cny": "FLOAT NOT NULL DEFAULT 0.0",
            "cost_status": "VARCHAR(32) NOT NULL DEFAULT 'unpriced'",
            "price_version_id": "VARCHAR(36)",
            "exchange_rate_version_id": "VARCHAR(36)",
            "input_usd_per_million": "FLOAT NOT NULL DEFAULT 0.0",
            "output_usd_per_million": "FLOAT NOT NULL DEFAULT 0.0",
            "fixed_usd_per_call": "FLOAT NOT NULL DEFAULT 0.0",
            "usd_cny_rate": "FLOAT NOT NULL DEFAULT 0.0",
            "cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cache_creation_input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "reasoning_tokens": "INTEGER NOT NULL DEFAULT 0",
            "total_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cached_input_usd_per_million": "FLOAT NOT NULL DEFAULT 0.0",
            "cache_write_usd_per_million": "FLOAT NOT NULL DEFAULT 0.0",
            "price_multiplier": "FLOAT NOT NULL DEFAULT 1.0",
        },
        "budget_policies": {
            "limit_currency": "VARCHAR(8) NOT NULL DEFAULT 'CNY'",
        },
        "price_versions": {
            "cached_input_usd_per_million": "FLOAT",
            "cache_write_usd_per_million": "FLOAT",
            "conditions": "JSON NOT NULL DEFAULT '{}'",
        },
        "graph_nodes": {
            "teaching_strategy": "TEXT NOT NULL DEFAULT ''",
        },
        "exercises": {
            "difficulty": "VARCHAR(20) NOT NULL DEFAULT 'medium'",
            "generation_batch_id": "VARCHAR(36)",
            "source_refs": "JSON NOT NULL DEFAULT '[]'",
            "rubric_json": "JSON NOT NULL DEFAULT '{}'",
            "metadata_json": "JSON NOT NULL DEFAULT '{}'",
        },
        "answer_records": {
            "actor_id": "VARCHAR(64)",
        },
        "sandbox_sessions": {
            "policy_revision": "VARCHAR(40) NOT NULL DEFAULT 'sandbox-policy-v1'",
            "runtime_kind": "VARCHAR(40) NOT NULL DEFAULT 'python-node'",
            "lifecycle_state": "VARCHAR(32) NOT NULL DEFAULT 'CREATED'",
            "workspace_relative_path": "VARCHAR(255) NOT NULL DEFAULT ''",
            "runtime_started_at": "DATETIME",
            "runtime_last_used_at": "DATETIME",
            "workspace_expires_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
            "absolute_expires_at": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
        },
    }
    with engine.begin() as connection:
        for table_name, columns in additions.items():
            existing = {
                str(row[1])
                for row in connection.exec_driver_sql(f"PRAGMA table_info({table_name})")
            }
            for column_name, definition in columns.items():
                if column_name not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
                    )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_mastery_review_job_dedupe "
            "ON mastery_review_jobs(workspace_id, dedupe_key) "
            "WHERE dedupe_key IS NOT NULL"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_usage_events_price_version_id "
            "ON usage_events(price_version_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_usage_events_exchange_rate_version_id "
            "ON usage_events(exchange_rate_version_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_provider_secrets_key_version "
            "ON provider_secrets(key_version)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_provider_secrets_revoked_at "
            "ON provider_secrets(revoked_at)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_records_state "
            "ON memory_records(workspace_id, state)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_records_session_id "
            "ON memory_records(workspace_id, session_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_records_scope "
            "ON memory_records(workspace_id, scope_type, scope_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_records_goal_id "
            "ON memory_records(workspace_id, goal_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_records_node_id "
            "ON memory_records(workspace_id, node_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_records_profile_effective "
            "ON memory_records(workspace_id, state, ledger_status, "
            "summary_eligibility, temporal_status)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_records_next_review "
            "ON memory_records(workspace_id, next_review_at)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_evidence_source "
            "ON memory_evidence(workspace_id, source_kind, source_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_profile_current "
            "ON memory_profile_snapshots(workspace_id, owner_subject_id, status, version)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_drafts_status "
            "ON memory_drafts(workspace_id, status)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_file_text_chunks_document_revision_id "
            "ON file_text_chunks(document_revision_id)"
        )
        # This is an application-managed projection rather than a second fact
        # source. Trigram tokenization supports both CJK substring search and
        # identifiers; old SQLite builds fall back to unicode tokenization.
        try:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5("
                "chunk_id UNINDEXED, workspace_id UNINDEXED, file_id UNINDEXED, "
                "content, tokenize='trigram')"
            )
        except Exception:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5("
                "chunk_id UNINDEXED, workspace_id UNINDEXED, file_id UNINDEXED, content)"
            )
        ensure_sqlite_session_search_projection(connection)
        # Before versioned billing, the only non-zero stored cost came from
        # Deep Research's explicit CNY amount divided by the agreed 6.77 rate.
        # Preserve that historical meaning once, rather than presenting those
        # rows as newly priced or silently leaving their CNY total at zero.
        connection.exec_driver_sql(
            "UPDATE usage_events "
            "SET cost_cny = cost_usd * 6.77, "
            "usd_cny_rate = 6.77, cost_status = 'legacy_snapshot' "
            "WHERE cost_usd > 0 AND cost_cny = 0 "
            "AND cost_status = 'unpriced'"
        )
        connection.exec_driver_sql(
            "UPDATE usage_events SET cost_status = 'non_billable' "
            "WHERE provider_id = 'local_mock' AND cost_status = 'unpriced'"
        )


def _verify_sqlite_metadata_shape() -> None:
    """Fail startup when ORM columns are absent from a persisted SQLite schema."""

    missing: list[str] = []
    with engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            actual = {
                str(row[1])
                for row in connection.exec_driver_sql(
                    f'PRAGMA table_info("{table.name}")'
                )
            }
            for column in table.columns:
                if column.name not in actual:
                    missing.append(f"{table.name}.{column.name}")
    if missing:
        raise RuntimeError(
            "SQLite schema is missing mapped columns after additive migrations: "
            + ", ".join(sorted(missing))
        )
