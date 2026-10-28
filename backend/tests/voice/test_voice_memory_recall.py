"""Per-turn memory recall for the voice runtime: budget, matching, injection.

These are unit-level on purpose.  The interesting behaviour is *when* a recall is
allowed to reach a request and *how long* a turn may wait for it -- neither needs
an app boot, and the API suite is expensive enough that this belongs here.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.models import (
    Message,
    MessageStreamEvent,
    MessageVersion,
    utc_now,
)
from app.services import chat as chat_module
from app.services import memory_enhancement
from app.services.voice_context import VoiceContextService, _last_user_text
from app.voice.embedded_memory import (
    DEFAULT_RECALL_WAIT_SECS,
    VoiceMemoryBridge,
    VoiceMemoryRecall,
    _install_memory_injection,
    answers_the_user_turn,
    last_user_text,
    voice_memory_llm_service_class,
)

BLOCK = "用户喜欢用类比学习。"


def _recall(query: str = "你好", block: str = BLOCK) -> VoiceMemoryRecall:
    return VoiceMemoryRecall(query=query, block=block, memory_count=1)


def _instant(voice_session_id: str, text: str) -> VoiceMemoryRecall:
    return _recall(query=text)


# --------------------------------------------------------------------- the bridge


def test_the_recall_for_this_turn_is_handed_to_its_request():
    async def scenario():
        bridge = VoiceMemoryBridge("vs_1", recall=_instant)
        bridge.start("你好")
        return await bridge.take("你好")

    result = asyncio.run(scenario())
    assert result is not None and result.block == BLOCK


def test_a_recall_is_consumed_once_and_never_reused_by_the_next_turn():
    async def scenario():
        bridge = VoiceMemoryBridge("vs_1", recall=_instant)
        bridge.start("你好")
        first = await bridge.take("你好")
        second = await bridge.take("你好")
        return first, second

    first, second = asyncio.run(scenario())
    assert first is not None and second is None


def test_a_recall_for_a_different_question_is_never_injected():
    async def scenario():
        bridge = VoiceMemoryBridge("vs_1", recall=_instant)
        bridge.start("我平时喜欢怎么学")
        return await bridge.take("今天天气怎么样")

    assert asyncio.run(scenario()) is None


def test_a_merged_asr_segment_still_matches_its_recall():
    """Finals can arrive in segments; the aggregator may merge them differently."""

    async def scenario():
        bridge = VoiceMemoryBridge("vs_1", recall=_instant)
        bridge.start("你好")
        return await bridge.take("你好，我想问一下记忆")

    assert asyncio.run(scenario()) is not None


def test_a_slow_recall_costs_the_budget_and_not_the_turn():
    gate = threading.Event()

    def slow(voice_session_id: str, text: str):
        gate.wait(timeout=5.0)
        return _recall(query=text)

    async def scenario():
        bridge = VoiceMemoryBridge("vs_1", wait_secs=0.05, recall=slow)
        bridge.start("你好")
        started = time.monotonic()
        result = await bridge.take("你好")
        elapsed = time.monotonic() - started
        bridge.invalidate()
        gate.set()
        return result, elapsed

    result, elapsed = asyncio.run(scenario())
    assert result is None
    assert elapsed < 0.5, elapsed


def test_a_turn_without_a_started_recall_does_not_wait():
    async def scenario():
        bridge = VoiceMemoryBridge("vs_1", recall=_instant)
        started = time.monotonic()
        result = await bridge.take("你好")
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(scenario())
    assert result is None and elapsed < DEFAULT_RECALL_WAIT_SECS


def test_invalidate_drops_the_recall_of_a_finished_turn():
    async def scenario():
        bridge = VoiceMemoryBridge("vs_1", recall=_instant)
        bridge.start("你好")
        await asyncio.sleep(0.05)
        bridge.invalidate()
        return await bridge.take("你好")

    assert asyncio.run(scenario()) is None


def test_a_barge_in_cancels_the_wait_instead_of_swallowing_it():
    """The wait runs inside the inference task, so interruption must propagate."""

    gate = threading.Event()

    def slow(voice_session_id: str, text: str):
        gate.wait(timeout=5.0)
        return _recall(query=text)

    async def scenario():
        bridge = VoiceMemoryBridge("vs_1", wait_secs=5.0, recall=slow)
        bridge.start("你好")
        task = asyncio.create_task(bridge.take("你好"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        bridge.invalidate()
        gate.set()

    asyncio.run(scenario())


def test_restarting_the_recall_keeps_only_the_latest_utterance():
    """A multi-segment final restarts the recall; the stale one must not win."""

    async def scenario():
        bridge = VoiceMemoryBridge("vs_1", recall=_instant)
        bridge.start("我想问")
        bridge.start("我想问一下记忆的事")
        return await bridge.take("我想问一下记忆的事")

    result = asyncio.run(scenario())
    assert result is not None and result.query == "我想问一下记忆的事"


def test_the_timeline_probe_is_warmed_at_construction_not_during_a_wait(monkeypatch):
    """Resolving the [VT] probe inside the wait window once ate the whole budget.

    The marker lives in a module that imports pipecat, and that import blocks the
    event loop: done lazily on the first turn it made a 0.1s wait time out on an
    instantaneous recall.  Warming it at construction is the fix, and this is the
    assertion that keeps it warmed.
    """

    import app.voice.embedded_memory as memory_mod

    resolved: list[int] = []
    real = memory_mod._probe
    monkeypatch.setattr(
        memory_mod, "_probe", lambda: (resolved.append(1), real())[1]
    )

    VoiceMemoryBridge("vs_1", recall=_instant)

    assert resolved == [1]


# ---------------------------------------------------------------- request shaping


def test_last_user_text_reads_both_string_and_partial_content():
    assert last_user_text([{"role": "system", "content": "s"}, {"role": "user", "content": "你好"}]) == "你好"
    assert (
        last_user_text([{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}])
        == "a b"
    )
    assert last_user_text([{"role": "assistant", "content": "x"}]) == ""
    assert last_user_text([]) == ""


def test_only_the_request_that_answers_the_users_turn_gets_memory():
    assert answers_the_user_turn([{"role": "user", "content": "你好"}]) is True
    # A background task result is appended as a system message.
    assert answers_the_user_turn(
        [{"role": "user", "content": "你好"}, {"role": "system", "content": "后台任务已完成"}]
    ) is False
    # A tool round ends with the tool result.
    assert answers_the_user_turn(
        [{"role": "user", "content": "你好"}, {"role": "tool", "content": "{}"}]
    ) is False
    assert answers_the_user_turn([]) is False


def test_the_memory_message_lands_with_the_instructions():
    params = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "你好"},
        ]
    }
    _install_memory_injection(params, _recall())
    assert [item["role"] for item in params["messages"]] == ["system", "system", "user"]
    assert BLOCK in params["messages"][1]["content"]


def _memory_service(bridge: VoiceMemoryBridge):
    """The tutor LLM service wired to a bridge, with its HTTP client stubbed."""

    pytest.importorskip("pipecat")
    service_class = voice_memory_llm_service_class()
    service = service_class(
        api_key="test",
        base_url="http://127.0.0.1:9",
        memory_bridge=bridge,
        settings=service_class.Settings(model="tutor", system_instruction="你是导师"),
    )
    calls: list[dict] = []

    class _Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return object()

    service._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions())
    )
    return service, calls


def test_the_request_carries_the_recall_without_touching_the_shared_context():
    from pipecat.processors.aggregators.llm_context import LLMContext

    bridge = VoiceMemoryBridge("vs_1", recall=_instant)
    service, calls = _memory_service(bridge)
    context = LLMContext(messages=[{"role": "user", "content": "我喜欢怎样学习？"}])
    before = [dict(item) for item in context.get_messages()]

    async def scenario():
        bridge.start("我喜欢怎样学习？")
        # Request isolation is tested with a ready recall, independently of
        # the separately tested real-time deadline and thread scheduling.
        await bridge._task
        await service.get_chat_completions(context)

    asyncio.run(scenario())

    messages = calls[0]["messages"]
    assert calls[0]["model"] == "tutor"
    assert messages[0] == {"role": "system", "content": "你是导师"}
    assert BLOCK in messages[1]["content"]
    assert messages[-1] == {"role": "user", "content": "我喜欢怎样学习？"}
    # The shared context is what later turns are built from: it must be untouched.
    assert [dict(item) for item in context.get_messages()] == before


def test_a_background_result_does_not_steal_the_turns_recall():
    """The result bridge runs the tutor with an appended system message."""

    from pipecat.processors.aggregators.llm_context import LLMContext

    bridge = VoiceMemoryBridge("vs_1", recall=_instant)
    service, calls = _memory_service(bridge)

    async def scenario():
        bridge.start("我喜欢怎样学习？")
        await service.get_chat_completions(
            LLMContext(
                messages=[
                    {"role": "user", "content": "我喜欢怎样学习？"},
                    {"role": "system", "content": "后台任务已完成"},
                ]
            )
        )
        await service.get_chat_completions(
            LLMContext(messages=[{"role": "user", "content": "我喜欢怎样学习？"}])
        )

    asyncio.run(scenario())

    assert len(calls) == 2
    assert all(BLOCK not in str(item.get("content")) for item in calls[0]["messages"])
    assert any(BLOCK in str(item.get("content")) for item in calls[1]["messages"])


def test_successive_voice_requests_send_prior_dialogue_to_the_provider():
    """Capture the actual service request after Pipecat's provider conversion."""
    from pipecat.processors.aggregators.llm_context import LLMContext

    service, calls = _memory_service(VoiceMemoryBridge("vs_1", recall=_instant))
    context = LLMContext(messages=[
        {"role": "system", "content": "你是导师"},
        {"role": "user", "content": "我叫林川。"},
    ])
    turns = [
        ("你好，林川。", "我叫什么？"),
        ("你叫林川。", "讲一个图书馆的故事。"),
        ("图书管理员在深夜寻找一本书。", "用短剧那种风格"),
        ("管理员：书不在这里。", "对吧？"),
    ]

    async def scenario():
        await service.get_chat_completions(context)
        for answer, query in turns:
            context.get_messages().extend([
                {"role": "assistant", "content": answer},
                {"role": "user", "content": query},
            ])
            before = [dict(item) for item in context.get_messages()]
            await service.get_chat_completions(context)
            assert context.get_messages() == before

    asyncio.run(scenario())
    assert len(calls) == 5
    for call, (answer, query) in zip(calls[1:], turns):
        messages = call["messages"]
        assert {"role": "assistant", "content": answer} in messages
        assert messages[-1] == {"role": "user", "content": query}
        assert sum(item["role"] == "user" for item in messages) >= 2
    assert {"role": "user", "content": "我叫林川。"} in calls[1]["messages"]
    assert [item["content"] for item in calls[-1]["messages"] if item["role"] == "user"] == [
        "我叫什么？", "讲一个图书馆的故事。", "用短剧那种风格", "对吧？",
    ]


