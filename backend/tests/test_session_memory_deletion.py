from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.errors import AppError
from app.core.security import Principal
from app.domain.memory_event_models import (
    ConversationEpisode, MemoryContextPackage, MemoryPayloadKey,
    MemoryProjectionOutbox, MemoryScopeContext, MemorySearchDocument, MemoryStream,
)
from app.domain.memory_event_types import MemoryEventType
from app.domain.models import (
    ChatSession, MemoryDeletionRecovery, MemoryDraft, MemoryEmbedding,
    MemoryEvidence, MemoryExtractionState, MemoryJournalEntry, MemoryProfileSnapshot,
    MemoryRecord, MemoryRevision, Message, Project, Workspace, utc_now,
)
from app.services.memory_commands import MemoryCommandService
from app.services.memory_crypto import MemoryPayloadCipher
from app.services.memory_event_store import AppendEvent, MemoryEventStore
from app.services.memory_projector import MemoryProjector, ensure_memory_search_fts
from app.services.session_memory_deletion import SessionMemoryDeletionService
from app.services.workflow import WorkflowService


@pytest.fixture
def env():
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        workspace = Workspace(id="w", tenant_id="t", owner_user_id="u", name="test")
        db.add_all([workspace, Workspace(id="other", tenant_id="other-t", owner_user_id="v", name="other")])
        db.flush()
        db.add_all([
            ChatSession(id="delete", workspace_id="w", title="Delete"),
            ChatSession(id="keep", workspace_id="w", title="Keep", parent_session_id="delete"),
            ChatSession(id="second", workspace_id="w", title="Second"),
            ChatSession(id="foreign", workspace_id="other", title="Foreign"),
        ])
        db.flush()
        db.add(Message(id="message", workspace_id="w", session_id="delete", role="user", content="source statement"))
        ensure_memory_search_fts(db)
        db.commit()
        principal = Principal(user_id="u", username="test", tenant_id="t", session_id="login")
        yield db, workspace, WorkflowService(db, workspace, principal)
    engine.dispose()


def atom(db, ident, *, session_id=None, workspace_id="w", tenant_id="t", **fields):
    row = MemoryRecord(id=ident, workspace_id=workspace_id, tenant_id=tenant_id,
                       session_id=session_id, title=ident, content_hash="hash",
                       relative_path="topics/test.md", **fields)
    db.add(row)
    db.flush()
    db.add(MemoryRevision(workspace_id=workspace_id, memory_id=ident, revision=1,
                          title=ident, content="private content", content_hash="hash", actor_id="u"))
    return row


def store(db):
    from app.core.config import get_settings
    from app.services.memory_event_ingestor import event_cipher_from_settings
    return MemoryEventStore(db, event_cipher_from_settings(get_settings()))


def test_single_delete_clears_content_copies_and_keeps_other_sessions(env):
    db, workspace, workflow = env
    forgotten = atom(db, "owned", session_id="delete")
    keep = atom(db, "retained", session_id="keep")
    foreign = atom(db, "foreign-memory", session_id="delete", workspace_id="other", tenant_id="other-t")
    now = utc_now()
    db.add_all([
        MemoryDraft(id="draft", workspace_id="w", session_id="delete", content="draft secret"),
        MemoryExtractionState(workspace_id="w", session_id="delete"),
        MemoryEvidence(id="evidence", workspace_id="w", source_kind="user_statement",
                       source_id="message", message_id="message", authorship="user",
                       content_hash="hash", excerpt="private statement"),
        MemoryEmbedding(workspace_id="w", memory_id="owned", model_key="test", content_hash="hash", vector=[1.0]),
        MemoryJournalEntry(workspace_id="w", memory_id="owned", revision=1, operation="ADD",
                           provider_id="local_workspace_markdown", content_hash="hash", payload={"content": "secret"}),
        MemoryDeletionRecovery(workspace_id="w", memory_id="owned", encrypted_payload="encrypted",
                               recoverable_until=now+timedelta(days=1), audit_retention_until=now+timedelta(days=7)),
        MemoryProfileSnapshot(workspace_id="w", owner_subject_id="u", version=1, markdown="profile secret",
                              source_fingerprint="hash", source_atom_ids=["owned", "retained"]),
        MemoryContextPackage(workspace_id="w", request_hash="r", query_hash="q", scope_hash="s",
                             policy_version="1", builder_version="1", agent_id="main", package_hash="h",
                             injected_ids_json=["owned", "retained"]),
    ])
    db.commit()
    impact = workflow.session_impact("delete")
    assert {i.resource_type: i.count for i in impact.impacts}["memory"] == 1
    workflow.delete_session("delete", "Delete")
    db.expire_all()
    assert db.get(ChatSession, "delete") is None
    assert db.get(ChatSession, "keep").parent_session_id is None
    assert db.get(Message, "message") is None
    assert forgotten.state == "destroyed" and forgotten.lifecycle_status == "forgotten"
    assert forgotten.title == "[forgotten]" and not forgotten.recoverable_until
    assert keep.state == foreign.state == "active"
    revision = db.scalar(select(MemoryRevision).where(MemoryRevision.memory_id == "owned"))
    assert revision.content is None and not revision.is_active
    for model in (MemoryDraft, MemoryEvidence, MemoryExtractionState, MemoryEmbedding):
        assert db.scalar(select(model)) is None
    assert db.scalar(select(MemoryJournalEntry)).payload == {}
    assert db.scalar(select(MemoryDeletionRecovery)).encrypted_payload is None
    assert db.scalar(select(MemoryProfileSnapshot)).markdown == ""
    assert db.scalar(select(MemoryContextPackage)).injected_ids_json == ["retained"]
    assert db.scalar(select(MemoryProjectionOutbox)) is not None
    assert all(key.status == "destroyed" for key in db.scalars(select(MemoryPayloadKey)))


