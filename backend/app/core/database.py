from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from collections.abc import Generator
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
logger = logging.getLogger(__name__)
# Busy timeout for the single SQLite write lock, sourced from settings
# (LEARNGRAPH_SQLITE_BUSY_TIMEOUT_MS). Default 10s: bounds interactive-request
# stalls while staying above the old 5s budget; the retry helpers and the
# B1-7 sweep mutex absorb the residual contention.
SQLITE_BUSY_TIMEOUT_MS = settings.sqlite_busy_timeout_ms
SQLITE_BUSY_TIMEOUT_SECONDS = SQLITE_BUSY_TIMEOUT_MS / 1_000

database_url = make_url(settings.database_url)
is_sqlite = database_url.get_backend_name() == "sqlite"
if is_sqlite and database_url.database and database_url.database != ":memory:":
    Path(database_url.database).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={
        "check_same_thread": False,
        # pysqlite lock wait (seconds). Mirrors PRAGMA busy_timeout below;
        # both come from settings.sqlite_busy_timeout_ms.
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


def _is_sqlite_locked_error(exc: OperationalError) -> bool:
    """True when the OperationalError is SQLite write-lock contention."""

    message = str(getattr(exc, "orig", exc))
    return "database is locked" in message or "database table is locked" in message


def run_wal_checkpoint(*, truncate: bool = True) -> dict[str, Any]:
    """Best-effort SQLite WAL checkpoint; never blocks on a busy database.

    A huge un-checkpointed WAL turns the first write commit past the
    autocheckpoint threshold into a long-lived holder of the single SQLite
    write lock (it copies many MB back into the main file), which can starve
    concurrent message streams with ``database is locked``.  The periodic
    maintenance loop (``scheduler.wal_checkpoint_scheduler``) keeps the WAL
    small so autocheckpoint stays cheap.  When another writer is active SQLite
    returns ``busy=1`` immediately instead of waiting, and we simply skip the
    round.  ``TRUNCATE`` also returns the checkpointed frames to the OS; a
    never-shrinking WAL is exactly the failure mode we are preventing.
    """

    if not is_sqlite:
        return {"ok": False, "skipped": True, "reason": "not_sqlite"}
    try:
        with engine.connect() as conn:
            mode = "TRUNCATE" if truncate else "PASSIVE"
            busy, log_frames, checkpointed_frames = conn.execute(
                text(f"PRAGMA wal_checkpoint({mode})")
            ).fetchone()
            return {
                "ok": bool(busy == 0),
                "busy": int(busy),
                "log_frames": int(log_frames),
                "checkpointed_frames": int(checkpointed_frames),
            }
    except Exception as exc:  # defensive: maintenance must never crash
        return {
            "ok": False,
            "busy": 1,
            "log_frames": 0,
            "checkpointed_frames": 0,
            "error": str(exc)[:160],
        }


T = TypeVar("T")


def retry_sqlite_locked(
    db: Session,
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay_seconds: float = 0.15,
) -> T:
    """Re-run ``fn`` when SQLite write-lock contention interrupted it.

    The engine's busy timeout already waits ``SQLITE_BUSY_TIMEOUT_MS`` before
    raising; a lock error therefore means another writer (a parallel request or
    an embedded scheduler sweep) held the single SQLite write lock for the
    whole wait. ``fn`` must be idempotent and start from a fresh transaction:
    the session is rolled back before every retry so the retried callable
    re-reads its inputs and re-applies its writes.
    """

    last_error: OperationalError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except OperationalError as exc:
            if not _is_sqlite_locked_error(exc):
                raise
            last_error = exc
            try:
                db.rollback()
            except Exception:  # pragma: no cover - defensive
                pass
            if attempt >= attempts:
                break
            time.sleep(base_delay_seconds * (2 ** (attempt - 1)))
    assert last_error is not None
    logger.warning(
        "SQLite write still locked after %d attempts: %s",
        attempts,
        str(getattr(last_error, "orig", last_error))[:200],
    )
    raise last_error


def commit_with_locked_retry(
    db: Session,
    *,
    redo: Callable[[], None] | None = None,
    attempts: int = 5,
    base_delay_seconds: float = 0.2,
) -> None:
    """Commit, retrying transient SQLite ``database is locked`` contention.

    The engine already waits ``SQLITE_BUSY_TIMEOUT`` before raising; a lock
    error therefore means another writer held the single SQLite write lock for
    the whole wait (for example a background sweep that kept a dirty ORM
    session across a long model call). Background sweeps should retry briefly
    instead of dropping a sweep.  ``redo`` re-applies the pending ORM changes
    after the mandatory rollback so the retried commit writes the same values.
    """

    for attempt in range(1, attempts + 1):
        try:
            db.commit()
            return
        except OperationalError as exc:
            if not _is_sqlite_locked_error(exc):
                raise
            try:
                db.rollback()
            except Exception:  # pragma: no cover - defensive
                pass
            if attempt >= attempts:
                logger.warning(
                    "SQLite write still locked after %d attempts: %s",
                    attempts,
                    str(getattr(exc, "orig", exc))[:200],
                )
                raise
            if redo is not None:
                try:
                    redo()
                except Exception:  # pragma: no cover - the retry would be futile
                    logger.warning("redo of a locked SQLite write failed", exc_info=True)
                    raise
            time.sleep(base_delay_seconds * (2 ** (attempt - 1)))
    raise OperationalError("commit retries exhausted", {}, None)  # pragma: no cover


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def init_database() -> None:
    from app.domain import (  # noqa: F401
        extension_models,
        memory_event_models,
        migration_models,
        models,
    )
    from app.core.migrations import apply_schema_migrations

    Base.metadata.create_all(bind=engine)
    _apply_sqlite_subapp_persistence_migration()
    with engine.begin() as connection:
        apply_schema_migrations(connection)
    _ensure_sqlite_skill_package_columns()
    _verify_schema_revisions()


def _apply_sqlite_subapp_persistence_migration() -> None:
    """T2.3 additive tables; never rebuild existing SQLite tables or foreign keys."""

    if not is_sqlite:
        return

    # ``create(checkfirst=True)`` makes this safe for already-initialized local
    # databases while keeping this migration independent from the legacy
    # additive-column ledger below.  In particular, do not rebuild the existing
    # ``subapp_interaction_events`` table merely to add a foreign key.
    with engine.begin() as connection:
        for table_name in (
            "subapp_sessions",
            "subapp_states",
            "subapp_bundle_validations",
            "subapp_bundles",
            "subapp_bundle_files",
            "subapp_bundle_preview_grants",
            "subapp_agent_runs",
            "subapp_agent_consent_requests",
        ):
            Base.metadata.tables[table_name].create(connection, checkfirst=True)


# Current schema revision identifier.  Bump this whenever an additive or
# destructive migration is applied (via _apply_migration) so the startup
# check catches stale databases before they cause data integrity issues.
CURRENT_SCHEMA_REVISION = "v1.1.0"
CURRENT_SCHEMA_DESCRIPTION = "Additive sandbox backend routing columns for mixed-backend drain"


def _compute_schema_checksum() -> str:
    """Hash of all table/schema metadata for drift detection."""
    from app.domain import extension_models, migration_models, models  # noqa: F401

    h = hashlib.sha256()
    for table_name in sorted(Base.metadata.tables):
        table = Base.metadata.tables[table_name]
        h.update(table_name.encode())
        for column in table.columns:
            h.update(f"{column.name}:{column.type!s}".encode())
    return h.hexdigest()[:32]


def _verify_schema_revisions() -> None:
    """Verify the database schema revision matches the code's expected revision.

    On a fresh database the initial revision is inserted automatically.  On an
    existing database the revision must match, otherwise the application refuses
    to start so the operator knows a migration is needed.
    """
    from app.domain.migration_models import SchemaRevision

    with SessionLocal() as session:
        row = (
            session.query(SchemaRevision)
            .order_by(SchemaRevision.applied_at.desc())
            .first()
        )
        if row is None:
            # Fresh database — insert the initial revision.
            info = SchemaRevision(
                revision=CURRENT_SCHEMA_REVISION,
                description=CURRENT_SCHEMA_DESCRIPTION,
                checksum=_compute_schema_checksum(),
                applied_by="init_database",
                duration_ms=0,
            )
            session.add(info)
            session.commit()
            logger.info(
                "Schema revision initialized: %s — %s",
                CURRENT_SCHEMA_REVISION,
                CURRENT_SCHEMA_DESCRIPTION,
            )
        elif row.revision != CURRENT_SCHEMA_REVISION:
            logger.warning(
                "Database schema revision is %s but code expects %s (%s). "
                "Run the required migration before starting the application.",
                row.revision,
                CURRENT_SCHEMA_REVISION,
                CURRENT_SCHEMA_DESCRIPTION,
            )


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
          -- B1-9: update only the title-derived columns of the session's
          -- existing FTS rows instead of deleting and re-inserting the whole
          -- session (write amplification on every auto-title change).
          UPDATE session_messages_fts
             SET title = new.title,
                 search_terms = coalesce(new.title, '') || ' ' ||
                                coalesce(m.role, '') || ' ' ||
                                coalesce(new.goal_id, '') || ' ' ||
                                coalesce(new.graph_id, '') || ' ' ||
                                coalesce(m.content, ''),
                 raw_content = coalesce(m.content, '')
            FROM messages AS m
           WHERE session_messages_fts.session_id = new.id
             AND session_messages_fts.workspace_id = new.workspace_id
             AND session_messages_fts.message_id = m.id
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
        "last_used_at": "DATETIME",
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
            "subapp_agent_consent": "VARCHAR(16) NOT NULL DEFAULT 'ask'",
        },
        "chat_sessions": {
            "idempotency_key_hash": "VARCHAR(64)",
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
            "teaching_strategy": "TEXT NOT NULL DEFAULT ''",
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
            "lifecycle_status": "VARCHAR(24) NOT NULL DEFAULT 'active'",
        },
        "image_description_cache": {
            "image_sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
            "provider_id": "VARCHAR(36) NOT NULL DEFAULT ''",
            "model_id": "VARCHAR(160) NOT NULL DEFAULT ''",
            "media_kind": "VARCHAR(16) NOT NULL DEFAULT 'image'",
            "prompt_version": "VARCHAR(40) NOT NULL DEFAULT 'v1'",
            "status": "VARCHAR(24) NOT NULL DEFAULT 'completed'",
            "description": "TEXT NOT NULL DEFAULT ''",
            "error_message": "VARCHAR(500) NOT NULL DEFAULT ''",
        },
        "files": {
            "logical_version": "INTEGER NOT NULL DEFAULT 0",
            "source": "VARCHAR(40) NOT NULL DEFAULT 'upload'",
            "created_by": "VARCHAR(64)",
            "updated_by": "VARCHAR(64)",
            "lifecycle_status": "VARCHAR(24) NOT NULL DEFAULT 'active'",
        },
        "document_revisions": {
            "supersedes_revision_id": "VARCHAR(36)",
            "activated_at": "DATETIME",
            "index_status": "VARCHAR(24) NOT NULL DEFAULT 'pending'",
            "embedding_status": "VARCHAR(24) NOT NULL DEFAULT 'pending'",
            "lifecycle_status": "VARCHAR(24) NOT NULL DEFAULT 'active'",
        },
        "evidence": {
            "result": "VARCHAR(32) NOT NULL DEFAULT 'observed'",
            "difficulty": "FLOAT NOT NULL DEFAULT 0.5",
            "assistance_level": "FLOAT NOT NULL DEFAULT 0.0",
            "score": "FLOAT",
            "source_ref": "VARCHAR(200) NOT NULL DEFAULT ''",
            "source_version_id": "VARCHAR(64)",
            "source_content_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
            "validity_status": "VARCHAR(24) NOT NULL DEFAULT 'active'",
            "invalidated_at": "DATETIME",
            "invalidation_event_id": "VARCHAR(64)",
        },
        "retrieval_traces": {
            "query_text": "TEXT NOT NULL DEFAULT ''",
        },
        "research_jobs": {
            "created_by": "VARCHAR(64)",
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
            "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'local-tenant'",
            "subject_user_id": "VARCHAR(64)",
            "audience_type": "VARCHAR(24) NOT NULL DEFAULT 'workspace'",
            "task_id": "VARCHAR(64)",
            "project_id": "VARCHAR(64)",
            "conversation_id": "VARCHAR(64)",
            "file_id": "VARCHAR(64)",
            "memory_layer": "VARCHAR(16) NOT NULL DEFAULT 'L4'",
            "assertion_type": "VARCHAR(32) NOT NULL DEFAULT 'explicit'",
            "sensitivity": "VARCHAR(24) NOT NULL DEFAULT 'normal'",
            "lifecycle_status": "VARCHAR(32) NOT NULL DEFAULT 'active'",
            "superseded_by_id": "VARCHAR(64)",
            "head_event_id": "VARCHAR(64)",
            "projection_version": "INTEGER NOT NULL DEFAULT 1",
            "auto_recall_suppressed": "BOOLEAN NOT NULL DEFAULT 0",
            "child_agent_denied": "BOOLEAN NOT NULL DEFAULT 0",
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
        "sandbox_destructive_grants": {
            "command_intent_digest": "VARCHAR(64)",
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
            "lease_token_hash": "VARCHAR(64)",
            "lease_expires_at": "DATETIME",
            "heartbeat_at": "DATETIME",
            "active_command_id": "VARCHAR(36)",
            "command_generation": "INTEGER NOT NULL DEFAULT 0",
        },
        # P2-A trusted component chain: issuer binding and trust eligibility.
        "component_manifest_versions": {
            "issuer_id": "VARCHAR(36)",
            "trusted_bundle_eligible": "BOOLEAN NOT NULL DEFAULT 0",
            # T2.2 optional subapp interaction contract (event/state schemas).
            "interaction_contract": "JSON",
        },
        # P2-B reviewed stdio launch specification on existing MCP servers.
        "mcp_servers": {
            "runner_image_digest": "VARCHAR(100)",
            "launch_command": "JSON NOT NULL DEFAULT '[]'",
            "launch_spec_hash": "VARCHAR(64)",
            "launch_status": "VARCHAR(40) NOT NULL DEFAULT 'unapproved'",
            "launch_approved_by": "VARCHAR(64)",
            "launch_approved_at": "DATETIME",
        },
        # P2-B OAuth authorization-code lifecycle on existing credential rows.
        # All columns are additive/nullable so legacy static-bearer rows stay
        # valid; ``auth_kind`` defaults preserve the original encrypted-secret flow.
        "mcp_server_credentials": {
            "auth_kind": "VARCHAR(32) NOT NULL DEFAULT 'static_bearer'",
            "token_type": "VARCHAR(40)",
            "scope": "VARCHAR(500)",
            "issuer": "VARCHAR(500)",
            "client_id": "VARCHAR(200)",
            "expires_at": "DATETIME",
            "refresh_token_ciphertext": "TEXT",
            "status": "VARCHAR(24) NOT NULL DEFAULT 'active'",
            "revoked_at": "DATETIME",
            "revoked_reason": "VARCHAR(500)",
            "pending_state": "VARCHAR(80)",
            "pending_code_verifier_ciphertext": "TEXT",
            "pending_scope": "VARCHAR(500)",
            "pending_redirect_uri": "VARCHAR(1000)",
            "pending_created_at": "DATETIME",
        },
        # Schema revision ledger audit fields.
        "schema_revisions": {
            "applied_by": "VARCHAR(64) NOT NULL DEFAULT ''",
            "duration_ms": "INTEGER NOT NULL DEFAULT 0",
        },
        # Non-agent (极速/思考) fetch authorization pause: the serialized original
        # request needed to resume generation after approval.
        "fetch_authorization_requests": {
            "resume_payload": "JSON",
            "assistant_message_id": "VARCHAR(36)",
            "user_message_id": "VARCHAR(36)",
        },
        # D2.1 generic Agent egress approval: card correlation with the assistant
        # message that carries the durable ``egress_authorization`` card part,
        # the optional originating tool call id, and the serialized resume
        # checkpoint so a decision can resume the exact suspended tool call.
        "egress_authorization_requests": {
            "assistant_message_id": "VARCHAR(36)",
            "user_message_id": "VARCHAR(36)",
            "tool_call_id": "VARCHAR(160)",
            "resume_payload": "JSON",
            "claimed_by": "VARCHAR(64)",
        },
        # Agent-published bidirectional sub-application: optional interaction
        # contract snapshot and the linked lightweight ComponentManifestVersion.
        "external_acquisition_files": {},
        "subapp_bundles": {
            "interaction_contract": "JSON",
            "component_manifest_id": "VARCHAR(36)",
            "agent_consent_allowlisted": "BOOLEAN NOT NULL DEFAULT 0",
        },
        "subapp_sessions": {
            "agent_triggers": "JSON NOT NULL DEFAULT '[]'",
            "agent_status": "VARCHAR(16) NOT NULL DEFAULT 'idle'",
            "agent_job_id": "VARCHAR(36)",
            "agent_error": "TEXT",
            "agent_updated_at": "DATETIME",
            "last_processed_event_id": "VARCHAR(36)",
            "agent_consent": "VARCHAR(16) NOT NULL DEFAULT 'ask'",
        },
        # Event-sourced memory context packages: correlation ids (conversation /
        # message) and the full retrieval-exclusion ledger added to the v2
        # package shape after the table shipped.
        "memory_context_packages": {
            "retrieved_ids_json": "JSON NOT NULL DEFAULT '[]'",
            "injected_ids_json": "JSON NOT NULL DEFAULT '[]'",
            "excluded_ids_json": "JSON NOT NULL DEFAULT '[]'",
            "truncated_ids_json": "JSON NOT NULL DEFAULT '[]'",
            "reason_codes_json": "JSON NOT NULL DEFAULT '{}'",
            "conversation_id": "VARCHAR(64)",
            "message_id": "VARCHAR(64)",
        },
        # Memory cold/hot zones on the v2 search projection so event-sourced
        # memories participate in the same visible layering as v1 records.
        "memory_search_documents": {
            "zone": "VARCHAR(16) NOT NULL DEFAULT 'recent'",
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
            "CREATE INDEX IF NOT EXISTS ix_sandbox_destructive_grant_intent "
            "ON sandbox_destructive_grants(command_intent_digest)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_sandbox_sessions_active_command_id "
            "ON sandbox_sessions(active_command_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_sandbox_sessions_lease_expires_at "
            "ON sandbox_sessions(lease_expires_at)"
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
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_sessions_idempotency_key_hash "
            "ON chat_sessions(idempotency_key_hash) "
            "WHERE idempotency_key_hash IS NOT NULL"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_file_text_chunks_document_revision_id "
            "ON file_text_chunks(document_revision_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_egress_authorization_requests_assistant_message_id "
            "ON egress_authorization_requests(assistant_message_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_egress_authorization_requests_user_message_id "
            "ON egress_authorization_requests(user_message_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_context_packages_conversation_id "
            "ON memory_context_packages(conversation_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_memory_context_packages_message_id "
            "ON memory_context_packages(message_id)"
        )
        # B1-2: session-history reads filter by workspace+session and sort by
        # created_at; the composite index serves both the filter and the sort.
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_messages_workspace_session_created "
            "ON messages(workspace_id, session_id, created_at)"
        )
        # B1-7: TTL lease table for cross-process sweep mutual exclusion.
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS advisory_locks ("
            "name VARCHAR(120) NOT NULL PRIMARY KEY, "
            "token VARCHAR(64) NOT NULL, "
            "expires_at DATETIME, "
            "created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL"
            ")"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_advisory_locks_expires_at "
            "ON advisory_locks(expires_at)"
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
