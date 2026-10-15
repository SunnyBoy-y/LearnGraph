"""Manual "加入计划" insert contract for the active roadmap.

The Graph surface lets a learner add one node to the active plan without
running the planner again.  These tests pin the invariants that keeps honest:

* the inserted item is generated from the same node facts the planner reads
  (action type, acceptance criteria, prerequisites, priority score);
* the day/position numbering and the capacity snapshot are rewritten exactly
  like a manual reschedule, so the plan never carries two orderings;
* adding an already planned node is a locate, not a duplicate;
* an unsatisfied prerequisite produces a visible blocked item instead of a
  silently actionable one;
* a stale editor or a node from another graph is refused.

Runs entirely against an in-memory scratch DB; no network, no ports.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.errors import AppError
from app.core.security import Principal
from app.domain import models as m
from app.domain.schemas.workflow import RoadmapItemInsert, RoadmapItemReschedule
from app.services.workflow import WorkflowService

WORKSPACE = "ws-manual-insert"
GOAL = "goal-manual-insert"
GRAPH = "graph-manual-insert"
ROADMAP = "roadmap-manual-insert"
ACTOR = "user-manual-insert"
TENANT = "tenant-unit"


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        yield session
    engine.dispose()


def _seed(db: Session, *, plan_started_at: datetime | None = None) -> None:
    started = plan_started_at or datetime.now(timezone.utc) - timedelta(days=1)
    db.add(
        m.Workspace(
            id=WORKSPACE,
            tenant_id=TENANT,
            owner_user_id=ACTOR,
            name="manual insert",
        )
    )
    db.add(
        m.Goal(
            id=GOAL,
            workspace_id=WORKSPACE,
            title="操作系统",
            raw_prompt="学会操作系统",
            status="approved",
            target_weight=60,
            availability={"minutes_per_day": 60, "days_per_week": 5},
            preferences={"session_minutes": 30, "preferred_action_types": ["learn"]},
        )
    )
    db.add(
        m.Graph(
            id=GRAPH,
            workspace_id=WORKSPACE,
            goal_id=GOAL,
            title="操作系统图谱",
            status="published",
            revision=3,
        )
    )
    for node_id, label, node_type in (
        ("node-root", "操作系统", "root"),
        ("node-planned", "内存管理", "concept"),
        ("node-free", "并发与同步", "practice"),
        ("node-gated", "文件系统", "concept"),
        ("node-blocker", "进程状态", "concept"),
    ):
        db.add(
            m.GraphNode(
                id=node_id,
                workspace_id=WORKSPACE,
                graph_id=GRAPH,
                label=label,
                description=f"{label} 的定义",
                node_type=node_type,
                target_weight=70,
            )
        )
    # `node-gated` depends on an unmastered prerequisite, so it must be inserted
    # as a visible blocked item rather than as actionable work.
    db.add(
        m.GraphEdge(
            id="edge-gated",
            workspace_id=WORKSPACE,
            graph_id=GRAPH,
            source_node_id="node-blocker",
            target_node_id="node-gated",
            relation="prerequisite",
        )
    )
    db.add(
        m.Roadmap(
            id=ROADMAP,
            workspace_id=WORKSPACE,
            goal_id=GOAL,
            graph_id=GRAPH,
            graph_revision=3,
            title="操作系统路线",
            version=2,
            status="published",
            rationale="初始生成",
            published_at=started,
            planning_snapshot={
                "rule_version": "action_planner_v1",
                "plan_started_at": started.isoformat(),
                "graph_revision": 3,
                "goal": {
                    "target_weight": 60,
                    "deadline_at": None,
                    "availability": {"minutes_per_day": 60, "days_per_week": 5},
                    "preferences": {
                        "preferred_action_types": ["learn"],
                        "session_minutes": 30,
                    },
                },
                "capacity_summary": {
                    "scheduled_item_count": 1,
                    "blocked_item_count": 0,
                    "scheduled_days": 1,
                    "total_minutes": 30,
                    "available_minutes_per_day": 60,
                    "over_capacity_days": [],
                    "scheduled_after_deadline_count": 0,
                },
            },
        )
    )
    db.add(
        m.ActionItem(
            id="action-existing",
            workspace_id=WORKSPACE,
            title="学习：内存管理",
            description="内存管理的定义",
            source="roadmap",
            action_type="learn",
            goal_id=GOAL,
            graph_id=GRAPH,
            node_id="node-planned",
            roadmap_id=ROADMAP,
            day_index=1,
            duration_minutes=30,
            position=0,
            metadata_json={"roadmap_version": 2, "ranking_reason": "eligible"},
        )
    )
    db.commit()


@pytest.fixture()
def service(db: Session) -> WorkflowService:
    _seed(db)
    principal = Principal(
        user_id=ACTOR,
        username=ACTOR,
        tenant_id=TENANT,
        session_id="session-unit",
    )
    workspace = db.get(m.Workspace, WORKSPACE)
    assert workspace is not None
    return WorkflowService(db, workspace, principal)


def _items(db: Session) -> list[m.ActionItem]:
    return list(
        db.query(m.ActionItem)
        .filter(m.ActionItem.roadmap_id == ROADMAP)
        .order_by(m.ActionItem.position)
        .all()
    )


def test_insert_schedules_node_into_the_chosen_day_and_renumbers(service: WorkflowService, db: Session) -> None:
    roadmap = service.insert_roadmap_item(
        ROADMAP,
        RoadmapItemInsert(
            base_version=2,
            node_id="node-free",
            day_index=1,
            duration_minutes=45,
            rationale="今天还有时间",
        ),
    )

    items = _items(db)
    assert [item.node_id for item in items] == ["node-planned", "node-free"]
    added = items[1]
    assert added.status == "pending"
    assert added.title == "学习：并发与同步"
    # `practice` node types plan as practice work, never as generic study.
    assert added.action_type == "practice"
    assert added.day_index == 1
    assert added.duration_minutes == 45
    assert added.due_at is not None
    assert added.metadata_json["origin"] == "manual_insert"
    assert added.metadata_json["acceptance_criteria"]
    # A 75-minute day no longer fits the 60-minute availability.
    assert roadmap.planning_snapshot["capacity_summary"]["total_minutes"] == 75
    assert roadmap.planning_snapshot["capacity_summary"]["over_capacity_days"] == [1]
    assert roadmap.planning_snapshot["revision_kind"] == "manual_insert"
    assert roadmap.planning_snapshot["manual_adjustments"][-1]["kind"] == "manual_insert"


def test_insert_defaults_to_session_minutes_and_todays_last_day(service: WorkflowService, db: Session) -> None:
    service.insert_roadmap_item(
        ROADMAP,
        RoadmapItemInsert(base_version=2, node_id="node-free"),
    )
    added = _items(db)[1]
    assert added.day_index == 1
    assert added.duration_minutes == 30


def test_insert_is_idempotent_for_an_already_planned_node(service: WorkflowService, db: Session) -> None:
    service.insert_roadmap_item(
        ROADMAP,
        RoadmapItemInsert(base_version=2, node_id="node-free", day_index=1),
    )
    service.insert_roadmap_item(
        ROADMAP,
        RoadmapItemInsert(base_version=2, node_id="node-free", day_index=1),
    )
    assert [item.node_id for item in _items(db)].count("node-free") == 1


def test_insert_marks_blocked_when_a_prerequisite_is_unsatisfied(service: WorkflowService, db: Session) -> None:
    service.insert_roadmap_item(
        ROADMAP,
        RoadmapItemInsert(base_version=2, node_id="node-gated", day_index=2),
    )
    added = next(item for item in _items(db) if item.node_id == "node-gated")
    assert added.status == "blocked"
    assert added.title == "待解锁：文件系统"
    assert added.metadata_json["ranking_reason"] == "blocked_by_prerequisite"
    assert added.metadata_json["prerequisites"]["blocked_by"][0]["node_id"] == "node-blocker"


def test_insert_refuses_a_stale_editor_and_a_foreign_node(service: WorkflowService, db: Session) -> None:
    with pytest.raises(AppError) as stale:
        service.insert_roadmap_item(
            ROADMAP,
            RoadmapItemInsert(base_version=1, node_id="node-free"),
        )
    assert stale.value.status_code == 409
    assert stale.value.code == "roadmap_version_conflict"

    with pytest.raises(AppError) as foreign:
        service.insert_roadmap_item(
            ROADMAP,
            RoadmapItemInsert(base_version=2, node_id="node-not-in-this-graph"),
        )
    assert foreign.value.status_code == 404
    assert foreign.value.code == "graph_node_not_found"


def test_insert_refuses_the_goal_root(service: WorkflowService, db: Session) -> None:
    with pytest.raises(AppError) as root:
        service.insert_roadmap_item(
            ROADMAP,
            RoadmapItemInsert(base_version=2, node_id="node-root"),
        )
    assert root.value.status_code == 422
    assert root.value.code == "graph_root_not_plannable"


def test_insert_keeps_existing_item_metadata_readable(service: WorkflowService, db: Session) -> None:
    service.insert_roadmap_item(
        ROADMAP,
        RoadmapItemInsert(
            base_version=2, node_id="node-free", day_index=1, duration_minutes=45
        ),
    )
    existing = next(item for item in _items(db) if item.node_id == "node-planned")
    # Reordering must not rewrite what the planner said about untouched items,
    # while shared day-level facts (load, capacity) stay correct for everyone.
    assert existing.metadata_json["ranking_reason"] == "eligible"
    assert existing.metadata_json["roadmap_version"] == 2
    assert existing.metadata_json["schedule"]["day_total_minutes"] == 75
    assert existing.metadata_json["schedule"]["exceeds_daily_capacity"] is True


def test_reschedule_keeps_working_through_the_shared_revision_gate(
    service: WorkflowService, db: Session
) -> None:
    """Regression: insert and reschedule share one gate and one ordering rule."""

    service.insert_roadmap_item(
        ROADMAP,
        RoadmapItemInsert(base_version=2, node_id="node-free", day_index=1),
    )
    roadmap = service.reschedule_roadmap_item(
        ROADMAP,
        "action-existing",
        RoadmapItemReschedule(
            base_version=2,
            day_index=3,
            position=0,
            duration_minutes=40,
            rationale="第三天有完整时间",
        ),
    )
    moved = next(item for item in _items(db) if item.id == "action-existing")
    assert moved.day_index == 3
    assert moved.duration_minutes == 40
    assert moved.metadata_json["ranking_reason"] == "user_rescheduled"
    assert moved.metadata_json["manual_adjustment"]["from_day_index"] == 1
    assert moved.metadata_json["manual_adjustment"]["to_day_index"] == 3
    assert roadmap.planning_snapshot["revision_kind"] == "manual_reschedule"
