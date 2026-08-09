"""Memory visibility regressions.

Covers the unified memory view work:

1. v2 event projections persist a cold/hot ``zone``.
2. Automatic zone derivation matches the intended product rules.
3. ``reconcile_memory_zones`` persists layering for records and projections.
4. ``MemoryService.list_views`` surfaces v2-only memories as read-only views.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain import models as m
from app.domain.memory_event_models import MemoryEvent, MemorySearchDocument, MemoryStream
from app.providers.local.memory import LocalWorkspaceMemoryProvider
from app.services.memory import MemoryService
from app.services.memory_projector import MemoryProjector
from app.services.memory_zones import derive_record_zone, reconcile_memory_zones

WORKSPACE = "ws-memory-visibility"
ACTOR = "user-memory-visibility"
TENANT = "local-tenant"


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
        session.add(
            m.Workspace(
                id=WORKSPACE,
                tenant_id=TENANT,
                owner_user_id=ACTOR,
                name="memory visibility",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def _seed_event(
    db: Session,
    memory_id: str,
    *,
    event_type: str = "memory.atom_created",
) -> MemoryEvent:
    stream = MemoryStream(
        id=f"stream-{memory_id}",
        aggregate_type="memory_atom",
        aggregate_id=memory_id,
        current_version=1,
        status="active",
        tenant_id=TENANT,
        subject_user_id=ACTOR,
        workspace_id=WORKSPACE,
    )
    db.add(stream)
    db.flush()
    event = MemoryEvent(
        event_id=f"event-{memory_id}",
        stream_id=stream.id,
        stream_version=1,
        event_type=event_type,
        producer="api",
        actor_type="user",
        actor_id=ACTOR,
        tenant_id=TENANT,
        subject_user_id=ACTOR,
        workspace_id=WORKSPACE,
        idempotency_key=f"idem-{memory_id}",
        sensitivity="normal",
        payload_hash="x" * 71,
    )
    db.add(event)
    db.commit()
    return event


def test_projector_persists_zone_from_event_payload(db: Session) -> None:
    event = _seed_event(db, "mem-hot")
    MemoryProjector(db).apply(
        event,
        {
            "memory_id": "mem-hot",
            "title": "FastAPI 路由",
            "content": "FastAPI 使用装饰器注册路由",
            "zone": "hot",
            "importance": 0.9,
        },
    )
    db.commit()
    document = db.scalar(
        select(MemorySearchDocument).where(
            MemorySearchDocument.target_id == "mem-hot"
        )
    )
    assert document is not None
    assert document.zone == "hot"
    assert document.subject == "FastAPI 路由"


def test_derive_record_zone_rules(db: Session) -> None:
    now = datetime.now(timezone.utc)

    active_goal = m.MemoryRecord(
        id="r-goal",
        workspace_id=WORKSPACE,
        tenant_id=TENANT,
        title="goal memory",
        content_hash="h",
        relative_path="",
        goal_id="goal-1",
        zone="topics",
    )
    unconfirmed = m.MemoryRecord(
        id="r-recent",
        workspace_id=WORKSPACE,
        tenant_id=TENANT,
        title="recent memory",
        content_hash="h",
        relative_path="",
        zone="topics",
        confirmation_count=0,
    )
    expired = m.MemoryRecord(
        id="r-expired",
        workspace_id=WORKSPACE,
        tenant_id=TENANT,
        title="expired memory",
        content_hash="h",
        relative_path="",
        zone="recent",
        valid_until=now - timedelta(days=1),
    )
    for record in (active_goal, unconfirmed, expired):
        db.add(record)
    db.commit()

    assert (
        derive_record_zone(
            active_goal,
            active_goal_ids={"goal-1"},
            closed_session_ids=set(),
            now=now,
        )
        == "hot"
    )
    assert (
        derive_record_zone(
            unconfirmed,
            active_goal_ids=set(),
            closed_session_ids=set(),
            now=now,
        )
        == "recent"
    )
    assert (
        derive_record_zone(
            expired,
            active_goal_ids=set(),
            closed_session_ids=set(),
            now=now,
        )
        == "archive"
    )


def test_reconcile_memory_zones_persists_layering(db: Session) -> None:
    now = datetime.now(timezone.utc)
    db.add(
        m.Goal(
            id="goal-1",
            workspace_id=WORKSPACE,
            title="active goal",
            raw_prompt="learn FastAPI",
            status="published",
        )
    )
    goal_record = m.MemoryRecord(
        id="r-goal",
        workspace_id=WORKSPACE,
        tenant_id=TENANT,
        title="goal memory",
        content_hash="h",
        relative_path="",
        goal_id="goal-1",
        zone="topics",
    )
    expired_record = m.MemoryRecord(
        id="r-expired",
        workspace_id=WORKSPACE,
        tenant_id=TENANT,
        title="expired memory",
        content_hash="h",
        relative_path="",
        zone="recent",
        valid_until=now - timedelta(days=1),
    )
    stale_document = MemorySearchDocument(
        id="doc-stale",
        target_type="memory",
        target_id="mem-stale",
        target_version=1,
        memory_layer="L4",
        memory_type="semantic_memory",
        subject="stale event memory",
        content="content",
        tenant_id=TENANT,
        subject_user_id=ACTOR,
        workspace_id=WORKSPACE,
        status="active",
        sensitivity="normal",
        importance=0.4,
        confidence=0.7,
        source_event_id="event-stale",
        content_hash="h",
        zone="topics",
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=10),
    )
    db.add_all([goal_record, expired_record, stale_document])
    db.commit()

    report = reconcile_memory_zones(db, WORKSPACE, now=now)
    assert report.changed >= 2
    db.commit()
    db.refresh(goal_record)
    db.refresh(expired_record)
    db.refresh(stale_document)
    assert goal_record.zone == "hot"
    assert expired_record.zone == "archive"
    assert stale_document.zone == "topics"


def test_list_views_includes_event_only_memory(db: Session) -> None:
    provider = LocalWorkspaceMemoryProvider(
        Path(tempfile.mkdtemp(prefix="lg-mem-views-")), WORKSPACE
    )
    service = MemoryService(
        db,
        db.get(m.Workspace, WORKSPACE),
        ACTOR,
        provider,
        Path(tempfile.mkdtemp(prefix="lg-mem-views-root-")),
    )
    now = datetime.now(timezone.utc)
    db.add(
        MemorySearchDocument(
            id="doc-event-only",
            target_type="memory",
            target_id="mem-event-only",
            target_version=3,
            memory_layer="L4",
            memory_type="semantic_memory",
            subject="Event Only Memory",
            content="This memory has no v1 record yet.",
            tenant_id=TENANT,
            subject_user_id=ACTOR,
            workspace_id=WORKSPACE,
            status="active",
            sensitivity="normal",
            zone="recent",
            importance=0.6,
            confidence=0.8,
            source_event_id="event-only",
            content_hash="h",
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()

    views = service.list_views(include_content=True)
    event_view = next((view for view in views if view.id == "mem-event-only"), None)
    assert event_view is not None
    assert event_view.view_source == "event"
    assert event_view.title == "Event Only Memory"
    assert event_view.content == "This memory has no v1 record yet."
    assert event_view.zone == "recent"
    assert event_view.restore_available is False


def test_profile_schema_v2_requires_overview_and_dimensions() -> None:
    from app.services.memory_profile import _profile_schema

    schema = _profile_schema()
    assert schema["required"] == ["overview", "dimensions"]
    props = schema["properties"]
    assert "overview" in props
    assert "dimensions" in props
    dim_items = props["dimensions"]["items"]
    assert dim_items["required"] == ["key", "title", "paragraphs"]


def test_profile_view_renders_overview_and_dimensions(db: Session) -> None:
    from app.domain.memory_event_models import utc_now
    from app.domain.models import MemoryProfileSnapshot
    from app.services.memory_profile import MemoryProfileService
    from app.core.config import Settings

    snapshot = MemoryProfileSnapshot(
        workspace_id=WORKSPACE,
        owner_subject_id=ACTOR,
        version=1,
        status="ready",
        markdown="## 概览\n你好",
        structured_sections=[
            {
                "kind": "overview",
                "heading": "概览",
                "paragraphs": [
                    {"id": "overview", "text": "你好，我是示例用户。", "atom_ids": ["a1"]}
                ],
            },
            {
                "kind": "dimension",
                "key": "learning_style",
                "heading": "学习方式",
                "paragraphs": [
                    {"id": "d0p0", "text": "偏好结构化学习。", "atom_ids": ["a1"]}
                ],
            },
        ],
        source_atom_ids=["a1"],
        source_fingerprint="fp",
        prompt_version="memory-profile-v2",
        generated_at=utc_now(),
        activated_at=utc_now(),
    )
    db.add(snapshot)
    db.commit()
    service = MemoryProfileService(
        db,
        db.get(m.Workspace, WORKSPACE),
        ACTOR,
        Settings(),
    )
    view = service._profile_view(snapshot)
    assert view.overview == "你好，我是示例用户。"
    assert view.source_count == 1
    assert len(view.dimensions) == 1
    assert view.dimensions[0].key == "learning_style"
    assert view.dimensions[0].title == "学习方式"
