"""Foreground tool contracts preserve authorization, evidence and cancellation."""
import asyncio
import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from app.voice.embedded_bot import _foreground_text_tool_schemas

NAMES = {"search_web", "fetch_web_page", "get_current_time", "get_weather", "get_news"}


def catalog(monkeypatch, runtime, journal):
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr("app.services.chat_service_factory.build_background_agent_tool_runtime", lambda *a, **kw: runtime)
    runtime.definitions = Mock(return_value=[{"function": {"name": name, "parameters": {"properties": {}, "required": []}}} for name in [*sorted(NAMES), "run_command"]])
    session = SimpleNamespace(workspace_id="workspace", owner_user_id="user", chat_session_id="chat")
    return {s.name: s for s in _foreground_text_tool_schemas(session, object(), journal)}


@pytest.mark.parametrize("name", sorted(NAMES))
@pytest.mark.parametrize("status", ["completed", "failed"])
def test_real_runtime_contract_preserves_result_status_sources_and_turn(monkeypatch, name, status):
    async def run():
        sources = [{"title": "source", "url": "https://example.com"}] if status == "completed" else []
        runtime = SimpleNamespace(execute=Mock(return_value=("result", {"status": status, "reason": "provider_failed" if status == "failed" else None}, sources)))
        journal = SimpleNamespace(current_turn_id=lambda: "turn-1", _emit=AsyncMock())
        schemas = catalog(monkeypatch, runtime, journal)
        assert set(schemas) == NAMES
        callback = AsyncMock()
        await schemas[name].handler(SimpleNamespace(arguments={}, tool_call_id="call-1", result_callback=callback))
        result = callback.await_args.args[0]
        assert result["status"] == status and result["sources"] == sources
        assert runtime.execute.call_args.kwargs["disclosed_tool_names"] == NAMES
        assert runtime.execute.call_args.kwargs["allowed_domains"] == []
        events = journal._emit.await_args_list
        assert [c.args[0] for c in events] == ["tool.started", "tool." + status]
        assert all(c.kwargs["turn_id"] == "turn-1" for c in events)
    asyncio.run(run())


def test_cancelled_tool_never_delivers_late_thread_result_to_next_turn(monkeypatch):
    async def run():
        entered, release = threading.Event(), threading.Event()
        def execute(*a, **kw):
            entered.set()
            assert release.wait(3)
            return "late result", {"status": "completed"}, []
        runtime = SimpleNamespace(execute=execute)
        journal = SimpleNamespace(current_turn_id=lambda: "old-turn", _emit=AsyncMock())
        schemas = catalog(monkeypatch, runtime, journal)
        callback = AsyncMock()
        task = asyncio.create_task(schemas["search_web"].handler(SimpleNamespace(arguments={}, tool_call_id="call", result_callback=callback)))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        finally:
            release.set()
        callback.assert_not_awaited()
        assert journal._emit.await_args.args[0] == "tool.cancelled"
        assert journal._emit.await_args.kwargs["turn_id"] == "old-turn"
    asyncio.run(run())


def test_catalog_failure_is_explicit_not_an_empty_tool_fallback(monkeypatch):
    monkeypatch.setattr("app.core.database.SessionLocal", Mock(side_effect=RuntimeError("db unavailable")))
    session = SimpleNamespace(workspace_id="workspace", owner_user_id="user", chat_session_id="chat")
    with pytest.raises(RuntimeError, match="Voice tool catalog could not be loaded"):
        _foreground_text_tool_schemas(session, object())