def test_topic_change_preserves_dialogue_without_old_task_or_error_injection(monkeypatch):
    from pipecat.processors.aggregators.llm_context import LLMContext
    from app.voice.conversation_control import VoiceConversationPolicy

    query = "现在讲一个童话故事。"
    old_error = "用户以前把 O(n) 和 O(log n) 搞反。"
    bridge = VoiceMemoryBridge(
        "vs_1", recall=lambda _session, text: _recall(query=text, block=old_error)
    )
    service, calls = _memory_service(bridge)
    service._task_session_id = "vs_1"
    service.conversation_policy = VoiceConversationPolicy({"focus_task_ids": ["old-task"]})
    monkeypatch.setattr(
        "app.voice.task_context.load_session",
        lambda *_: pytest.fail("a new topic must not read old task records"),
    )
    context = LLMContext(messages=[
        {"role": "system", "content": "你是导师"},
        {"role": "user", "content": "我喜欢短故事。"},
        {"role": "assistant", "content": "好，接下来讲短一些。"},
        {"role": "user", "content": query},
    ])

    async def scenario():
        bridge.start(query)
        await bridge._task
        await service.get_chat_completions(context)

    asyncio.run(scenario())
    messages = calls[0]["messages"]
    assert {"role": "user", "content": "我喜欢短故事。"} in messages
    assert {"role": "assistant", "content": "好，接下来讲短一些。"} in messages
    assert not any("[voice-task-data]" in str(item.get("content", "")) for item in messages)
    assert not any(old_error in str(item.get("content", "")) for item in messages)


