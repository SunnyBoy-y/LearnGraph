"""Occurrence-time regressions: pure parsing, persistence, recall and episodes."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.errors import AppError
from app.domain.models import Workspace
from app.domain.memory_event_models import ConversationEpisode, MemoryEvent, MemoryScopeContext, MemorySearchDocument, MemoryStream
from app.providers.local.memory import LocalWorkspaceMemoryProvider
from app.services.context_builder import ContextBuilder
from app.services.memory import MemoryService
from app.services.memory_occurrence import OccurrenceWindow, resolve_occurrence_window, utc_datetime
from app.services.memory_projector import MemoryProjector
from app.services.memory_retrieval import MemoryHybridRetriever
from app.services.memory_router import MemoryRouter
from app.services.temporal_normalizer import TemporalNormalizer, parse_temporal_query
from app.domain.schemas.context_builds import ContextBuildRequest

UTC = timezone.utc
OBSERVED = datetime(2026, 11, 24, 2, tzinfo=UTC)
DAY = datetime(2026, 10, 18, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.mark.parametrize("expression,start,granularity", [
    ("昨天", "2026-11-23", "date"),
    ("7天前", "2026-11-17", "date"),
    ("上周", "2026-11-16", "week"),
    ("去年", "2025-01-01", "year"),
    ("下个月", "2026-12-01", "month"),
    ("2026年10月18日", "2026-10-18", "date"),
])
def test_past_and_future_expressions_keep_verbatim(expression, start, granularity):
    raw = f"  {expression}  "
    result = TemporalNormalizer().normalize(raw, observed_at=OBSERVED, prefer_past=True)
    assert result.original_expression == raw
    assert result.as_dict()["time_expression"] == raw
    assert result.start_at.date().isoformat() == start
    assert result.granularity == granularity
    assert result.observed_at == OBSERVED


def test_timezone_and_year_ambiguity_are_anchored_to_observation():
    anchor = datetime(2026, 1, 1, 1, tzinfo=UTC)
    result = TemporalNormalizer().normalize("昨天", observed_at=anchor, timezone_name="America/Los_Angeles")
    assert result.start_at.date().isoformat() == "2025-12-30"
    anchor = datetime(2026, 1, 5, tzinfo=UTC)
    normalizer = TemporalNormalizer()
    assert normalizer.normalize("10月18日", observed_at=anchor, prefer_past=True).start_at.year == 2025
    assert normalizer.normalize("10月18日", observed_at=anchor, prefer_past=False).start_at.year == 2026


def test_temporal_query_keeps_residual_topic_and_half_open_bounds():
    query = parse_temporal_query("2026年10月18日学过什么数据库知识", now=OBSERVED)
    assert query.is_temporal_recall
    assert "数据库" in query.residual_query
    assert query.start_at == DAY
    window = resolve_occurrence_window(time_query="2026年10月18日", observed_at=OBSERVED)
    assert window.contains(DAY)
    assert not window.contains(DAY + timedelta(days=1))
    assert window.contains(DAY - timedelta(hours=1), DAY + timedelta(hours=1))
    assert not window.contains(DAY - timedelta(hours=1), DAY)
    assert not window.matches_memory(DAY + timedelta(days=2), {"start_at": DAY.isoformat()})
    with pytest.raises(AppError):
        resolve_occurrence_window(time_query="unresolvable", observed_at=OBSERVED)
    with pytest.raises(AppError):
        resolve_occurrence_window(start_at=DAY, end_at=DAY)


@pytest.fixture()
def store(tmp_path):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        workspace = Workspace(id="temporal-w", tenant_id="temporal-t", owner_user_id="temporal-u", name="temporal")
        db.add(workspace)
        db.commit()
        service = MemoryService(db, workspace, "temporal-u", LocalWorkspaceMemoryProvider(tmp_path, workspace.id), tmp_path)
        scope = MemoryScopeContext(tenant_id=workspace.tenant_id, principal_user_id="temporal-u", workspace_id=workspace.id)
        yield db, service, scope
    engine.dispose()


def project(db, name="past", **overrides):
    stream = MemoryStream(id=f"s-{name}", aggregate_type="memory_atom", aggregate_id=name,
                          tenant_id="temporal-t", workspace_id="temporal-w", subject_user_id="temporal-u", current_version=1)
    db.add(stream)
    db.flush()
    event = MemoryEvent(event_id=f"e-{name}", stream_id=stream.id, stream_version=1, event_type="memory.atom_created",
                        producer="api", actor_type="user", actor_id="temporal-u", tenant_id="temporal-t",
                        workspace_id="temporal-w", subject_user_id="temporal-u", idempotency_key=name, payload_hash="hash",
                        occurred_at=OBSERVED)
    db.add(event)
    db.flush()
    semantics = TemporalNormalizer().normalize("2026年10月18日", observed_at=OBSERVED).as_dict()
    payload = {"memory_id": name, "title": "数据库实验", "content": "完成数据库索引实验。", "record_kind": "event_summary",
               "event_at": DAY.isoformat(), "temporal_semantics": semantics, **overrides}
    MemoryProjector(db).apply(event, payload)
    db.commit()
    db.expire_all()
    return db.scalar(select(MemorySearchDocument).where(MemorySearchDocument.target_id == name)), event, payload


def test_projection_uses_occurrence_not_updated_at_and_clears_metadata(store):
    db, service, _ = store
    document, event, payload = project(db)
    assert utc_datetime(document.event_at) == DAY
    assert document.metadata_json["temporal_semantics"]["original_expression"] == "2026年10月18日"
    assert service.list_views(window=OccurrenceWindow(DAY, DAY + timedelta(days=1)))[0].id == "past"
    assert service.list_views(window=OccurrenceWindow(OBSERVED, OBSERVED + timedelta(days=1))) == []
    MemoryProjector(db).apply(event, {**payload, "temporal_semantics": None, "event_at": "2026-10-19T00:00:00+08:00"})
    db.commit()
    assert document.metadata_json["temporal_semantics"] is None


def test_archived_historical_memory_only_competes_for_explicit_time_recall(store):
    db, service, scope = store
    document, _, _ = project(db, zone="archive", summary_eligibility="historical", valid_until="2026-10-19T00:00:00Z")
    assert document.status == "historical"
    retriever = MemoryHybridRetriever(db)
    assert not retriever.search(scope, "数据库", allowed_layers=frozenset({"L3"})).candidates
    result = retriever.search(scope, "2026年10月18日发生什么", allowed_layers=frozenset({"L3"}), window=OccurrenceWindow(DAY, DAY + timedelta(days=1)))
    assert [item.target_id for item in result.candidates] == ["past"]
    assert result.candidates[0].occurrence["event_at"] == utc_datetime(DAY).isoformat()
    assert len(service.timeline(window=OccurrenceWindow(DAY, DAY + timedelta(days=1)))["memories"]) == 1


def test_temporal_filter_does_not_bypass_principal_or_suppression(store):
    db, service, scope = store
    project(db, "foreign", subject_user_id="another-user")
    project(db, "suppressed", auto_recall_suppressed=True)
    project(db, "outside", event_at=(DAY + timedelta(days=2)).isoformat(), temporal_semantics=None)
    window = OccurrenceWindow(DAY, DAY + timedelta(days=1))
    assert not MemoryHybridRetriever(db).search(scope, "回忆", allowed_layers=frozenset({"L3"}), window=window).candidates
    assert service.timeline(window=window)["memories"] == []


@pytest.mark.parametrize("status", ["cancelled", "rescheduled", "lapsed_unverified", "expired"])
def test_projection_only_temporal_status_cannot_bypass_recall_eligibility(store, status):
    db, service, scope = store
    project(db, temporal_status=status)
    window = OccurrenceWindow(DAY, DAY + timedelta(days=1))
    retriever = MemoryHybridRetriever(db)
    assert not retriever.search(scope, "数据库", allowed_layers=frozenset({"L3"})).candidates
    assert not retriever.search(scope, "回忆", allowed_layers=frozenset({"L3"}), window=window).candidates
    assert service.timeline(window=window)["memories"] == []


def test_context_and_timeline_include_only_scoped_overlapping_episodes(store):
    db, service, scope = store
    for name, start, user in [("inside", DAY, "temporal-u"), ("outside", DAY + timedelta(days=2), "temporal-u"), ("foreign", DAY, "other")]:
        db.add(ConversationEpisode(id=name, stream_id=f"episode-stream-{name}", content_hash="hash", head_event_id=f"episode-event-{name}",
                                   tenant_id="temporal-t", workspace_id="temporal-w", subject_user_id=user,
                                   conversation_id=f"conversation-{name}", title=name, summary="完成了索引实验", status="closed",
                                   started_at=utc_datetime(start), ended_at=utc_datetime(start + timedelta(hours=1))))
    db.commit()
    timeline = service.timeline(window=OccurrenceWindow(DAY, DAY + timedelta(days=1)))
    assert [item["id"] for item in timeline["episodes"]] == ["inside"]
    result = ContextBuilder(db, MemoryRouter(MemoryHybridRetriever(db))).build(scope, ContextBuildRequest(query="2026年10月18日发生了什么", token_budget=4000))
    assert [item["episode_id"] for item in result.view.episodes] == ["inside"]
    assert "kind=time_window" in result.prompt_block
    assert "occurrence" in result.prompt_block


def test_legacy_context_uses_same_occurrence_window_without_current_fallback(store):
    from app.domain.schemas.management import MemoryCreateRequest

    _, service, _ = store
    past = service.create(MemoryCreateRequest(title="完成索引实验", content="我完成了数据库索引实验。", event_at=DAY, zone="archive"))
    service.create(MemoryCreateRequest(title="其他日期", content="我完成了网页实验。", event_at=DAY + timedelta(days=2)))
    result = service.effective_memory_package(query_text="2026年10月18日发生了什么")
    assert [item.id for item in result.effective_memories] == [past.id]
    assert "occurrence=" in result.prompt_block
    empty = service.effective_memory_package(query_text="2020年1月1日发生了什么")
    assert not empty.effective_memories
    assert "不用其他日期补齐" in empty.prompt_block


def test_runtime_allows_time_only_history_and_exposes_read_only_timeline():
    from unittest.mock import Mock
    from app.services.agent_runtime import AgentToolRuntime

    runtime = object.__new__(AgentToolRuntime)
    runtime.memory_tools = Mock()
    runtime._success = Mock(return_value=("ok", {}, []))
    runtime.memory_tools.search_conversation_history.return_value = {"hits": []}
    runtime.memory_tools.timeline.return_value = {"memories": [], "episodes": []}
    runtime._execute_memory_tool("search_conversation_history", {"query": "", "time_query": "2026年10月18日"}, chat_session_id="s")
    assert runtime.memory_tools.search_conversation_history.call_args.kwargs["query"] == ""
    assert runtime.memory_tools.search_conversation_history.call_args.kwargs["time_query"] == "2026年10月18日"
    runtime._execute_memory_tool("query_memory_timeline", {"time_query": "2026年10月18日"}, chat_session_id="s")
    runtime.memory_tools.timeline.assert_called_once()
    with pytest.raises(AppError):
        runtime._execute_memory_tool("query_memory_timeline", {}, chat_session_id="s")


@pytest.mark.parametrize("expression,status,statement", [
    ("昨天", "timeless", "昨天我完成了数据库索引实验。"),
    ("昨天", "completed", "昨天我完成了数据库索引实验。"),
    ("下个月", "scheduled", "下个月我计划做数据库索引实验。"),
])
def test_extraction_normalizes_both_past_facts_and_future_plans(store, monkeypatch, expression, status, statement):
    import hashlib
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app.core.config import get_settings
    from app.domain.models import ChatSession, MemoryDraft, MemoryEvidence, Message, WorkspaceSetting
    from app.services import memory_enhancement as enhancement

    db, service, _ = store
    db.add(WorkspaceSetting(workspace_id=service.workspace_id, key="memory.shared_policy", value={"workspace_enabled": True}))
    db.add(ChatSession(id="temporal-s", workspace_id=service.workspace_id, title="temporal", memory_enabled=True))
    db.add(Message(id="temporal-m", workspace_id=service.workspace_id, session_id="temporal-s", role="user", status="completed", content=statement, created_at=OBSERVED))
    db.add(MemoryEvidence(id="temporal-e", workspace_id=service.workspace_id, source_kind="user_statement", source_id="temporal-m",
                          message_id="temporal-m", authorship="user", profile_eligible=True, observed_at=OBSERVED, content_hash=hashlib.sha256(statement.encode()).hexdigest()))
    db.commit()
    model = SimpleNamespace(available=True, provider_id="fake", model_id="fake", remote_capability=False, last_usage={}, max_output_tokens=4096)
    model.generate_json = lambda *_: {"has_more": False, "memories": [{
        "operation": "CREATE", "memory_type": "event_summary", "title": "索引实验", "content": statement,
        "confidence": 0.99, "evidence_ids": ["temporal-e"], "source_quote": statement,
        "time_expression": expression, "temporal_status": status,
    }]}
    monkeypatch.setattr(enhancement, "model_provider_for_workspace", lambda *a, **kw: model)
    monkeypatch.setattr(enhancement, "memory_provider_for_workspace", lambda *a, **kw: service.provider)
    monkeypatch.setattr(enhancement, "BillingService", MagicMock())
    result = enhancement.extract_session_memories(db, service.workspace, "temporal-s", get_settings(), actor_id="temporal-u", force=True)
    assert result["status"] == "ok", result
    draft = db.scalar(select(MemoryDraft))
    assert draft is not None, result
    payload = draft.structured_payload
    assert payload["temporal_semantics"]["original_expression"] == expression
    assert datetime.fromisoformat(payload["temporal_semantics"]["observed_at"]) == OBSERVED
    assert payload.get("event_at")
    if status in {"timeless", "completed"}:
        assert not payload.get("valid_until")
        assert payload["temporal_status"] not in {"expired", "lapsed_unverified"}
