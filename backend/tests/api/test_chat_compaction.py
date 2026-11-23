from __future__ import annotations

import json

from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.models import ChatGoalRun, ChatSession, ContextSummary, Message
from app.providers.ports.model import ProviderStreamEvent
from app.providers.remote.openai import ProviderHTTPError
from app.services.chat import ChatService
from app.services.context_compaction import (
    IN_LOOP_SUMMARY_PREFIX,
    MICROCOMPACT_MARKER,
    REHYDRATION_PREFIX,
)


def _events(body: str) -> list[dict]:
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


def _start_session(client, register_user, auth_headers):
    token, ws, _, _ = register_user(prefix="cmp")
    headers = auth_headers(token, ws)
    resp = client.post("/api/v1/sessions", headers=headers, json={"title": "compaction suite"})
    assert resp.status_code in (200, 201), resp.text
    return headers, resp.json()["id"]


class CompactionTestProvider:
    available = True
    remote_capability = True
    provider_type = "openai"
    provider_id = "stub-provider"
    model_id = "stub-model"
    supports_structured_chat = True
    supports_agent_tools = True
    supports_sync_compaction = True
    thinking_mode = "off"
    actual_reasoning_effort = None
    search_route = "disabled"
    last_usage: dict = {}

    def __init__(
        self,
        *,
        context_window_tokens: int = 256_000,
        capabilities: dict | None = None,
        tool_rounds: int = 0,
        overflow_on_round: int | None = None,
    ):
        self.context_window_tokens = context_window_tokens
        self.capabilities = capabilities or {}
        self.tool_rounds = tool_rounds
        self.overflow_on_round = overflow_on_round
        self.calls = 0
        self.histories: list[list[dict]] = []
        self.summary_prompts: list[str] = []
        self._overflow_raised = False

    def stream_chat(self, messages, **kwargs):
        # Synchronous compaction helper calls stream_chat with a single user prompt
        if (
            len(messages) == 1
            and messages[0].role == "user"
            and (
                "execution handoff summary" in (messages[0].content or "")
                or "会话摘要器" in (messages[0].content or "")
                or "PRIORITY FOCUS FROM USER" in (messages[0].content or "")
            )
        ):
            self.summary_prompts.append(messages[0].content or "")
            yield ProviderStreamEvent(
                "text_delta",
                content="1. Primary Goal: compacted test summary\n2. Pending Work: continue",
            )
            yield ProviderStreamEvent("completed", finish_reason="stop")
            return

        self.calls += 1
        self.histories.append([m.as_payload() for m in messages])

        if self.overflow_on_round is not None and self.calls == self.overflow_on_round and not self._overflow_raised:
            self._overflow_raised = True
            raise ProviderHTTPError(
                "This model's maximum context length is exceeded (context_length_exceeded)",
                status_code=400,
            )

        if self.calls <= self.tool_rounds:
            yield ProviderStreamEvent(
                "tool_calls",
                tool_calls=[
                    {
                        "id": f"call_{self.calls}",
                        "type": "function",
                        "function": {
                            "name": "sandbox_read_file",
                            "arguments": json.dumps({"path": f"notes_{self.calls}.md"}),
                        },
                    }
                ],
            )
            yield ProviderStreamEvent("completed", finish_reason="tool_calls")
            return

        yield ProviderStreamEvent("text_delta", content="Compaction verified and task finished.")
        yield ProviderStreamEvent("completed", finish_reason="stop")