# ------------------------------------------------------------- the memory read path


def test_voice_memory_block_falls_back_to_the_legacy_loader_outside_events_mode(monkeypatch):
    fake = SimpleNamespace(
        memory_context_loader=lambda session_id, query_text=None, node_ids=None, prompt_token_budget=None: "LEGACY",
        _memory_prompt_token_budget=lambda: 1234,
        _build_v2_memory_context=lambda *a, **k: ("V2", {"context_build_id": "ctx_1"}),
    )
    monkeypatch.setattr(
        chat_module, "get_settings", lambda: SimpleNamespace(memory_read_mode="shadow")
    )
    block, telemetry = chat_module.ChatService.voice_memory_block(fake, "sess", "问题")
    assert block == "LEGACY"
    assert telemetry["read_mode"] == "shadow"


def test_voice_memory_block_uses_the_event_builder_in_events_mode(monkeypatch):
    fake = SimpleNamespace(
        memory_context_loader=lambda *a, **k: "LEGACY",
        _memory_prompt_token_budget=lambda: 1234,
        _build_v2_memory_context=lambda *a, **k: ("V2", {"context_build_id": "ctx_1"}),
    )
    monkeypatch.setattr(
        chat_module, "get_settings", lambda: SimpleNamespace(memory_read_mode="events", memory_context_builder_v2=True)
    )
    block, telemetry = chat_module.ChatService.voice_memory_block(fake, "sess", "问题")
    assert block == "V2"
    assert telemetry["context_build_id"] == "ctx_1"


