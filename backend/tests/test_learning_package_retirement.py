"""Live entry points are retired; queued package work never reaches a model."""
import asyncio
import inspect
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.models import DurableJob
from app.services import durable_queue as dq
from app.services import learning_packages as lp


@pytest.fixture
def queue_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'retirement.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dq, "SessionLocal", factory)
    yield factory
    engine.dispose()


@pytest.mark.parametrize("kind,payload", [
    ("learning.package.build", {"build_id": "historical-build"}),
    ("learning.package.grade", {"attempt_id": "historical-attempt"}),
])
def test_old_package_jobs_cancel_without_running_handlers(queue_db, monkeypatch, kind, payload):
    build = Mock(side_effect=AssertionError("Retired build handler was called"))
    grade = Mock(side_effect=AssertionError("Retired grading handler was called"))
    monkeypatch.setattr(lp, "run_build_stage", build)
    monkeypatch.setattr(lp, "grade_attempt", grade)
    with queue_db() as db:
        job = dq.DurableQueue(db).enqueue(workspace_id="w", kind=kind, payload=payload)
        job_id = job.id
    assert dq.run_one_durable_job("retirement-test") is True
    assert dq.run_one_durable_job("retirement-test") is False
    with queue_db() as db:
        archived = db.get(DurableJob, job_id)
        assert archived.status == "cancelled"
        assert archived.payload == payload
        assert archived.completed_at is not None
    build.assert_not_called()
    grade.assert_not_called()


@pytest.mark.parametrize("kind,key,handler", [
    ("learning.node_activity.build", "build_id", "run_build_stage"),
    ("learning.node_activity.grade", "attempt_id", "grade_attempt"),
])
def test_node_activity_jobs_dispatch(queue_db, monkeypatch, kind, key, handler):
    execute = Mock(return_value=True)
    monkeypatch.setattr(lp, handler, execute)
    with queue_db() as db:
        job = dq.DurableQueue(db).enqueue(workspace_id="w", kind=kind, payload={key: "activity"})
        job_id = job.id
    assert dq.run_one_durable_job("node-activity-test") is True
    execute.assert_called_once()
    assert execute.call_args.args[0] == job_id
    assert execute.call_args.args[2] == "activity"
    with queue_db() as db:
        assert db.get(DurableJob, job_id).status == "completed"


def test_worker_keeps_sticker_reconciliation_without_package_sweep(monkeypatch):
    from app.services import stickers

    learning = Mock(side_effect=AssertionError("Package reconciliation must stay retired"))
    sticker = Mock()
    monkeypatch.setattr(lp, "reconcile_learning_builds", learning)
    monkeypatch.setattr(stickers, "reconcile_sticker_indexes", sticker)

    async def exercise():
        stop = asyncio.Event()

        def finish(_worker, **_kwargs):
            stop.set()
            return False

        monkeypatch.setattr(dq, "run_one_durable_job", finish)
        await dq.durable_queue_worker(stop, "retirement-test")

    asyncio.run(exercise())
    learning.assert_not_called()
    sticker.assert_called_once()


def test_package_router_is_unregistered():
    from fastapi import FastAPI
    from app.api.router import api_router

    app = FastAPI()
    app.include_router(api_router)
    paths = set(app.openapi()["paths"])
    assert not any("/learning-pages" in path for path in paths)
    assert "/api/v1/node-activities/nodes/{node_id}" in paths
    assert "/api/v1/node-activities/attempts/{attempt_id}/submit" in paths
    assert "/api/v1/graphs" in paths
    assert "/api/v1/practice/sessions" in paths


def test_chat_keeps_authorized_node_context_without_package_injection():
    from app.services import chat

    source = inspect.getsource(chat)
    assert "learning_package_prompt_context" not in source
    assert "self._authorized_context(" in source
    assert "self._concept_capsule_prompt(" in source
