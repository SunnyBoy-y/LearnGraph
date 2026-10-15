"""Focused recovery regressions; isolated SQLite, no real model calls."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.models import Graph, GraphNode, DurableJob
from app.domain.graph_cover_models import GraphCoverJob
from app.domain.learning_package_models import LearningPackage, LearningEnrollment
from app.domain.schemas.graphs import GraphCoverUpdateRequest
from app.services.learning_packages import LearningPackageService, complete_training_activity
from app.services.graph_cover_management import GraphCoverService
from app.services import graph_cover_ai, graph_cover_management


@pytest.fixture
def isolated_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # Service-level isolation: ownership is supplied explicitly; no production DB.
    Base.metadata.create_all(engine, tables=[
        Graph.__table__, GraphNode.__table__, DurableJob.__table__,
        GraphCoverJob.__table__, LearningPackage.__table__, LearningEnrollment.__table__,
    ])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(graph_cover_management.AuditRepository, "record", lambda *a, **kw: None)
    monkeypatch.setattr(graph_cover_ai.AuditRepository, "record", lambda *a, **kw: None)
    monkeypatch.setattr(graph_cover_ai, "SessionLocal", factory)
    monkeypatch.setattr(graph_cover_ai, "_lease_ok", lambda *a, **kw: True)
    monkeypatch.setattr(graph_cover_ai, "_actor_can_write_graph", lambda *a, **kw: True)
    yield factory
    engine.dispose()


SPEC = {
    "variables": [{"id": "x", "initial": 1, "minimum": 0, "maximum": 10}],
    "actions": [{"id": "inc",
        "requires": [{"variable": "x", "operator": "gte", "value": 1}],
        "effects": [{"variable": "x", "operation": "add", "value": 1}],
        "feedback": "ok"}],
    "success": [{"variable": "x", "operator": "gte", "value": 2}],
}


def add_enrollment(db, activity):
    package = LearningPackage(id="package", workspace_id="ws", node_id="node",
        build_id="build", fingerprint="fingerprint",
        manifest={"lesson": {"sections": [{"id": "section"}]}},
        private_assessment={}, private_activity=deepcopy(SPEC))
    row = LearningEnrollment(id="enrollment", workspace_id="ws", user_id="user",
        package_id=package.id, revision=0,
        progress={"sections": [], "activity": activity})
    db.add_all([package, row])
    db.commit()
    return row


def test_late_activity_defaults_preserve_existing_progress():
    existing = {"values": {"x": 7, "earned": 4}, "history": ["old"], "completed": True}
    before = deepcopy(existing)
    result = complete_training_activity(SPEC, existing)
    assert result == existing
    assert existing == before
    assert result is not existing
    assert complete_training_activity(SPEC, {"values": {}})["values"] == {"x": 1}


def test_enrollment_read_patches_late_activity_without_mutating_row(isolated_db):
    with isolated_db() as db:
        row = add_enrollment(db, {"values": {}, "history": [], "completed": False})
        before = deepcopy(row.progress)
        view = LearningPackageService(db, "ws", "user").enrollment_view(row)
        assert view["progress"]["activity"]["values"] == {"x": 1}
        assert row.progress == before
        assert row.revision == 0


def test_late_activity_can_execute_without_restart(isolated_db):
    with isolated_db() as db:
        row = add_enrollment(db, {"values": {}, "history": [], "completed": False})
        result = LearningPackageService(db, "ws", "user").progress(row.id, SimpleNamespace(
            expected_revision=0, section_id=None, reset_activity=False, action_id="inc"))
        assert result["progress"]["activity"]["values"]["x"] == 2
        assert result["progress"]["activity"]["completed"] is True
        assert result["revision"] == 1


def seed_cover(db, graph_id="graph", status="queued"):
    graph = Graph(id=graph_id, workspace_id="ws", goal_id="goal", title="Test graph",
        status="published", revision=1, cover_svg="original")
    durable = DurableJob(id=f"durable-{graph_id}", workspace_id="ws", kind="graph_cover", status="leased" if status == "running" else status, payload={})
    job = GraphCoverJob(id=f"cover-{graph_id}", workspace_id="ws", graph_id=graph_id,
        actor_id="user", engine="svg", status=status, prompt="test", job_id=durable.id)
    db.add_all([graph, durable, job])
    db.commit()
    return graph.id, job.id, durable.id


def manual_cover(db, graph_id):
    service = GraphCoverService(db, "ws", "user", can_access=lambda *a, **kw: True)
    service.update(graph_id, GraphCoverUpdateRequest(mode="template", template="paper"))
    db.expire_all()
    return db.get(Graph, graph_id).cover_svg


def test_manual_cover_cancels_only_matching_active_jobs(isolated_db):
    with isolated_db() as db:
        graph_id, cover_id, durable_id = seed_cover(db, status="running")
        _, other_cover, other_durable = seed_cover(db, "other")
        cover = manual_cover(db, graph_id)
        assert cover and cover != "original"
        assert db.get(GraphCoverJob, cover_id).status == "cancelled"
        assert db.get(DurableJob, durable_id).status == "cancelled"
        assert db.get(GraphCoverJob, other_cover).status == "queued"
        assert db.get(DurableJob, other_durable).status == "queued"


def test_late_ai_result_cannot_overwrite_manual_cover(isolated_db, monkeypatch):
    with isolated_db() as db:
        graph_id, cover_id, durable_id = seed_cover(db)
    saved = {}
    def render(*args, **kwargs):
        with isolated_db() as manual_db:
            saved["cover"] = manual_cover(manual_db, graph_id)
        return "<svg>stale AI result</svg>", "provider", "model"
    monkeypatch.setattr(graph_cover_ai, "_render_svg_cover", render)
    graph_cover_ai.run_cover_ai_job(durable_id, "lease-token", cover_id)
    with isolated_db() as db:
        assert saved["cover"]
        assert db.get(Graph, graph_id).cover_svg == saved["cover"]
        assert db.get(GraphCoverJob, cover_id).status == "cancelled"
        assert db.get(DurableJob, durable_id).status == "cancelled"


def test_uncancelled_ai_cover_still_completes(isolated_db, monkeypatch):
    with isolated_db() as db:
        graph_id, cover_id, durable_id = seed_cover(db)
    monkeypatch.setattr(graph_cover_ai, "_render_svg_cover",
        lambda *a, **kw: ("<svg>new AI result</svg>", "provider", "model"))
    graph_cover_ai.run_cover_ai_job(durable_id, "lease-token", cover_id)
    with isolated_db() as db:
        assert db.get(Graph, graph_id).cover_svg == "<svg>new AI result</svg>"
        assert db.get(GraphCoverJob, cover_id).status == "ready"