def test_the_pinned_snapshot_uses_the_newest_user_turn_as_its_query():
    timeline = [
        SimpleNamespace(role="user", content="第一句"),
        SimpleNamespace(role="assistant", content="回答"),
        SimpleNamespace(role="user", content=" 最近这句 "),
    ]
    assert _last_user_text(timeline) == "最近这句"
    assert _last_user_text([]) == ""


# ------------------------------------------------------------------ write side


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


class _FakeChat:
    """Only what ``_record_message_completion`` touches."""

    def __init__(self, db, workspace_id: str = "ws_1") -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.events: list[dict] = []

    def _append_event(self, **kwargs):
        self.events.append(kwargs)
        self.db.add(
            MessageStreamEvent(
                id=str(uuid4()),
                workspace_id=self.workspace_id,
                session_id=kwargs["session_id"],
                message_id=kwargs["message_id"],
                message_version_id=kwargs["message_version_id"],
                part_id=kwargs["part_id"],
                sequence=kwargs["sequence"],
                event_type=kwargs["event_type"],
                payload=kwargs["payload"],
                created_at=utc_now(),
            )
        )
        self.db.commit()
        return SimpleNamespace(id="ev_1")


def _assistant_message(db, *, content: str = "回答") -> tuple[Message, MessageVersion]:
    message = Message(
        id=str(uuid4()),
        workspace_id="ws_1",
        session_id="cs_1",
        role="assistant",
        status="completed",
        content=content,
        provider_trace={"voice": True, "voice_turn": True, "voice_turn_id": "vt_1"},
    )
    db.add(message)
    db.flush()
    version = MessageVersion(
        id=str(uuid4()),
        workspace_id="ws_1",
        message_id=message.id,
        version=1,
        status="completed",
    )
    db.add(version)
    db.commit()
    return message, version


def _event_count(db) -> int:
    return int(db.scalar(select(func.count()).select_from(MessageStreamEvent)) or 0)


def test_a_voice_turn_records_one_message_completed_event(db):
    message, version = _assistant_message(db)
    service = VoiceContextService(_FakeChat(db))

    service._record_message_completion(message, message_version_id=version.id)

    assert _event_count(db) == 1
    assert service.chat.events[0]["event_type"] == "message.completed"
    assert service.chat.events[0]["message_id"] == message.id


