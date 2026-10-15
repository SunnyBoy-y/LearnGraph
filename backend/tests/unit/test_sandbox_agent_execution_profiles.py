from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.providers.ports.model import ProviderStreamEvent
from app.services.sandbox_agent_executor import execute_subagent_job


@pytest.fixture(autouse=True)
def isolated_executor_store(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core import database
    from app.domain.models import ChatSession, User, Workspace

    engine = create_engine(f"sqlite:///{(tmp_path / 'executor.db').as_posix()}")
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(id="user-1", tenant_id="tenant-1", username="executor", username_normalized="executor", password_hash="test"))
        db.add(Workspace(id="workspace-1", tenant_id="tenant-1", owner_user_id="user-1", name="executor"))
        db.add(ChatSession(id="chat-1", workspace_id="workspace-1", title="executor"))
        db.commit()
    monkeypatch.setattr(database, "SessionLocal", factory)
    yield
    engine.dispose()


class FakeProvider:
    available = True
    remote_capability = False
    provider_id = "fake"
    model_id = "fake-model"
    thinking_mode = "medium"
    actual_reasoning_effort = "medium"

    def __init__(self, *, answer: str, tool_call: dict | None = None):
        self.answer = answer
        self.tool_call = tool_call
        self.calls = 0
        self.tools_seen: list[dict] | None = []

    def stream_chat(self, messages, *, tools=None):
        self.calls += 1
        self.tools_seen = tools
        if self.calls == 1 and self.tool_call is not None:
            yield ProviderStreamEvent(type="tool_calls", tool_calls=[self.tool_call])
            yield ProviderStreamEvent(type="completed")
            return
        yield ProviderStreamEvent(type="text_delta", content=self.answer)
        yield ProviderStreamEvent(type="completed")


class FakeToolRuntime:
    def __init__(self):
        self.executed: list[dict] = []

    def definitions_for_tools(self, *, tool_names, web_search_enabled, memory_enabled):
        assert tool_names is None
        assert web_search_enabled is True
        assert memory_enabled is False
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fetch_web_page",
                    "parameters": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                },
            },
        ]

    def execute(self, tool_call, *, allowed_domains, chat_session_id, disclosed_tool_names):
        self.executed.append(
            {
                "tool_call": tool_call,
                "allowed_domains": list(allowed_domains),
                "chat_session_id": chat_session_id,
                "disclosed_tool_names": set(disclosed_tool_names),
            }
        )
        return (
            '{"query":"q","results":[{"title":"Source","url":"https://example.com","snippet":"s"}]}',
            {"status": "completed", "query": "q", "result_count": 1},
            [{"title": "Source", "url": "https://example.com", "snippet": "s"}],
        )


def _settings():
    from app.core.config import Settings

    return Settings(
        sandbox_subagent_max_rounds=3,
        sandbox_subagent_max_seconds=30,
        sandbox_subagent_default_max_tokens=20_000,
        sandbox_subagent_default_max_cost_usd=1.0,
    )


def _job(*, role_key: str, tools, cancel_requested: bool = False):
    return SimpleNamespace(
        payload_json={
            "task_id": "task-1",
            "role_key": role_key,
            "tools": tools,
            "prompt": "research this",
            "budget": {},
            "requirement_version": 1,
        },
        workspace_id="workspace-1",
        owner_user_id="user-1",
        chat_session_id="chat-1",
        attempt=1,
        status="RUNNING",
        cancel_requested=cancel_requested,
    )


def _task(*, current_requirement_version: int = 1):
    return SimpleNamespace(
        id="task-1",
        status="RUNNING",
        spec_json={
            "requirement_version": 1,
            "current_requirement_version": current_requirement_version,
        },
    )


def _answer() -> str:
    return (
        "The answer.\n"
        '{"summary":"The answer.","findings":["fact"],"artifacts":[],"evidence":[],'
        '"acceptance":[{"criterion":"answer present","status":"passed"}],'
        '"risks":[],"limitations":[],"unresolved":[],"confidence":0.9}'
    )