def _install_provider(monkeypatch, provider: CompactionTestProvider, *, tool_output_chars: int = 1200):
    from app.api.routers import chat as router

    executed: list[str] = []

    def factory(db, context, settings, **kwargs):
        svc = ChatService(
            db,
            context.workspace_id,
            context.principal.user_id,
            provider,
            settings=settings,
            retry_delays=(0,),
            retry_jitter_ratio=0,
        )
        svc._build_fast_preface_provider = lambda: None
        svc._agent_skill_package_instructions = lambda **kwargs: "test-route"
        svc._agent_tool_definitions = lambda *args, **kwargs: [
            {
                "type": "function",
                "function": {
                    "name": "sandbox_read_file",
                    "description": "Read a file inside the session sandbox.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            }
        ]

        def execute(call, domains, session_id, **kwargs):
            executed.append(call["id"])
            payload = {
                "status": "completed",
                "path": f"notes_{len(executed)}.md",
                "content": f"RESULT_{len(executed)}_" + ("Z" * tool_output_chars),
            }
            return json.dumps(payload), {"status": "completed"}, []

        svc._execute_agent_tool = execute
        return svc

    monkeypatch.setattr(router, "service", factory)
    return executed


def test_context_usage_includes_tool_definitions_and_078_default_ratio(
    client, register_user, auth_headers, monkeypatch
):
    provider = CompactionTestProvider(context_window_tokens=128_000)
    _install_provider(monkeypatch, provider)
    headers, sid = _start_session(client, register_user, auth_headers)

    chat_usage = client.get(
        f"/api/v1/sessions/{sid}/context-usage?agent_mode=false", headers=headers
    )
    assert chat_usage.status_code == 200, chat_usage.text
    chat_body = chat_usage.json()

    agent_usage = client.get(
        f"/api/v1/sessions/{sid}/context-usage?agent_mode=true", headers=headers
    )
    assert agent_usage.status_code == 200, agent_usage.text
    agent_body = agent_usage.json()

    assert chat_body["compaction_ratio"] == 0.8
    assert agent_body["compaction_ratio"] == 0.78
    assert agent_body["estimated_tokens"] > chat_body["estimated_tokens"]


def test_manual_compact_endpoint_supports_focus_and_force(
    client, register_user, auth_headers, monkeypatch
):
    provider = CompactionTestProvider(context_window_tokens=128_000)
    _install_provider(monkeypatch, provider)
    headers, sid = _start_session(client, register_user, auth_headers)

    # Create a few completed turns so there is history to compact
    for idx in range(3):
        resp = client.post(
            f"/api/v1/sessions/{sid}/messages/stream",
            headers=headers,
            json={"content": f"Turn {idx}: inspect SQLite lock tracebacks and Redis cache"},
        )
        assert resp.status_code == 200, resp.text
        assert any(e["type"] == "message.completed" for e in _events(resp.text))

    # Without force or focus on a small conversation, manual compact skips cleanly
    skipped_resp = client.post(f"/api/v1/sessions/{sid}/compact", headers=headers)
    assert skipped_resp.status_code == 200, skipped_resp.text
    skipped_body = skipped_resp.json()
    assert skipped_body["skipped"] is True
    assert skipped_body["reason"] == "context_too_small"

    # With focus + force, manual compact runs synchronous structured handoff summary
    focus_text = "保留 SQLite 锁报错堆栈与 Redis 缓存键名"
    forced_resp = client.post(
        f"/api/v1/sessions/{sid}/compact",
        headers=headers,
        json={"focus": focus_text, "force": True},
    )
    assert forced_resp.status_code == 200, forced_resp.text
    forced_body = forced_resp.json()
    assert forced_body["skipped"] is False
    assert forced_body["kind"] == "model"
    assert forced_body["source_message_count"] >= 1
    assert any(focus_text in p for p in provider.summary_prompts)

    with SessionLocal() as db:
        summaries = list(
            db.scalars(select(ContextSummary).where(ContextSummary.session_id == sid))
        )
        assert len(summaries) == 1
        assert summaries[0].kind == "model"


def test_reactive_overflow_compact_recovers_without_reexecuting_tools(
    client, register_user, auth_headers, monkeypatch
):
    # 5 tool rounds with bulky outputs, then overflow raised on the 6th provider call
    provider = CompactionTestProvider(
        context_window_tokens=128_000,
        tool_rounds=5,
        overflow_on_round=6,
    )
    executed = _install_provider(monkeypatch, provider, tool_output_chars=1200)
    headers, sid = _start_session(client, register_user, auth_headers)

    # Attach an active /goal (ChatGoalRun) to verify rehydration preserves goal state across compaction
    with SessionLocal() as db:
        session = db.get(ChatSession, sid)
        goal = ChatGoalRun(
            session_id=sid,
            workspace_id=session.workspace_id,
            actor_id="test-actor",
            objective="Inspect notes_1..5 and summarize",
            job_id="job-compact-1",
            status="active",
            rounds_used=2,
            max_rounds=8,
            summary="First notes collected",
        )
        db.add(goal)
        db.commit()

    resp = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "Inspect all five files and summarize.", "agent_mode": True},
    )
    assert resp.status_code == 200, resp.text
    events = _events(resp.text)
    event_types = [e["type"] for e in events]
    assert "context.compaction.completed" in event_types
    assert "message.completed" in event_types

    compaction_events = [e for e in events if e["type"] == "context.compaction.completed"]
    assert any(e["payload"]["reason"] == "reactive_context_overflow" for e in compaction_events)

    # Tools were executed exactly once each (no duplicate re-execution on overflow retry)
    assert executed == ["call_1", "call_2", "call_3", "call_4", "call_5"]

    # Retry history after overflow must be compacted/microcompacted and include rehydration
    retry_history = provider.histories[-1]
    serialized_retry = json.dumps(retry_history, ensure_ascii=False)
    assert (
        MICROCOMPACT_MARKER in serialized_retry
        or IN_LOOP_SUMMARY_PREFIX in serialized_retry
    )
    if IN_LOOP_SUMMARY_PREFIX in serialized_retry:
        assert REHYDRATION_PREFIX in serialized_retry
        assert "Inspect notes_1..5 and summarize" in serialized_retry

    # Verify protocol validity of retry history: every tool message matches a kept assistant tool_call
    declared_ids = {
        tc["id"]
        for m in retry_history
        if m.get("role") == "assistant" and m.get("tool_calls")
        for tc in m["tool_calls"]
    }
    for m in retry_history:
        if m.get("role") == "tool":
            assert m["tool_call_id"] in declared_ids

    completed_id = [e for e in events if e["type"] == "message.completed"][-1]["message_id"]
    with SessionLocal() as db:
        msg = db.get(Message, completed_id)
        assert msg.provider_trace.get("reactive_overflow_compactions") == 1


def test_proactive_in_loop_microcompact_and_split_turn_on_tight_budget(
    client, register_user, auth_headers, monkeypatch
):
    # Context window 6_000 (input_budget = 3_952): microcompact triggers at ~2_173, split-turn at ~3_082
    provider = CompactionTestProvider(
        context_window_tokens=6_000,
        tool_rounds=8,
    )
    executed = _install_provider(monkeypatch, provider, tool_output_chars=1_800)
    headers, sid = _start_session(client, register_user, auth_headers)

    resp = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "Run 8 inspection steps.", "agent_mode": True},
    )
    assert resp.status_code == 200, resp.text
    events = _events(resp.text)
    assert any(e["type"] == "message.completed" for e in events)
    assert len(executed) == 8

    completed_id = [e for e in events if e["type"] == "message.completed"][-1]["message_id"]
    with SessionLocal() as db:
        msg = db.get(Message, completed_id)
        trace = msg.provider_trace or {}
        assert (trace.get("microcompact_cleared") or 0) > 0
        # Either microcompact kept it below threshold or split-turn also ran if needed
        assert (trace.get("microcompact_cleared") or 0) >= 2