@pytest.mark.parametrize("link", ["conversation", "scope", "source_message", "evidence", "draft", "revision"])
def test_workspace_memories_are_found_by_provenance(env, link):
    db, workspace, workflow = env
    record = atom(db, "derived")
    if link == "conversation":
        record.conversation_id = "delete"
    elif link == "scope":
        record.scope_type, record.scope_id = "session", "delete"
    elif link == "source_message":
        record.source_ids = ["message"]
    elif link == "evidence":
        db.add(MemoryEvidence(id="ev", workspace_id="w", source_kind="user_statement", source_id="message",
                              message_id="message", authorship="user", content_hash="hash"))
        record.evidence_ids = ["ev"]
    elif link == "draft":
        db.add(MemoryDraft(workspace_id="w", session_id="delete", status="COMMITTED", result_memory_id=record.id))
    else:
        db.flush()
        db.scalar(select(MemoryRevision).where(MemoryRevision.memory_id == record.id)).source_ids = ["message"]
    db.commit()
    workflow.delete_session("delete", "Delete")
    db.refresh(record)
    assert record.lifecycle_status == "forgotten"


def test_pending_draft_target_is_not_deleted(env):
    db, workspace, workflow = env
    record = atom(db, "unrelated", session_id="keep")
    db.add(MemoryDraft(workspace_id="w", session_id="delete", operation="UPDATE", target_memory_id=record.id))
    db.commit()
    workflow.delete_session("delete", "Delete")
    db.refresh(record)
    assert record.state == "active"


def test_batch_delete_cleans_both_and_is_idempotent(env):
    db, workspace, workflow = env
    first = atom(db, "first", session_id="delete")
    second = atom(db, "second-memory", session_id="second")
    db.commit()
    impact = workflow.session_batch_impact(["delete", "second"])
    assert {i.resource_type: i.count for i in impact.impacts}["memory"] == 2
    result = workflow.delete_sessions(["delete", "second"], impact.confirmation_text)
    assert result.deleted_count == 2
    assert first.state == second.state == "destroyed"
    assert workflow.delete_sessions(["delete", "second"], impact.confirmation_text).deleted_count == 2


def test_cleanup_rolls_back_with_session_delete(env):
    db, workspace, workflow = env
    record = atom(db, "rollback", session_id="delete")
    db.commit()
    with patch.object(workflow.audit, "record", side_effect=RuntimeError("audit failed")):
        with pytest.raises(RuntimeError):
            workflow.delete_session("delete", "Delete")
    db.rollback()
    assert db.get(ChatSession, "delete") is not None
    assert db.get(MemoryRecord, record.id).state == "active"
    assert db.scalar(select(MemoryRevision)).content == "private content"
    assert db.scalar(select(MemoryProjectionOutbox)) is None