def test_research_default_uses_real_runtime_search_and_records_sources():
    runtime = FakeToolRuntime()
    provider = FakeProvider(
        answer=_answer(),
        tool_call={
            "id": "call-1",
            "type": "function",
            "function": {"name": "search_web", "arguments": '{"query":"q"}'},
        },
    )
    outcome = execute_subagent_job(
        _settings(),
        _job(role_key="research", tools=None),
        _task(),
        provider=provider,
        tool_runtime=runtime,
    )

    assert outcome.status == "SUCCEEDED"
    assert [item["function"]["name"] for item in provider.tools_seen] == [
        "search_web",
        "fetch_web_page",
    ]
    assert runtime.executed[0]["disclosed_tool_names"] == {"search_web", "fetch_web_page"}
    typed = outcome.deliverables["agent_result"]
    assert typed["task_type"] == "research"
    assert typed["sources"][0]["url"] == "https://example.com"


def test_empty_tool_list_stays_empty_and_does_not_fall_back():
    provider = FakeProvider(answer=_answer())
    outcome = execute_subagent_job(
        _settings(),
        _job(role_key="generic", tools=[]),
        _task(),
        provider=provider,
    )

    assert outcome.status == "SUCCEEDED"
    assert provider.tools_seen == []


def test_cancellation_is_cooperative_and_typed():
    provider = FakeProvider(answer=_answer())
    job = _job(role_key="reason", tools=[])
    job.cancel_requested = True

    outcome = execute_subagent_job(
        _settings(),
        job,
        _task(),
        provider=provider,
    )

    assert outcome.status == "CANCELLED"
    assert outcome.deliverables["agent_result"]["status"] == "cancelled"
    assert provider.calls == 0


def test_superseded_requirement_returns_stale_without_model_call():
    provider = FakeProvider(answer=_answer())
    outcome = execute_subagent_job(
        _settings(),
        _job(role_key="reason", tools=[]),
        _task(current_requirement_version=2),
        provider=provider,
    )

    assert outcome.status == "STALE"
    assert outcome.deliverables["agent_result"]["status"] == "stale"
    assert provider.calls == 0


class FakeDB:
    def flush(self):
        return None

    def close(self):
        return None


def test_unsupported_thinking_mode_falls_back_and_records_effective(monkeypatch):
    from app.providers.local.model import UnavailableModelProvider
    import app.core.database as database_module
    import app.providers.factory as factory_module

    provider = FakeProvider(answer=_answer())
    attempts: list[str] = []

    def fake_provider_for_workspace(_db, _workspace_id, _settings, *, thinking_mode):
        attempts.append(thinking_mode)
        if thinking_mode == "xhigh":
            return UnavailableModelProvider(
                "The selected model does not support thinking mode 'xhigh'"
            )
        provider.thinking_mode = thinking_mode
        provider.actual_reasoning_effort = thinking_mode
        return provider

    monkeypatch.setattr(database_module, "SessionLocal", FakeDB)
    monkeypatch.setattr(factory_module, "model_provider_for_workspace", fake_provider_for_workspace)
    # This test isolates provider fallback with a deliberately minimal DB port.
    # Monetary persistence is covered by the real-session native billing tests.
    monkeypatch.setattr("app.services.sandbox_agent_executor.BillingService.preflight_model_call", lambda *_args, **_kwargs: None)
    job = _job(role_key="reason", tools=[])
    job.payload_json["thinking_mode"] = "xhigh"

    outcome = execute_subagent_job(_settings(), job, _task())

    assert outcome.status == "SUCCEEDED"
    assert attempts[:2] == ["xhigh", "high"]
    result = outcome.deliverables["agent_result"]
    assert result["thinking_mode_requested"] == "xhigh"
    assert result["thinking_mode_effective"] == "high"