def test_the_completion_event_is_not_written_twice(db):
    message, version = _assistant_message(db)
    service = VoiceContextService(_FakeChat(db))

    service._record_message_completion(message, message_version_id=version.id)
    service._record_message_completion(message, message_version_id=version.id)

    assert _event_count(db) == 1


def test_the_completion_event_is_found_again_by_message_when_the_version_is_unknown(db):
    """A replayed finalize has no version in hand; it must not double-write."""

    message, _version = _assistant_message(db)
    service = VoiceContextService(_FakeChat(db))

    service._record_message_completion(message)
    service._record_message_completion(message)

    assert _event_count(db) == 1


def test_a_turn_without_an_answer_records_no_completion_event(db):
    message, version = _assistant_message(db, content="   ")
    service = VoiceContextService(_FakeChat(db))

    service._record_message_completion(message, message_version_id=version.id)

    assert _event_count(db) == 0


def test_a_failing_event_write_never_escapes(db):
    message, version = _assistant_message(db)
    service = VoiceContextService(_FakeChat(db))

    def boom(**kwargs):
        raise RuntimeError("stream event write failed")

    service.chat._append_event = boom
    service._record_message_completion(message, message_version_id=version.id)  # must not raise


# ------------------------------------------------------------------ provenance

def test_a_spoken_message_is_labelled_voice_and_a_typed_one_is_not():
    spoken = SimpleNamespace(provider_trace={"voice": True, "voice_turn": True})
    typed = SimpleNamespace(provider_trace={"voice_turn": False})
    assert memory_enhancement._message_interaction_surface(spoken) == "voice"
    assert memory_enhancement._message_interaction_surface(typed) == "text"
    assert memory_enhancement._message_interaction_surface(SimpleNamespace(provider_trace=None)) == "text"


def test_provenance_surfaces_stay_unlabelled_for_text_only_evidence():
    assert memory_enhancement._provenance_surfaces(["e1"], {"e1": "text"}) == {}
    assert memory_enhancement._provenance_surfaces([], {}) == {}
    assert memory_enhancement._provenance_surfaces(["e1"], {"e1": "voice"}) == {
        "surfaces": ["voice"]
    }
    assert memory_enhancement._provenance_surfaces(
        ["e1", "e2"], {"e1": "voice", "e2": "text"}
    ) == {"surfaces": ["text", "voice"]}


# --------------------------------------------------------------- the prefetch trigger


class _FakeTurnApi:
    """Turn layer that records instead of persisting (same shape as the real one)."""

    def __init__(self) -> None:
        self.accepted: list[dict] = []

    def interrupt_turn(self, voice_session_id, turn_id, **kwargs):
        return {"turn_id": turn_id, "status": "interrupted"}

    def finalize_turn(self, voice_session_id, turn_id, *args, **kwargs):
        return {"turn_id": turn_id, "status": "finalized"}

    def open_turn(self, voice_session_id):
        return None

    def accept_turn(self, voice_session_id, text, **kwargs):
        self.accepted.append({"text": text, **kwargs})
        return {"turn_id": f"turn_{len(self.accepted)}", "user_text": text}


def _journal(monkeypatch):
    from app.voice import journal as journal_mod

    emitted: list[tuple[str, dict]] = []

    def _emit_event_line(voice_session_id, event_type, **kwargs):
        emitted.append((event_type, kwargs))
        return {"type": event_type, "seq": len(emitted)}

    fake = _FakeTurnApi()
    monkeypatch.setattr(journal_mod, "emit_event", _emit_event_line)
    for name in ("interrupt_turn", "finalize_turn", "open_turn", "accept_turn"):
        monkeypatch.setattr(journal_mod.turn_api, name, getattr(fake, name))
    return journal_mod.VoiceTurnJournal("vs_recall", idle_timeout_secs=30.0), emitted


def test_the_journal_reports_the_users_words_as_soon_as_they_exist(monkeypatch):
    journal, _emitted = _journal(monkeypatch)
    seen: list[str] = []
    journal.set_user_text_hook(seen.append)

    asyncio.run(journal.user_final("我想问一下记忆"))

    assert seen == ["我想问一下记忆"]