@pytest.mark.parametrize("mode", ["confirmation", "foreign_workspace"])
def test_invalid_delete_never_changes_memories(env, mode):
    db, workspace, workflow = env
    record = atom(db, "safe", session_id="delete")
    db.commit()
    with pytest.raises(AppError):
        if mode == "confirmation":
            workflow.delete_session("delete", "wrong")
        else:
            workflow.delete_sessions(["delete", "foreign"], "wrong")
    assert record.state == "active"
    assert db.get(ChatSession, "delete") is not None


def test_event_only_memory_and_episode_are_removed(env):
    db, workspace, workflow = env
    events = store(db)
    scope = MemoryScopeContext(tenant_id="t", principal_user_id="u", workspace_id="w", conversation_id="delete")
    payload = {"memory_id": "v2-only", "title": "private", "content": "secret content"}
    result = events.append(scope, aggregate_type="memory_atom", aggregate_id="v2-only", expected_version=0,
                           event=AppendEvent(event_type=MemoryEventType.MEMORY_CREATED, payload=payload, idempotency_key="v2"))
    MemoryProjector(db).apply(result.event, payload)
    episode = ConversationEpisode(id="ep", stream_id="ep-stream", tenant_id="t", workspace_id="w",
                                  conversation_id="delete", title="episode", summary="private summary",
                                  content_hash="hash", head_event_id="head")
    db.add(episode)
    db.commit()
    assert db.scalar(select(MemorySearchDocument)) is not None
    workflow.delete_session("delete", "Delete")
    assert db.scalar(select(MemorySearchDocument)) is None
    assert db.scalar(text("SELECT COUNT(*) FROM memory_search_fts")) == 0
    assert db.get(ConversationEpisode, "ep") is None
    assert db.get(MemoryStream, result.event.stream_id).status == "forgotten"
    # A worker holding an old decrypted payload must not resurrect its index.
    MemoryProjector(db).apply(result.event, payload)
    assert db.scalar(select(MemorySearchDocument)) is None
    with pytest.raises(AppError) as error:
        events.append(scope, aggregate_type="memory_atom", aggregate_id="v2-only", expected_version=None,
                      event=AppendEvent(event_type=MemoryEventType.MEMORY_CORRECTED, payload=payload, idempotency_key="late"))
    assert error.value.code == "memory_stream_forgotten"


def test_project_delete_also_cleans_session_memories(env):
    db, workspace, workflow = env
    db.add(Project(id="project", workspace_id="w", title="Project"))
    db.get(ChatSession, "delete").project_id = "project"
    record = atom(db, "project-memory", session_id="delete")
    db.commit()
    workflow.delete_project("project", "Project")
    assert db.get(MemoryRecord, record.id).state == "destroyed"


def test_source_lock_rejects_deleted_session_even_if_orm_cached(env):
    from app.services.session_memory_deletion import lock_session_memory_sources
    db, workspace, workflow = env
    cached = db.get(ChatSession, "delete")
    workflow.delete_session("delete", "Delete")
    assert cached.id == "delete"
    with pytest.raises(AppError) as error:
        lock_session_memory_sources(db, workspace.id, [cached.id])
    assert error.value.code == "session_not_found"


def test_workspace_creation_retains_source_and_rejects_late_create(env, tmp_path):
    from unittest.mock import MagicMock
    from app.domain.schemas.management import MemoryCreateRequest, MemoryDraftCreateRequest
    from app.services.memory import MemoryService
    db, workspace, workflow = env
    provider = MagicMock(provider_id="local_workspace_markdown", remote_capability=False)
    service = MemoryService(db, workspace, "u", provider, tmp_path)
    payload = MemoryCreateRequest(title="derived", content="user preference")
    with patch.object(service, "_project_after_commit"):
        view = service.create(payload, source_session_id="delete")
    record = db.get(MemoryRecord, view.id)
    assert record.namespace == "workspace" and record.session_id is None
    assert record.conversation_id == "delete"
    workflow.delete_session("delete", "Delete")
    with pytest.raises(AppError) as error:
        service.create(payload, source_session_id="delete")
    assert error.value.code == "session_not_found"
    db.rollback()
    with pytest.raises(AppError) as error:
        service.create_draft(MemoryDraftCreateRequest(
            session_id="delete", title="late", content="must not survive",
        ))
    assert error.value.code == "session_not_found"
    assert db.scalar(select(MemoryDraft)) is None


