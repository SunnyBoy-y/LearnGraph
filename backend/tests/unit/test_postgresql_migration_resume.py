"""Independent migration recovery gates, using only disposable SQLite targets."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, text

from app.core.database_routing import atomic_json, read_json
from app.services.database_migration import DatabaseMigrationController


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    from app.core import postgresql_search
    from app.services import database_migration

    metadata = MetaData()
    Table("items", metadata, Column("id", Integer, primary_key=True))
    settings = SimpleNamespace(database_migration_state_dir=str(tmp_path / "migration"), storage_root=tmp_path / "storage")
    controller = DatabaseMigrationController(settings, SimpleNamespace(), None, metadata)
    controller.root.mkdir()
    snapshot = controller.root / "review.snapshot.sqlite3"
    with closing(sqlite3.connect(snapshot)) as connection:
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY)")
        connection.commit()
    # The target is deliberately SQLite: no PostgreSQL credentials/connections.
    target = create_engine("sqlite://")
    with target.begin() as connection:
        connection.execute(text("CREATE TABLE _lg_database_copy(job_id TEXT PRIMARY KEY,applied_commit INTEGER)"))
        connection.execute(text("INSERT INTO _lg_database_copy VALUES('review',0)"))
    monkeypatch.setattr(database_migration, "decrypt_url", lambda *_args: "postgresql://unused")
    monkeypatch.setattr(database_migration, "postgres_engine", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(controller, "_verify_saved_backup", Mock())
    baseline = Mock(side_effect=lambda *_args: controller._phase("BASELINE_VERIFIED", baseline_verified=True))
    monkeypatch.setattr(controller, "_verify_baseline", baseline)
    verify = Mock()
    monkeypatch.setattr(controller, "_verify_target", verify)
    install = Mock()
    monkeypatch.setattr(postgresql_search, "install_search", install)

    def follow(*_args):
        assert read_json(controller.state_path)["search_verified"]
        controller._phase("READY_TO_ACTIVATE")

    follow = Mock(side_effect=follow)
    monkeypatch.setattr(controller, "_follow_source", follow)
    job = {"id": "review", "source_path": str(snapshot), "target_secret": {"test": True}, "backup_verified": True}
    try:
        yield controller, target, job, baseline, verify, install, follow
    finally:
        target.dispose()


@pytest.mark.parametrize("baseline_verified,search_verified", [(False, False), (True, False), (True, True)])
def test_resume_search_gate_is_independent_of_baseline(recovery, baseline_verified, search_verified):
    controller, target, job, baseline, verify, install, follow = recovery
    atomic_json(controller.state_path, {**job, "baseline_verified": baseline_verified, "search_verified": search_verified})

    controller._resume()

    assert baseline.call_count == (0 if baseline_verified else 1)
    assert install.call_count == (0 if search_verified else 1)
    if not search_verified:
        install.assert_called_once_with(target)
    verify.assert_called_once_with(target)
    assert follow.call_count == 1
    assert follow.call_args.args[0] == controller.root / "review.snapshot.sqlite3"
    assert follow.call_args.args[1] is target
    assert follow.call_args.args[3] == 0
    state = read_json(controller.state_path)
    assert state["state"] == "READY_TO_ACTIVATE"
    assert state["baseline_verified"] and state["search_verified"]


def test_failed_search_installation_can_be_retried_after_verified_baseline(recovery):
    controller, target, job, baseline, verify, install, follow = recovery
    atomic_json(controller.state_path, {**job, "baseline_verified": True, "search_verified": False})
    install.side_effect = [ValueError("search setup interrupted"), None]

    controller._resume()

    state = read_json(controller.state_path)
    assert state["state"] == "FAILED"
    assert state["baseline_verified"] and not state["search_verified"]
    verify.assert_not_called()
    follow.assert_not_called()
    # Retry the actual recovery entry point. A verified baseline stays verified,
    # but it must not suppress the unfinished search installation.
    controller._resume()

    assert install.call_count == 2
    baseline.assert_not_called()
    verify.assert_called_once_with(target)
    assert follow.call_count == 1
    state = read_json(controller.state_path)
    assert state["state"] == "READY_TO_ACTIVATE" and state["search_verified"]
