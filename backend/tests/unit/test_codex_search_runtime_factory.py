"""Ensure native Codex search never constructs an AnySearch lane or worker."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import chat_service_factory as factory


def context_and_settings():
    context = SimpleNamespace(
        workspace_id="test-workspace", workspace=object(), permissions=frozenset(),
        principal=SimpleNamespace(user_id="test-user", tenant_id="test-tenant"),
    )
    settings = SimpleNamespace(memory_root="unused", memory_context_builder_v2=False,
                               memory_read_mode="events", sandbox_agent_enabled=False)
    return context, settings


@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize("route", ["model_native", "external"])
def test_service_and_isolated_worker_use_the_models_resolved_route(monkeypatch, agent, route):
    context, settings = context_and_settings()
    model = SimpleNamespace(provider_type="codex_chatgpt", search_route=route)
    monkeypatch.setattr(factory, "model_provider_for_workspace", MagicMock(return_value=model))
    monkeypatch.setattr(factory, "AuthorizationService", MagicMock())
    monkeypatch.setattr(factory, "MemoryService", MagicMock())
    for name in ("memory_provider_for_workspace", "vision_provider_for_workspace"):
        monkeypatch.setattr(factory, name, MagicMock(return_value=None))
    search = MagicMock(return_value="test-anysearch")
    runtime = MagicMock(return_value="runtime")
    worker = MagicMock(return_value="worker")
    monkeypatch.setattr(factory, "search_provider_for_workspace", search)
    monkeypatch.setattr(factory, "build_agent_tool_runtime", runtime)
    monkeypatch.setattr(factory, "build_agent_tool_worker_runtime", worker)
    monkeypatch.setattr(factory, "ChatService", lambda *args, **kwargs: SimpleNamespace(model=args[3], **kwargs))
    result = factory.build_chat_service(None, workspace_context=context, settings=settings,
                                        search_route="auto", agent_mode=agent)
    assert result.model is model
    if route == "model_native":
        search.assert_not_called()
        assert result.search_provider is None
    else:
        assert search.call_count == 1 and result.search_provider == "test-anysearch"
    if agent:
        expected = {"search_route": route, **({"external_web_search_enabled": False} if route == "model_native" else {})}
        assert runtime.call_args.kwargs == expected
        assert result.tool_worker_factory("worker-db") == "worker"
        assert worker.call_args.kwargs.get("external_web_search_enabled", True) is (route != "model_native")
    else:
        runtime.assert_not_called()
        assert result.tool_worker_factory is None


def test_native_runtime_does_not_resolve_an_external_search_provider(monkeypatch):
    context, settings = context_and_settings()
    authorization = MagicMock()
    authorization.workspace_permissions.return_value = set()
    monkeypatch.setattr(factory, "AuthorizationService", MagicMock(return_value=authorization))
    for name in ("MemoryService", "MCPAndSkillService", "SessionRetrievalService"):
        monkeypatch.setattr(factory, name, MagicMock())
    for name in ("image_search_provider_for_workspace", "memory_provider_for_workspace",
                 "image_provider_for_workspace", "fetch_provider_for_workspace"):
        monkeypatch.setattr(factory, name, MagicMock(return_value=None))
    search = MagicMock(side_effect=AssertionError("Must not construct AnySearch"))
    monkeypatch.setattr(factory, "search_provider_for_workspace", search)
    monkeypatch.setattr(factory, "AgentToolRuntime", lambda **kwargs: SimpleNamespace(**kwargs))
    runtime = factory.build_agent_tool_runtime(None, context, settings, external_web_search_enabled=False)
    search.assert_not_called()
    assert runtime.search_provider is None