def test_extraction_discards_model_result_after_source_deletion(env):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app.core.config import get_settings
    from app.services import memory_enhancement as enhancement
    db, workspace, workflow = env
    db.get(ChatSession, "delete").memory_enabled = True
    db.commit()
    model = MagicMock(available=True, provider_id="fake", model_id="fake", remote_capability=False, last_usage={})
    def generate(*args):
        workflow.delete_session("delete", "Delete")
        return {"memories": [{"title": "late", "content": "secret"}]}
    model.generate_json.side_effect = generate
    config = {"extraction": {"enabled": True, "provider_id": "fake", "model_id": "fake", "auto_commit": True}}
    with (
        patch.object(enhancement, "load_enhancement_config", return_value=config),
        patch.object(enhancement, "_workspace_memory_enabled", return_value=True),
        patch.object(enhancement, "model_provider_for_workspace", return_value=model),
        patch.object(enhancement, "_existing_memory_context", return_value=([], [])),
        patch.object(enhancement, "_user_evidence_for_messages", return_value={"message": SimpleNamespace(id="ev", observed_at=utc_now())}),
        patch.object(enhancement, "BillingService"),
    ):
        result = enhancement.extract_session_memories(db, workspace, "delete", get_settings(), force=True)
    assert result["status"] == "session_deleted"
    assert db.scalar(select(MemoryDraft)) is None
    assert db.scalar(select(MemoryExtractionState)) is None


def test_profile_discards_generated_summary_after_source_deletion(env):
    from app.services.memory_profile import MemoryProfileService
    db, workspace, workflow = env
    record = atom(db, "profile-source", session_id="delete")
    db.commit()
    service = object.__new__(MemoryProfileService)
    service.db, service.workspace_id, service.actor_id = db, workspace.id, "u"
    def model_json(**kwargs):
        if kwargs["schema_name"] == "memory_profile":
            workflow.delete_session("delete", "Delete")
            return ({"dimensions": [{"key": "learning_style", "title": "Style",
                "paragraphs": [{"text": "private preference", "atom_ids": ["A1"]}]}]}, "fake")
        return ({"valid_claim_ids": ["d0p0"]}, "fake")
    with (
        patch.object(service, "_atom_payloads", return_value=([record], [{"id": record.id}])),
        patch.object(service, "_model_selection", return_value=("fake", "fake")),
        patch.object(service, "_model_json", side_effect=model_json),
    ):
        with pytest.raises(AppError) as error:
            service.refresh_profile(force=True)
    assert error.value.code == "memory_profile_sources_changed"
    failure = db.scalar(select(MemoryProfileSnapshot))
    assert failure is not None and failure.status == "failed"
    assert failure.markdown == ""
    assert failure.structured_sections == []
    assert failure.source_atom_ids == [] and failure.claim_atom_map == {}
    assert "private preference" not in failure.stale_reason


@pytest.mark.parametrize("binding_status", ["verified", "pending_delete", "orphaned"])
def test_provider_copy_is_deleted_from_durable_outbox(env, binding_status):
    from unittest.mock import MagicMock
    from app.core.config import get_settings
    from app.domain.models import MemoryProviderBinding
    from app.services.memory_provider_projector import MemoryProviderProjector
    db, workspace, workflow = env
    record = atom(db, "provider-memory", session_id="delete")
    binding = MemoryProviderBinding(
        workspace_id=workspace.id, provider_instance_id="local_workspace_markdown",
        memory_id=record.id, revision=1, provider_record_id="topics/private.md",
        provider_entity_kind="workspace", provider_entity_value=workspace.id,
        source_content_hash="hash", target_readback_hash="hash", binding_status=binding_status,
    )
    db.add(binding)
    db.commit()
    workflow.delete_session("delete", "Delete")
    job = db.scalar(select(MemoryProjectionOutbox).where(MemoryProjectionOutbox.projection_kind == "markdown"))
    assert job is not None
    provider = MagicMock(provider_id="local_workspace_markdown", remote_capability=False)
    with patch("app.services.memory_provider_projector.memory_provider_for_workspace", return_value=provider):
        MemoryProviderProjector(db, get_settings()).handle(job)
    provider.delete.assert_called_once_with("topics/private.md")
    assert binding.binding_status == "deleted"
    assert not provider.upsert.called
