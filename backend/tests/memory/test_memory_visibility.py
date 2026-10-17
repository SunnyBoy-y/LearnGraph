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


def test_profile_schema_requires_overview_sources_and_dimensions() -> None:
    from app.services.memory_profile import _profile_schema

    schema = _profile_schema()
    assert schema["required"] == ["overview", "overview_atom_ids", "dimensions"]
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
    # SQLite returns naive UTC datetimes. The API must include the UTC offset
    # so browsers in Asia/Shanghai do not interpret them as local time.
    snapshot.generated_at = snapshot.generated_at.replace(tzinfo=None)
    snapshot.updated_at = snapshot.updated_at.replace(tzinfo=None)
    view = service._profile_view(snapshot)
    assert view.generated_at.utcoffset() == timedelta(0)
    assert view.updated_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("status", ["stale", "failed", "building"])
def test_unavailable_report_does_not_substitute_atoms_or_show_old_claims(db: Session, status: str) -> None:
    from app.services.memory_profile import MemoryProfileService

    for memory_id, state, subject in (
        ("current", "active", ACTOR),
        ("deleted", "deleted", ACTOR),
        ("other-user", "active", "another-user"),
    ):
        db.add(m.MemoryRecord(
            id=memory_id, workspace_id=WORKSPACE, tenant_id=TENANT,
            subject_user_id=subject, title=memory_id, content_hash="hash",
            relative_path="", state=state,
        ))
        db.flush()
        db.add(m.MemoryRevision(
            memory_id=memory_id, workspace_id=WORKSPACE, revision=1,
            title=memory_id, content=f"{memory_id} preference", content_hash="hash",
            actor_id=subject,
        ))
    snapshot = m.MemoryProfileSnapshot(
        workspace_id=WORKSPACE, owner_subject_id=ACTOR, version=2, status=status,
        markdown="Obsolete private claim", source_atom_ids=["deleted"],
        structured_sections=[{
            "heading": "Old report",
            "paragraphs": [{"text": "Obsolete private claim", "atom_ids": ["deleted"]}],
        }],
        source_fingerprint="old-fingerprint", stale_reason="source_changed",
    )
    db.add(snapshot)
    db.commit()
    service = object.__new__(MemoryProfileService)
    service.db, service.workspace_id, service.actor_id = db, WORKSPACE, ACTOR

    view = service.get_profile()

    assert view.status == status
    assert view.id == snapshot.id and view.version == 2
    assert view.stale_reason == "source_changed"
    assert view.source_atom_ids == []
    assert view.markdown == "" and view.overview == "" and view.dimensions == []
    assert "Obsolete private claim" not in view.model_dump_json()
    assert "other-user" not in view.model_dump_json()
    # Reading the fallback must not overwrite the generated snapshot.
    assert snapshot.markdown == "Obsolete private claim"


def test_failed_report_without_current_memories_keeps_failure_status(db: Session) -> None:
    from app.services.memory_profile import MemoryProfileService

    db.add(m.MemoryProfileSnapshot(
        workspace_id=WORKSPACE, owner_subject_id=ACTOR, version=1, status="failed",
        markdown="Deleted claim", source_atom_ids=["deleted"],
        source_fingerprint="old", stale_reason="refresh_failed|timeout|",
    ))
    db.commit()
    service = object.__new__(MemoryProfileService)
    service.db, service.workspace_id, service.actor_id = db, WORKSPACE, ACTOR

    view = service.get_profile()

    assert view.status == "failed"
    assert view.source_count == 0 and view.markdown == ""
    assert view.overview == "" and view.dimensions == []
    assert view.stale_reason.startswith("refresh_failed|")


