from sqlalchemy import create_engine

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
