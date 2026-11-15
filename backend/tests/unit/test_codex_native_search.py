"""Offline regression contracts for ChatGPT login's hosted search lane."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.domain.schemas.chat import MessageCreateRequest
from app.providers.model_catalog import unified_model_defaults
from app.providers.model_options import (
    ModelCapabilityError,
    catalog_capability_snapshot,
    model_capabilities_for_model,
    resolve_model_call_options,
)
from app.providers.ports.model import ProviderChatMessage
from app.providers.remote.codex import CodexCredentials
from app.providers.remote.codex_provider import CodexResponsesProvider
from app.providers.remote.openai import ProviderHTTPError
from app.services.chat import ChatService


MODEL = "gpt-5.5"
CAPABILITIES = {"provider_family": "codex_chatgpt", "capability_source": "official_catalog"}


def test_only_codex_transport_gains_hosted_search():
    assert unified_model_defaults(MODEL, provider_type="codex_chatgpt")["hosted_web_search"] is True
    assert not unified_model_defaults(MODEL, provider_type="openai_compatible_chat").get("hosted_web_search")
    assert catalog_capability_snapshot(MODEL, provider_type="codex_chatgpt")["hosted_web_search"] is True
    assert not unified_model_defaults("gpt-image-2", provider_type="codex_chatgpt").get("hosted_web_search")


def test_legacy_discovery_false_is_refreshed_without_rediscovery():
    caps = {**CAPABILITIES, "hosted_web_search": False,
            "models": {MODEL: {"capability_source": "official_catalog", "hosted_web_search": False}}}
    assert model_capabilities_for_model(caps, MODEL)["hosted_web_search"] is True
    options = resolve_model_call_options(caps, MODEL, search_route="auto")
    assert options.native_web_search and options.search_route == "model_native"


@pytest.mark.parametrize("route", ["disabled", "external", "local"])
def test_explicit_non_native_routes_are_preserved(route):
    options = resolve_model_call_options(CAPABILITIES, MODEL, search_route=route)
    assert options.search_route == route
    assert not options.native_web_search


def test_explicit_model_opt_out_is_not_overwritten():
    caps = {**CAPABILITIES, "models": {MODEL: {
        "hosted_web_search": False, "capability_source": "user_declared",
    }}}
    assert resolve_model_call_options(caps, MODEL, search_route="auto").search_route == "external"
    with pytest.raises(ModelCapabilityError, match="hosted_web_search"):
        resolve_model_call_options(caps, MODEL, search_route="model_native")


@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize("route", ["model_native", "disabled", "external"])
def test_codex_wire_keeps_functions_and_returns_real_citations(agent, route):
    requests = []
    source = {"type": "url_citation", "url": "https://openai.com/news/",
              "title": "OpenAI News", "start_index": 0, "end_index": 6}

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert str(request.url) == "https://chatgpt.com/backend-api/codex/responses"
        assert request.headers["authorization"] == "Bearer test-access-not-real"
        content = {"type": "output_text", "text": "Result", "annotations": [source] if route == "model_native" else []}
        final = {"id": "r-test", "status": "completed", "output": [
            {"type": "message", "role": "assistant", "content": [content]},
        ], "usage": {"input_tokens": 5, "output_tokens": 2}}
        events = [{"type": "response.output_text.delta", "delta": "Result"},
                  {"type": "response.completed", "response": final}]
        return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))

    provider = CodexResponsesProvider(
        provider_id="test-codex", provider_type="codex_chatgpt", model_id=MODEL,
        base_url="https://chatgpt.com/backend-api/codex", api_key="not-an-api-key",
        credentials=CodexCredentials("test-access-not-real", "test-refresh-not-real", account_id="test-account-not-real"),
        transport=httpx.MockTransport(handle),
        call_options=resolve_model_call_options(CAPABILITIES, MODEL, search_route=route),
    )
    tools = [{"type": "function", "function": {
        "name": "read_file", "description": "Read an authorized file",
        "parameters": {"type": "object", "properties": {}},
    }}] if agent else None
    list(provider.stream_chat([ProviderChatMessage("user", "Search current news")], tools=tools))
    assert len(requests) == 1
    body = requests[0]
    search_tools = [t for t in body.get("tools", []) if t.get("type") == "web_search"]
    assert search_tools == ([{"type": "web_search", "external_web_access": True}] if route == "model_native" else [])
    assert any(t.get("name") == "read_file" for t in body.get("tools", [])) is agent
    assert body["store"] is False and body["stream"] is True
    assert not ({"max_tokens", "max_output_tokens", "max_completion_tokens"} & body.keys())
    assert bool(provider.last_sources) is (route == "model_native")
    if route == "model_native":
        assert provider.last_sources[0]["url"] == source["url"]


def service(route="model_native", provider_type="codex_chatgpt", runtime=True):
    instance = object.__new__(ChatService)
    instance.model_provider = SimpleNamespace(search_route=route, provider_type=provider_type)
    instance.search_provider = SimpleNamespace(available=True)
    instance._session_memory_policy_enabled = lambda _session_id: True
    instance._tool_definitions_cache = {}
    instance._toolset_version = 0
    instance.agent_tool_runtime = SimpleNamespace(definitions=lambda **_: [
        {"type": "function", "function": {"name": name}}
        for name in ("search_web", "parallel_web_research", "search_images", "get_weather", "read_file")
    ]) if runtime else None
    return instance


@pytest.mark.parametrize("runtime", [False, True])
def test_codex_native_never_advertises_anysearch(runtime):
    instance = service(runtime=runtime)
    tools = instance._agent_tool_definitions(True, True)
    names = {t.get("function", {}).get("name") for t in tools}
    assert not ({"search_web", "parallel_web_research"} & names)
    if runtime:
        assert {"search_images", "get_weather", "read_file"} <= names
    instance.model_provider.search_route = "external"
    names = {t.get("function", {}).get("name") for t in instance._agent_tool_definitions(True, True)}
    assert "search_web" in names  # Route change must not reuse native's cache.


def test_qwen_native_companion_is_not_removed():
    instance = service(provider_type="qwen")
    names = {t["function"]["name"] for t in instance._agent_tool_definitions(True, True)}
    assert {"search_web", "parallel_web_research"} <= names


def test_native_route_skips_external_presearch_even_with_anysearch_configured():
    instance = service()
    instance.billing = SimpleNamespace(execute_search=lambda *_a, **_k: pytest.fail("AnySearch must not be called"))
    payload = MessageCreateRequest(content="Find current information", web_search=True, search_route="auto")
    assert instance._run_web_search(payload) == ([], "")
    instance.search_provider = None
    instance._ensure_web_search_available(payload)


def test_native_search_policy_does_not_tell_fast_mode_to_avoid_the_search_tool():
    policy = ChatService._mode_tool_policy(
        agent_mode_enabled=False, model_native_search_enabled=True,
    )
    assert "已授权模型原生联网搜索" in policy
    assert "本轮不提供可调用的工具接口" not in policy
    offline = ChatService._mode_tool_policy(agent_mode_enabled=False)
    assert "本轮不提供可调用的工具接口" in offline


@pytest.mark.parametrize("name", ["search_web", "parallel_web_research"])
def test_stale_external_tool_calls_are_rejected_before_execution(name):
    instance = service()
    result, meta, sources = instance._execute_agent_tool(
        {"function": {"name": name, "arguments": '{"query":"news"}'}}, [],
    )
    assert json.loads(result)["error"] == "web_search_route_denied"
    assert meta["status"] == "failed" and sources == []


def test_upstream_search_rejection_surfaces_instead_of_external_fallback():
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "web_search unavailable"}})

    provider = CodexResponsesProvider(
        provider_id="test-codex", provider_type="codex_chatgpt", model_id=MODEL,
        base_url="https://chatgpt.com/backend-api/codex", api_key="not-an-api-key",
        credentials=CodexCredentials("test-access-not-real", "test-refresh-not-real", account_id="test-account-not-real"),
        transport=httpx.MockTransport(handle),
        call_options=resolve_model_call_options(CAPABILITIES, MODEL, search_route="auto"),
    )
    with pytest.raises(ProviderHTTPError):
        list(provider.stream_chat([ProviderChatMessage("user", "Search news")]))
    assert len(requests) == 1
    assert requests[0]["tools"] == [{"type": "web_search", "external_web_access": True}]