def _profile_model_service(db: Session, monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app.services import memory_profile

    service = object.__new__(memory_profile.MemoryProfileService)
    service.db, service.workspace_id, service.actor_id = db, WORKSPACE, ACTOR
    model = SimpleNamespace(
        available=True, provider_id="test-provider", model_id="test-model",
        remote_capability=True, context_window_tokens=128_000, max_output_tokens=4096,
        last_usage={}, generate_json=MagicMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(service, "_model_selection", lambda *args: (model.provider_id, model.model_id))
    monkeypatch.setattr(service, "_profile_model", lambda *args: (model, True))
    billing = MagicMock()
    monkeypatch.setattr(memory_profile, "BillingService", lambda *args: billing)
    return service, model, billing


@pytest.mark.parametrize("schema_name,size,context,allowed", [
    ("memory_profile", 20_000, 128_000, True),
    ("memory_profile_verification", 20_000, 128_000, True),
    ("memory_atom_extraction", 12_000, 128_000, False),
    ("memory_profile", 20_000, 16_000, False),
    ("memory_profile", 32_000, 128_000, False),
])
def test_report_input_budget_is_task_and_model_aware(
    db: Session, monkeypatch: pytest.MonkeyPatch, schema_name: str,
    size: int, context: int, allowed: bool,
) -> None:
    from app.core.errors import AppError

    service, model, billing = _profile_model_service(db, monkeypatch)
    model.context_window_tokens = context
    call = dict(
        prompt="学" * size, schema_name=schema_name, schema={"type": "object"},
        feature="test", estimated_output_tokens=2400,
    )
    if allowed:
        assert service._model_json(**call)[0] == {"ok": True}
        model.generate_json.assert_called_once()
        assert billing.preflight_model_call.call_args.kwargs["estimated_input_tokens"] > size
    else:
        with pytest.raises(AppError) as error:
            service._model_json(**call)
        assert error.value.code == "memory_profile_input_budget_exceeded"
        model.generate_json.assert_not_called()
        billing.preflight_model_call.assert_not_called()


def test_large_memory_refresh_generates_verified_report_and_retries_immediately(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from app.domain.models import utc_now
    from app.services.token_estimate import estimate_tokens

    for index in range(160):
        memory_id, evidence_id = f"report-{index:03}", f"evidence-{index}"
        db.add(m.MemoryEvidence(
            id=evidence_id, workspace_id=WORKSPACE, source_kind="user_input",
            source_id=memory_id, authorship="user", content_hash="hash", profile_eligible=True,
        ))
        db.add(m.MemoryRecord(
            id=memory_id, workspace_id=WORKSPACE, tenant_id=TENANT, subject_user_id=ACTOR,
            title="学习偏好", content_hash="hash", relative_path="", evidence_ids=[evidence_id],
        ))
        db.flush()
        db.add(m.MemoryRevision(
            memory_id=memory_id, workspace_id=WORKSPACE, revision=1, title="学习偏好",
            content="用户喜欢先通过具体的例子和练习来理解概念。" * 5,
            content_hash="hash", actor_id=ACTOR,
        ))
    db.add(m.MemoryProfileSnapshot(
        workspace_id=WORKSPACE, owner_subject_id=ACTOR, version=1, status="failed",
        source_fingerprint="old", stale_reason=f"refresh_failed|input budget|{utc_now().isoformat()}",
    ))
    db.commit()
    service, model, billing = _profile_model_service(db, monkeypatch)

    _, input_atoms = service._atom_payloads()
    selected_ref = f"A{next(index for index, atom in enumerate(input_atoms) if atom['id'] == 'report-159') + 1}"

    def generate(prompt, schema_name, schema):
        from app.providers.remote.openai import OpenAICompatibleChatProvider

        assert estimate_tokens(prompt + json.dumps(schema, ensure_ascii=False)) + 512 <= 32_000
        if schema_name == "memory_profile":
            # Reproduce the old 12K guard failure with a real 160-atom input.
            assert estimate_tokens(prompt) > 12_000
            assert '"id": "A1"' in prompt and "report-159" not in prompt
            assert "当前时间：" in prompt and "不得写成当前/未来安排" in prompt
            return OpenAICompatibleChatProvider._validate_structured_result({
                "overview": f"你倾向于通过例子与练习理解概念（{selected_ref}）。",
                "overview_atom_ids": [selected_ref],
                "dimensions": [{
                    "key": "learning_style", "title": "学习方式",
                    # Prose length and citation count are editorial targets,
                    # not structural errors that should discard a valid report.
                    "paragraphs": [{"text": "你喜欢先看具体例子，再通过练习理解概念。" * 20, "atom_ids": [selected_ref] * 14}],
                }],
            }, schema)
        # The verifier sees only cited evidence, including IDs past the old
        # arbitrary first-80 overview limit.
        assert "report-159" in prompt and "report-000" not in prompt
        assert "当前时间：" in prompt
        return {"valid_claim_ids": ["overview", "d0p0"], "violations": []}

    model.generate_json.side_effect = generate
    view = service.refresh_profile(force=True)

    assert view.status == "ready" and view.version == 2
    assert view.overview == "你倾向于通过例子与练习理解概念。"
    assert len(view.dimensions) == 1 and view.source_count == 160
    assert model.generate_json.call_count == 2 and billing.record_usage.call_count == 2
    snapshot = db.get(m.MemoryProfileSnapshot, view.id)
    assert snapshot.claim_atom_map["overview"] == ["report-159"]
    assert service.get_profile().status == "ready"
    snapshot.status = "failed"
    snapshot.stale_reason = "refresh_failed|timeout|"
    db.commit()
    assert service.get_profile().overview == view.overview
    # Once cited evidence is removed, the saved prose must disappear, without
    # switching the report body to a list of the remaining atoms.
    db.get(m.MemoryRecord, "report-159").state = "deleted"
    db.commit()
    unavailable = service.get_profile()
    assert unavailable.source_count == 159
    assert unavailable.overview == "" and unavailable.markdown == ""
    assert unavailable.dimensions == []


def test_background_refresh_still_respects_failure_cooldown(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock
    from app.domain.models import utc_now

    db.add(m.MemoryProfileSnapshot(
        workspace_id=WORKSPACE, owner_subject_id=ACTOR, version=1, status="failed",
        source_fingerprint="", stale_reason=f"refresh_failed|timeout|{utc_now().isoformat()}",
    ))
    db.commit()
    service, _, _ = _profile_model_service(db, monkeypatch)
    rebuild = MagicMock()
    monkeypatch.setattr(service, "_refresh_profile", rebuild)

    assert service.refresh_profile().status == "failed"
    rebuild.assert_not_called()