def test_a_merged_segment_reports_the_whole_question_again(monkeypatch):
    """The recall must be restarted for the longer utterance, not left on a prefix."""

    journal, _emitted = _journal(monkeypatch)
    seen: list[str] = []
    journal.set_user_text_hook(seen.append)

    asyncio.run(journal.user_final("我想问"))
    asyncio.run(journal.user_final("一下记忆"))

    assert seen == ["我想问", "我想问一下记忆"]


def test_a_failing_recall_hook_never_breaks_the_journal(monkeypatch):
    journal, _emitted = _journal(monkeypatch)

    def boom(_text: str) -> None:
        raise RuntimeError("recall exploded")

    journal.set_user_text_hook(boom)
    asyncio.run(journal.user_final("你好"))
    assert journal._turn is not None and journal._turn.user_text == "你好"

    # The journal keeps serving the next utterance with a healthy hook.
    seen: list[str] = []
    journal.set_user_text_hook(seen.append)
    asyncio.run(journal.user_final("再问一句"))
    assert seen == ["你好再问一句"]


def test_a_typed_message_also_starts_the_recall(monkeypatch):
    """The typed branch sets the turn's words without going through the funnel."""

    from app.voice import journal as journal_mod

    journal, _emitted = _journal(monkeypatch)
    seen: list[str] = []
    journal.set_user_text_hook(seen.append)

    async def scenario():
        # An open turn whose words the control plane has not filled in yet.
        journal._turn = journal_mod._TurnState(turn_id="turn_typed")
        await journal.user_typed("打字问一句")

    asyncio.run(scenario())

    assert seen == ["打字问一句"]


# ------------------------------------------------------- the recall plumbing itself


class _NullSession:
    """Stands in for the read session the recall commits its trace on."""

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def _stub_recall_environment(monkeypatch, block="MEM", *, boom=False, memory_count=2):
    from app.core import config as config_mod
    from app.core import database as database_mod
    from app.services import chat_service_factory as factory_mod
    from app.voice import events as events_mod

    handle = SimpleNamespace(
        workspace_id="ws_1",
        owner_user_id="u_1",
        chat_session_id="cs_1",
        model_id="m_1",
        provider_id="p_1",
        max_thinking_mode="off",
    )

    calls: list[dict] = []

    class _Chat:
        def voice_memory_block(self, session_id, query, token_budget=None, agent_id=None):
            calls.append(
                {
                    "session_id": session_id,
                    "query": query,
                    "token_budget": token_budget,
                    "agent_id": agent_id,
                }
            )
            if boom:
                raise RuntimeError("retrieval exploded")
            return block, {"context_build_id": "ctx_9", "memory_count": memory_count}

    monkeypatch.setattr(events_mod, "load_session", lambda sid: handle)
    monkeypatch.setattr(
        database_mod,
        "SessionLocal",
        lambda: contextlib.nullcontext(_NullSession()),
    )
    monkeypatch.setattr(factory_mod, "build_voice_chat_service", lambda *a, **k: _Chat())
    monkeypatch.setattr(config_mod, "get_settings", lambda: SimpleNamespace())
    return calls


def test_recall_returns_the_block_and_its_provenance(monkeypatch):
    from app.voice.embedded_memory import recall_voice_memory

    calls = _stub_recall_environment(monkeypatch)
    result = recall_voice_memory("vs_1", "  Rust 所有权  ")

    assert result is not None
    assert result.query == "Rust 所有权"
    assert result.block == "MEM"
    assert result.context_build_id == "ctx_9"
    assert result.memory_count == 2
    # The recall labels its retrieval, so voice reads can be told from typed ones.
    assert calls[0]["agent_id"] == "voice"


def test_recall_degrades_to_nothing_when_the_read_path_fails(monkeypatch):
    from app.voice.embedded_memory import recall_voice_memory

    _stub_recall_environment(monkeypatch, boom=True)
    assert recall_voice_memory("vs_1", "Rust 所有权") is None


