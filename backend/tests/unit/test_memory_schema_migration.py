import hashlib

import pytest
from sqlalchemy import create_engine

from app.core import migrations
from app.core.migrations import _memory_benchmark_source_metadata


def test_memory_benchmark_metadata_migration_handles_existing_sqlite_rows() -> None:
    engine = create_engine("sqlite:///:memory:")

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE memory_search_documents (
                id VARCHAR(64) PRIMARY KEY,
                content TEXT NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO memory_search_documents (id, content) VALUES ('legacy', 'text')"
        )

        _memory_benchmark_source_metadata(connection)
        _memory_benchmark_source_metadata(connection)

        metadata_column = next(
            row
            for row in connection.exec_driver_sql(
                'PRAGMA table_info("memory_search_documents")'
            ).fetchall()
            if row[1] == "metadata_json"
        )
        assert metadata_column[3] == 1
        assert metadata_column[4] == "'{}'"
        assert connection.exec_driver_sql(
            "SELECT metadata_json FROM memory_search_documents WHERE id = 'legacy'"
        ).scalar_one() == "{}"

    engine.dispose()


def test_memory_occurrence_migration_backfills_once_without_using_updated_at() -> None:
    from app.core.migrations import _memory_search_event_at

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE memory_records (id TEXT PRIMARY KEY, event_at DATETIME, created_at DATETIME)")
        connection.exec_driver_sql("CREATE TABLE memory_search_documents (id TEXT PRIMARY KEY, target_id TEXT, target_type TEXT, workspace_id TEXT, valid_from DATETIME, created_at DATETIME, updated_at DATETIME)")
        connection.exec_driver_sql("INSERT INTO memory_records VALUES ('fact', '2026-10-18 00:00:00', '2026-11-24 00:00:00')")
        connection.exec_driver_sql("INSERT INTO memory_search_documents VALUES ('d1', 'fact', 'memory', 'w', NULL, '2026-11-24 00:00:00', '2026-12-01 00:00:00'), ('d2', 'other', 'memory', 'w', '2026-10-19 00:00:00', '2026-11-24 00:00:00', '2026-12-01 00:00:00')")
        _memory_search_event_at(connection)
        _memory_search_event_at(connection)
        assert connection.exec_driver_sql("SELECT event_at FROM memory_search_documents ORDER BY id").scalars().all() == ["2026-10-18 00:00:00", "2026-10-19 00:00:00"]
        indexes = connection.exec_driver_sql('PRAGMA index_list("memory_search_documents")').fetchall()
        assert any(row[1] == "ix_memory_search_event_at" for row in indexes)
    engine.dispose()


@pytest.mark.parametrize(
    "legacy_description",
    [
        None,
        "Repair approval state of published file-generated graphs",
        "Temporal event_at projection and search index",
        "Unknown migration",
    ],
)
def test_temporal_revision_handles_v121_collision(monkeypatch, legacy_description) -> None:
    temporal = next(m for m in migrations.MIGRATIONS if m.revision == "v1.22.0")
    monkeypatch.setattr(migrations, "MIGRATIONS", (temporal,))
    with create_engine("sqlite:///:memory:").begin() as connection:
        migrations.SchemaRevision.__table__.create(connection)
        if legacy_description is not None:
            checksum = hashlib.sha256(f"v1.21.0\0{legacy_description}".encode()).hexdigest()
            connection.execute(migrations.SchemaRevision.__table__.insert().values(
                revision="v1.21.0", description=legacy_description,
                checksum=checksum, applied_at=migrations.utc_now(),
            ))
        connection.exec_driver_sql(
            "CREATE TABLE memory_search_documents (id TEXT PRIMARY KEY, target_type TEXT, "
            "workspace_id TEXT, valid_from DATETIME, created_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO memory_search_documents VALUES ('doc', 'other', 'ws', NULL, '2026-11-24')"
        )
        if legacy_description == "Unknown migration":
            with pytest.raises(RuntimeError, match=r"checksum mismatch: v1\.21\.0"):
                migrations.apply_schema_migrations(connection)
            return
        if legacy_description == "Temporal event_at projection and search index":
            temporal.apply(connection)
        migrations.apply_schema_migrations(connection)
        migrations.apply_schema_migrations(connection)
        assert connection.exec_driver_sql(
            "SELECT event_at FROM memory_search_documents"
        ).scalar_one() == "2026-11-24"
        assert dict(connection.exec_driver_sql(
            "SELECT revision, description FROM schema_revisions"
        ).all()) == {
            **({"v1.21.0": legacy_description} if legacy_description else {}),
            "v1.22.0": temporal.description,
        }
