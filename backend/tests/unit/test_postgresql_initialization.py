"""PostgreSQL initialization regressions that require no external database."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text

from app.core.database_routing import install_postgres_writer_order


@pytest.fixture
def writer_engine():
    """Exercise the real SQLAlchemy transaction events with a lock-call probe."""
    engine = create_engine("sqlite://")
    calls = []

    @event.listens_for(engine, "connect")
    def configure(dbapi, _record):
        dbapi.create_function("pg_advisory_xact_lock", 1, lambda key: calls.append(key) or 0)

    install_postgres_writer_order(engine)
    install_postgres_writer_order(engine)  # Listener installation is idempotent.
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE items(id INTEGER PRIMARY KEY)"))
        yield engine, calls
    finally:
        engine.dispose()


@pytest.mark.parametrize("write_before_savepoint", [False, True])
def test_writer_lock_is_reacquired_after_savepoint_rollback(writer_engine, write_before_savepoint):
    engine, calls = writer_engine
    with engine.connect() as connection:
        with connection.begin():
            if write_before_savepoint:
                connection.execute(text("INSERT INTO items VALUES(1)"))
            savepoint = connection.begin_nested()
            connection.execute(text("INSERT INTO items VALUES(2)"))
            assert len(calls) == 1
            savepoint.rollback()
            assert not connection.info.get("_lg_pg_writer")
            connection.execute(text("INSERT INTO items VALUES(3)"))
            connection.execute(text("UPDATE items SET id=4 WHERE id=3"))
            assert len(calls) == 2
        assert not connection.info.get("_lg_pg_writer")
        assert connection.execute(text("SELECT count(*) FROM items WHERE id=2")).scalar_one() == 0


def test_writer_lock_survives_released_savepoint_but_resets_on_outer_rollback(writer_engine):
    engine, calls = writer_engine
    with engine.connect() as connection:
        outer = connection.begin()
        with connection.begin_nested():
            connection.execute(text("INSERT INTO items VALUES(1)"))
        connection.execute(text("INSERT INTO items VALUES(2)"))
        assert len(calls) == 1
        outer.rollback()
        assert not connection.info.get("_lg_pg_writer")
        with connection.begin():
            connection.execute(text("INSERT INTO items VALUES(3)"))
            assert len(calls) == 2


def test_writer_lock_is_reacquired_after_nested_savepoint_rollback(writer_engine):
    engine, calls = writer_engine
    with engine.begin() as connection:
        parent = connection.begin_nested()
        child = connection.begin_nested()
        connection.execute(text("INSERT INTO items VALUES(1)"))
        child.commit()
        parent.rollback()
        assert not connection.info.get("_lg_pg_writer")
        connection.execute(text("INSERT INTO items VALUES(2)"))
        assert len(calls) == 2


@pytest.mark.parametrize("read_only", [False, True])
def test_postgresql_startup_installs_search_only_for_writer(monkeypatch, read_only):
    from app.core import database
    from app.core import migrations, postgresql_search

    engine = MagicMock()
    engine.dialect.name = "postgresql"
    connection = engine.begin.return_value.__enter__.return_value
    connection.dialect.name = "postgresql"
    create = MagicMock()
    migrate = MagicMock()
    install = MagicMock()
    verify = MagicMock()
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "is_sqlite", False)
    monkeypatch.setattr(database.settings, "sqlite_read_only", read_only)
    monkeypatch.setattr(database.Base.metadata, "create_all", create)
    monkeypatch.setattr(migrations, "apply_schema_migrations", migrate)
    monkeypatch.setattr(postgresql_search, "install_search", install)
    monkeypatch.setattr(database, "_ensure_sqlite_skill_package_columns", MagicMock())
    monkeypatch.setattr(database, "_verify_schema_revisions", verify)

    database._init_database_under_route_lease()

    if read_only:
        engine.begin.assert_not_called()
        create.assert_not_called()
        migrate.assert_not_called()
        install.assert_not_called()
        verify.assert_not_called()
    else:
        connection.exec_driver_sql.assert_called_once_with("SELECT pg_advisory_xact_lock(684728190123)")
        create.assert_called_once_with(bind=connection)
        migrate.assert_called_once_with(connection)
        install.assert_called_once_with(engine)
        verify.assert_called_once_with()


@pytest.mark.parametrize("projection_exists", [False, True])
def test_search_installation_backfills_only_a_new_projection(monkeypatch, projection_exists):
    from app.core import postgresql_search

    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = True
    inspector = MagicMock()
    inspector.has_table.return_value = projection_exists
    inspect = MagicMock(return_value=inspector)
    monkeypatch.setattr(postgresql_search, "inspect", inspect)

    postgresql_search.install_search(engine)

    inspect.assert_called_once_with(connection)
    inspector.has_table.assert_called_once_with("document_chunks_fts")
    statements = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert statements[0] == "SELECT pg_advisory_xact_lock(684728190123)"
    assert any(sql.startswith("CREATE TABLE IF NOT EXISTS document_chunks_fts") for sql in statements)
    assert sum(sql.startswith("CREATE INDEX IF NOT EXISTS") for sql in statements) == 11
    assert not any(sql.startswith("DELETE") for sql in statements)
    inserts = [sql for sql in statements if sql.startswith("INSERT INTO document_chunks_fts")]
    assert len(inserts) == (0 if projection_exists else 1)
    if inserts:
        assert "WHERE lifecycle_status='active'" in inserts[0]


def test_search_installation_failure_rolls_back_its_transaction(monkeypatch):
    from app.core import postgresql_search

    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = False
    inspector = MagicMock()
    inspector.has_table.return_value = False
    monkeypatch.setattr(postgresql_search, "inspect", lambda _connection: inspector)

    with pytest.raises(ValueError, match="multilingual search verification failed"):
        postgresql_search.install_search(engine)
    assert engine.begin.return_value.__exit__.call_args.args[0] is ValueError