def test_recall_skips_an_empty_result_and_an_empty_question(monkeypatch):
    from app.voice.embedded_memory import recall_voice_memory

    _stub_recall_environment(monkeypatch, block="   ")
    assert recall_voice_memory("vs_1", "Rust 所有权") is None
    assert recall_voice_memory("vs_1", "   ") is None
    assert recall_voice_memory("", "Rust 所有权") is None


# --------------------------------------------- the read path, against a real database


@pytest.fixture
def recall_db(monkeypatch):
    """Workspace + user + chat session + one remembered fact, in memory.

    ``_write_shadow_telemetry`` is stubbed out: it spawns a daemon thread with its
    own session bound to the same connection, and that thread races the in-memory
    engine's teardown (observed as a Windows access violation, not a test
    failure).  It is a telemetry side channel with its own tests; the retrieval
    path under test is untouched.
    """

    from app.domain.memory_event_models import MemorySearchDocument
    from app.domain.models import ChatSession, User, Workspace
    from app.services.chat import ChatService

    monkeypatch.setattr(
        ChatService, "_write_shadow_telemetry", lambda self, *a, **k: None
    )

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
                id="ws-recall",
                tenant_id="tenant-recall",
                owner_user_id="u-recall",
                name="voice recall",
            )
        )
        session.add(
            User(
                id="u-recall",
                tenant_id="tenant-recall",
                username="tutor",
                username_normalized="tutor",
                password_hash="x",
            )
        )
        session.add(
            ChatSession(
                id="cs-recall",
                workspace_id="ws-recall",
                title="voice recall",
                memory_enabled=True,
            )
        )
        from app.domain.models import WorkspaceSetting
        session.add(WorkspaceSetting(workspace_id="ws-recall", key="memory.shared_policy",
            value={"workspace_enabled": True, "workspace_recall_enabled": True}))
        session.add(
            MemorySearchDocument(
                id="doc-recall",
                target_type="memory",
                target_id="mem-recall",
                target_version=1,
                memory_layer="L3",
                memory_type="semantic_memory",
                subject="Rust 所有权",
                content="用户说 Rust 的所有权规则他早就懂了，别再从零讲。",
                tenant_id="tenant-recall",
                subject_user_id="u-recall",
                workspace_id="ws-recall",
                status="active",
                sensitivity="normal",
                importance=0.8,
                confidence=0.9,
                source_event_id="event-doc-recall",
                content_hash="hash-doc-recall",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def test_a_remembered_fact_reaches_the_voice_turn_that_asks_about_it(recall_db):
    """The point of the whole change: a spoken question recalls a typed memory.

    This runs the real read path -- ``build_voice_chat_service`` → the ordinary
    ContextBuilder → the memory projection -- so it fails if the voice entry
    point ever stops being the same one text chat uses.
    """

    from app.core.config import Settings
    from app.services.chat_service_factory import build_voice_chat_service

    chat = build_voice_chat_service(
        recall_db,
        workspace_id="ws-recall",
        actor_id="u-recall",
        settings=Settings(),
    )
    block, telemetry = chat.voice_memory_block("cs-recall", "Rust 所有权")

    assert "早就懂了" in block
    assert telemetry.get("context_build_id")


def test_the_voice_entry_point_adds_no_policy_of_its_own(recall_db):
    """Same query, same section as text chat: voice only decides *when* to ask.

    Note what this does not claim: relevance filtering belongs to the retriever,
    so an unrelated question can still surface a low-relevance memory -- exactly
    as it does in text chat.  Voice must not invent a second policy here, and this
    is the assertion that keeps it from doing so.
    """

    from app.core.config import Settings
    from app.services.chat_service_factory import build_voice_chat_service

    chat = build_voice_chat_service(
        recall_db,
        workspace_id="ws-recall",
        actor_id="u-recall",
        settings=Settings(),
    )
    block, telemetry = chat.voice_memory_block("cs-recall", "Rust 所有权")
    text_block, _telemetry = chat._build_v2_memory_context("cs-recall", "Rust 所有权")

    assert block == text_block
    assert telemetry["context_build_id"] == _telemetry["context_build_id"]
