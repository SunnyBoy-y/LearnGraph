"""Voice retrieval labels and the tutor's two new lookups.

Two things are asserted here that are easy to get wrong silently:

* the label a voice recall carries into ``memory_retrieval_traces`` (a trace with
  no label is indistinguishable from a typed turn), and
* that the label does **not** change what gets retrieved.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.memory_event_models import MemoryContextPackage, MemorySearchDocument
from app.domain.models import ChatSession, User, Workspace
from app.services.chat import ChatService
from app.services.chat_service_factory import build_voice_chat_service
from app.services.voice_context import VOICE_MEMORY_AGENT_ID
from app.voice.coordinator_adapter import _artifact_items
from app.voice.embedded_delegation import (
    dispatch_artifact_list,
    dispatch_memory_search,
)

FACT = "用户说 Rust 的所有权规则他早就懂了，别再从零讲。"


@pytest.fixture
def db(monkeypatch):
    """Workspace + user + chat session + one remembered fact, in memory.

    The telemetry write is done synchronously here: production runs it on a
    daemon thread with its own session, and that thread races the in-memory
    engine's teardown.  The writer itself is the real one -- only the threading
    is substituted, so the rows asserted below are the rows production writes.
    """

    from app.services.context_telemetry import ContextTelemetryWriter

    def _sync_telemetry(self, scope, request, view, telemetry):
        ContextTelemetryWriter(self.db).write(scope, request, view)
        self.db.commit()

    monkeypatch.setattr(ChatService, "_write_shadow_telemetry", _sync_telemetry)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        session.add(
            Workspace(
                id="ws-voice-tools",
                tenant_id="tenant-voice-tools",
                owner_user_id="u-voice-tools",
                name="voice tools",
            )
        )
        session.add(
            User(
                id="u-voice-tools",
                tenant_id="tenant-voice-tools",
                username="tutor",
                username_normalized="tutor",
                password_hash="x",
            )
        )
        session.add(
            ChatSession(
                id="cs-voice-tools",
                workspace_id="ws-voice-tools",
                title="voice tools",
                memory_enabled=True,
            )
        )
        from app.domain.models import WorkspaceSetting
        session.add(WorkspaceSetting(workspace_id="ws-voice-tools", key="memory.shared_policy",
            value={"workspace_enabled": True, "workspace_recall_enabled": True}))
        session.add(
            MemorySearchDocument(
                id="doc-voice-tools",
                target_type="memory",
                target_id="mem-voice-tools",
                target_version=1,
                memory_layer="L3",
                memory_type="semantic_memory",
                subject="Rust 所有权",
                content=FACT,
                tenant_id="tenant-voice-tools",
                subject_user_id="u-voice-tools",
                workspace_id="ws-voice-tools",
                status="active",
                sensitivity="normal",
                importance=0.8,
                confidence=0.9,
                source_event_id="event-voice-tools",
                content_hash="hash-voice-tools",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def _chat(db):
    return build_voice_chat_service(
        db,
        workspace_id="ws-voice-tools",
        actor_id="u-voice-tools",
        settings=__import__("app.core.config", fromlist=["Settings"]).Settings(),
    )


def _packages(db) -> list[MemoryContextPackage]:
    return list(
        db.scalars(
            select(MemoryContextPackage).where(
                MemoryContextPackage.workspace_id == "ws-voice-tools"
            )
        ).all()
    )


# ------------------------------------------------------------------- the label


def test_a_voice_recall_is_labelled_voice_where_it_is_recorded(db):
    _chat(db).voice_memory_block("cs-voice-tools", "Rust 所有权")

    rows = _packages(db)
    assert rows, "a recall must leave a record"
    assert {row.agent_id for row in rows} == {VOICE_MEMORY_AGENT_ID}


def test_a_text_turn_keeps_the_default_label(db):
    _chat(db)._build_v2_memory_context("cs-voice-tools", "Rust 所有权")

    assert {row.agent_id for row in _packages(db)} == {"main_agent"}


def test_the_retrieval_call_carries_the_label_in_both_places(db):
    """Scope *and* request, because the two telemetry surfaces read different ones.

    The context package takes ``request.agent_id``; a retrieval trace takes
    ``scope.agent_id``.  Labelling only one of them would leave the other surface
    unable to tell a spoken recall from a typed one.
    """

    chat = _chat(db)
    seen: dict[str, str] = {}
    real_build = chat.context_builder.build

    def spy(scope, request):
        seen["scope"] = scope.agent_id
        seen["request"] = request.agent_id
        return real_build(scope, request)

    chat.context_builder.build = spy
    chat.voice_memory_block("cs-voice-tools", "Rust 所有权")

    assert seen == {"scope": VOICE_MEMORY_AGENT_ID, "request": VOICE_MEMORY_AGENT_ID}


def test_the_label_does_not_change_what_is_retrieved(db):
    """The label is telemetry only: same query, same block either way."""

    chat = _chat(db)
    labelled, _telemetry = chat.voice_memory_block("cs-voice-tools", "Rust 所有权")
    unlabelled, _telemetry = chat.voice_memory_block(
        "cs-voice-tools", "Rust 所有权", agent_id="main_agent"
    )

    assert labelled == unlabelled
    assert "早就懂了" in labelled


# ------------------------------------------------------------- the memory lookup


def test_the_memory_lookup_returns_items_the_tutor_can_speak_from(db):
    result = _chat(db).voice_memory_search("cs-voice-tools", "Rust 所有权")

    assert result["count"] >= 1
    item = result["items"][0]
    assert item["memory_id"] == "mem-voice-tools"
    assert "早就懂了" in item["content"]
    assert item["confidence"] == pytest.approx(0.9)


def test_the_memory_lookup_answers_an_empty_query_without_raising(db):
    result = _chat(db).voice_memory_search("cs-voice-tools", "   ")

    assert result == {"items": [], "count": 0, "reason": "empty_query"}


def test_the_memory_lookup_degrades_instead_of_raising(db, monkeypatch):
    chat = _chat(db)

    def boom(*args, **kwargs):
        raise RuntimeError("retrieval exploded")

    monkeypatch.setattr(chat, "_build_v2_memory_package", boom)
    result = chat.voice_memory_search("cs-voice-tools", "Rust 所有权")

    assert result["items"] == [] and result["degraded"] is True


# ---------------------------------------------------------- the tool dispatchers


class _Port:
    def __init__(self, *, delay: float = 0.0, boom: bool = False) -> None:
        self.delay = delay
        self.boom = boom
        self.calls: list[tuple] = []

    async def search_memory(self, query, *, limit=5):
        self.calls.append(("search_memory", query, limit))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.boom:
            raise RuntimeError("port exploded")
        return {"items": [{"memory_id": "m1"}], "count": 1}

    async def list_artifacts(self, query="", *, limit=5):
        self.calls.append(("list_artifacts", query, limit))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.boom:
            raise RuntimeError("port exploded")
        return {"items": [{"id": "c1", "kind": "card"}], "count": 1}


def test_the_memory_lookup_has_a_hard_ceiling():
    port = _Port(delay=0.5)

    result = asyncio.run(
        dispatch_memory_search(port, "Rust 所有权", timeout_secs=0.05)
    )

    assert result["reason"] == "memory_search_timeout"
    assert result["items"] == []


def test_a_failing_lookup_reports_a_reason_and_never_raises():
    result = asyncio.run(dispatch_memory_search(_Port(boom=True), "Rust 所有权"))

    assert result["reason"] == "memory_search_failed"
    assert result["items"] == []


def test_an_empty_query_never_reaches_the_port():
    port = _Port()
    result = asyncio.run(dispatch_memory_search(port, "   "))

    assert result["reason"] == "empty_query"
    assert port.calls == []


def test_without_a_port_the_tools_report_unavailable():
    assert asyncio.run(dispatch_memory_search(None, "x"))["reason"] == (
        "voice_tools_unavailable"
    )
    assert asyncio.run(dispatch_artifact_list(None))["reason"] == (
        "voice_tools_unavailable"
    )


def test_the_artifact_listing_passes_through_and_is_bounded():
    port = _Port()
    result = asyncio.run(dispatch_artifact_list(port, "复习", limit=99))

    assert result["count"] == 1
    assert port.calls == [("list_artifacts", "复习", 20)]


# --------------------------------------------------------------- artifact shaping


def _card(title: str, *, minutes: int, kind: str = "magic_card") -> dict:
    stamp = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc) + timedelta(
        minutes=minutes
    )
    return {
        "card_id": f"card-{title}",
        "title": title,
        "status": "draft",
        "card_type": kind,
        "interactive": True,
        "version_count": 2,
        "chat_session_id": "cs-voice-tools",
        "updated_at": stamp,
    }


def _collection(name: str, *, minutes: int):
    artifact = SimpleNamespace(
        id=f"art-{name}",
        name=name,
        status="active",
        created_at=datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
        + timedelta(minutes=minutes),
    )
    return (artifact, 3)


def test_artifacts_from_both_halves_of_the_page_are_listed_newest_first():
    result = _artifact_items(
        [_card("旧卡片", minutes=0), _card("新卡片", minutes=10)],
        [_collection("集合", minutes=5)],
        needle="",
        top_k=10,
    )

    assert [item["title"] for item in result["items"]] == ["新卡片", "集合", "旧卡片"]
    assert [item["kind"] for item in result["items"]] == ["card", "collection", "card"]
    assert result["count"] == 3


def test_the_artifact_query_narrows_by_title_and_ignores_case():
    result = _artifact_items(
        [_card("复习卡片", minutes=0), _card("其它", minutes=1)],
        [],
        needle="复习",
        top_k=10,
    )

    assert [item["title"] for item in result["items"]] == ["复习卡片"]


def test_the_artifact_listing_respects_the_cap():
    cards = [_card(f"卡片{i}", minutes=i) for i in range(5)]

    result = _artifact_items(cards, [], needle="", top_k=2)

    assert len(result["items"]) == 2 and result["count"] == 2


# ------------------------------------------------------------- tools on the tutor


def _tutor_with(port):
    from app.voice.embedded_bot import TutorAgent
    from app.voice.embedded_memory import voice_memory_llm_service_class

    service_class = voice_memory_llm_service_class()
    llm = service_class(
        api_key="test",
        base_url="http://127.0.0.1:9",
        settings=service_class.Settings(model="tutor", system_instruction="你是导师"),
    )
    return TutorAgent(llm=llm, voice_session_id="vs_1", delegation_port=port)


class _Params:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def result_callback(self, payload):
        self.payloads.append(payload)


def test_the_tutor_advertises_both_lookups_only_with_a_port():
    names = {getattr(item, "__name__", "") for item in _tutor_with(_Port()).build_tools()}

    assert {"search_memory", "list_artifacts"} <= names
    assert _tutor_with(None).build_tools() == []


def test_the_tutor_tools_return_what_the_port_gave():
    port = _Port()
    agent = _tutor_with(port)

    async def scenario():
        params = _Params()
        await agent.search_memory(params, query="Rust 所有权")
        await agent.list_artifacts(params)
        return params.payloads

    payloads = asyncio.run(scenario())

    assert payloads[0]["items"] == [{"memory_id": "m1"}]
    assert payloads[1]["items"] == [{"id": "c1", "kind": "card"}]
    assert [call[0] for call in port.calls] == ["search_memory", "list_artifacts"]
