from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.domain.schemas.extensions import MCPServerManifest
from app.providers.ports.model import ProviderChatMessage, ProviderStreamEvent
from app.providers.web_activity import native_web_activity
from app.services.agent_runtime import AgentToolRuntime
from app.services.browser_page_read import BROWSER_SCRIPT, read_browser_page
from app.services.chat import ChatService
from app.services.web_evidence import normalize_web_evidence
from app.services.web_preflight import stream_web_operation
from app.services.web_tool_policy import bind_web_route, mcp_web_result, web_tool_allowed


def plugin(kind="search", permissions=None):
    server = SimpleNamespace(id="papers", display_name="文献检索 MCP", enabled=True, agent_auto_invoke=True, requested_tools=["lookup"],
        manifest_json={"permissions": permissions if permissions is not None else ["network"],
                       "web_tools": {"lookup": {"kind": kind, "results_path": "results"}}})
    extensions = SimpleNamespace(db=MagicMock(), list_servers=lambda: [server], _current_snapshot=lambda _server: None,
        _agent_mcp_function_name=lambda sid, name: f"lg_mcp_{sid}_{name}",
        agent_tool_definitions=lambda **_: [{"type": "function", "function": {
            "name": "lg_mcp_papers_lookup", "description": "Find papers", "parameters": {"type": "object", "properties": {}}
        }}])
    runtime = AgentToolRuntime(workspace_id="ws", actor_id="user", search_provider=None,
        extensions=extensions, sandbox=None, sandbox_authorized=False)
    return runtime, server


def test_manifest_requires_reviewed_network_permission_and_requested_tool():
    manifest = MCPServerManifest(identity="papers", requested_tools=["lookup"], permissions=["network"],
        web_tools={"lookup": {"kind": "search"}})
    assert manifest.web_tools["lookup"].kind == "search"
    with pytest.raises(ValidationError, match="network"):
        MCPServerManifest(identity="papers", requested_tools=["lookup"], web_tools={"lookup": {"kind": "search"}})
    with pytest.raises(ValidationError, match="requested_tools"):
        MCPServerManifest(identity="papers", requested_tools=["read"], permissions=["network"],
                          web_tools={"lookup": {"kind": "search"}})


@pytest.mark.parametrize("route,expected", [("disabled", False), ("model_native", False), ("external", True), ("local", True), ("auto", True)])
def test_builtin_and_plugin_search_obey_route_at_disclosure_and_execution(route, expected):
    runtime, _ = plugin()
    bind_web_route(runtime, route)
    for name in ("search_web", "parallel_web_research", "sandbox_search_web", "lg_mcp_papers_lookup"):
        allowed = expected and not (route == "local" and name.startswith("lg_mcp_"))
        assert web_tool_allowed(runtime, name) is allowed
        if not allowed:
            content, meta, sources = runtime.execute({"function": {"name": name, "arguments": "{}"}},
                allowed_domains=[], chat_session_id="chat", disclosed_tool_names={name})
            assert meta["reason"] == "web_search_route_denied" and sources == []
    definitions = runtime.definitions(agent_mode_enabled=True, web_search_enabled=True,
                                     activated_capabilities={"mcp_tool:papers:lookup"})
    names = {d["function"]["name"] for d in definitions}
    assert ("lg_mcp_papers_lookup" in names) is (expected and route != "local")
    assert web_tool_allowed(runtime, "fetch_web_page")
    assert web_tool_allowed(runtime, "browser_read_page")


def test_unknown_network_plugin_cannot_bypass_native_route_with_hashed_name():
    runtime, server = plugin()
    server.manifest_json["web_tools"] = {}
    bind_web_route(runtime, "model_native")
    assert not web_tool_allowed(runtime, "lg_mcp_papers_lookup")
    assert runtime.extensions.web_search_route == "model_native"


def test_official_qwen_companion_is_allowed_but_anysearch_is_not():
    runtime, _ = plugin()
    bind_web_route(runtime, "model_native")
    runtime.search_provider = type("QwenResponsesToolProvider", (), {})()
    assert web_tool_allowed(runtime, "search_web")
    assert not web_tool_allowed(runtime, "sandbox_search_web")
    runtime.search_provider = type("AnySearchProvider", (), {})()
    assert not web_tool_allowed(runtime, "search_web")


def test_mcp_search_snippet_never_becomes_page_text_even_if_content_is_present():
    runtime, _ = plugin()
    result, sources = mcp_web_result(runtime, "lg_mcp_papers_lookup", {"status": "succeeded", "result": {
        "structuredContent": {"results": [{"url": "https://example.org/paper", "title": "Paper", "snippet": "abstract", "content": "not evidence of a page read"}]}
    }}, [])
    assert sources[0]["evidence_kind"] == "search_snippet"
    assert sources[0]["page_read"] is False and sources[0]["content"] == ""
    assert result["results"][0]["provider_label"] == "文献检索 MCP"


def test_mcp_fetch_json_result_uses_same_citation_schema_and_domain_filter(monkeypatch):
    monkeypatch.setattr("app.providers.remote.fetch.require_public_http_url", lambda *_: "example.org")
    runtime, _ = plugin(kind="fetch")
    mapped = mcp_web_result(runtime, "lg_mcp_papers_lookup", {"status": "succeeded", "result": {
        "content": [{"type": "text", "text": json.dumps({"results": [
            {"url": "https://example.org/a", "title": "A", "content": "body"},
            {"url": "https://outside.test/b", "content": "must not cite"},
        ]})}]
    }}, ["example.org"])
    _, sources = mapped
    assert len(sources) == 1 and sources[0]["page_read"] is True
    assert sources[0]["evidence_kind"] == "page_text" and sources[0]["index"] == 1


@pytest.mark.parametrize("payload", [{"status": "failed", "result": {}}, {"status": "succeeded", "result": {"isError": True}}])
def test_mcp_errors_are_not_presented_as_search_success(payload):
    runtime, _ = plugin()
    with pytest.raises(AppError) as exc:
        mcp_web_result(runtime, "lg_mcp_papers_lookup", payload, [])
    assert exc.value.code == "mcp_web_tool_failed"


def test_unsafe_and_duplicate_sources_are_not_accepted_as_citations():
    rows = [{"url": "file:///secret", "content": "secret"}, {"url": "https://u:p@example.org/x"},
            {"url": "https://example.org/a", "content": "page"}, {"url": "https://example.org/a", "content": "duplicate"}]
    sources = normalize_web_evidence(rows, provider_id="p", provider_label="P", kind="fetch")
    assert len(sources) == 1 and sources[0]["page_read"]


def test_isolated_browser_never_accesses_user_session_or_accepts_cookie_arguments(monkeypatch):
    sandbox = MagicMock()
    for args in ({"url": "https://example.org", "use_user_session": True},
                 {"url": "https://example.org", "cookies": [{"name": "secret"}]}):
        with pytest.raises(AppError):
            read_browser_page(sandbox, args, chat_session_id="chat", allowed_domains=["example.org"], agent_authorized=True)
    sandbox.execute_agent_tool.assert_not_called()
    assert "browser.newContext" in BROWSER_SCRIPT
    assert "storageState" not in BROWSER_SCRIPT and "connectOverCDP" not in BROWSER_SCRIPT
    assert "LEARNGRAPH_EGRESS_POLICY_DIGEST" in BROWSER_SCRIPT
    assert "serviceWorkers: 'block'" in BROWSER_SCRIPT


def test_browser_uses_existing_sandbox_egress_and_returns_actual_page_text(monkeypatch):
    monkeypatch.setattr("app.services.browser_page_read.require_public_http_url", lambda *_: "example.org")
    sandbox = MagicMock()
    sandbox.execute_agent_tool.return_value = {"status": "SUCCEEDED", "exit_code": 0,
        "stdout": "LG_BROWSER_RESULT:" + json.dumps({"url": "https://example.org/a", "title": "A", "content": "rendered dynamic body"})}
    result, sources = read_browser_page(sandbox, {"url": "https://example.org/a"},
        chat_session_id="chat", allowed_domains=["example.org"], agent_authorized=True)
    assert result["session_mode"] == "isolated" and result["page_read"] is True
    assert sources[0]["evidence_kind"] == "page_text"
    call = sandbox.execute_agent_tool.call_args
    assert call.args[0] == "sandbox_exec" and call.args[1]["argv"][0] == "node"
    from app.providers.remote.sandbox import validate_agent_argv
    assert validate_agent_argv(tuple(call.args[1]["argv"]))
    assert [entry.args[0] for entry in sandbox.execute_agent_tool.call_args_list] == ["sandbox_write_file", "sandbox_write_file", "sandbox_write_file", "sandbox_exec"]
    assert call.kwargs["chat_session_id"] == "chat"


def test_browser_failure_does_not_claim_page_was_read(monkeypatch):
    monkeypatch.setattr("app.services.browser_page_read.require_public_http_url", lambda *_: "example.org")
    sandbox = MagicMock()
    sandbox.execute_agent_tool.side_effect = [{}, {}, {}, {"status": "FAILED", "exit_code": 1, "stdout": ""}]
    with pytest.raises(AppError) as exc:
        read_browser_page(sandbox, {"url": "https://example.org/a"}, chat_session_id="chat",
                          allowed_domains=["example.org"], agent_authorized=True)
    assert exc.value.code == "browser_read_failed"


def test_preflight_status_is_emitted_before_io_and_reports_failures():
    calls = []
    stream = stream_web_operation(lambda: calls.append("executed") or ["url"], kind="search",
                                 provider=SimpleNamespace(provider_type="anysearch", provider_id="any"))
    first = next(stream)
    assert not calls and '"status": "streaming"' in first and "AnySearch" in first
    assert '"status": "completed"' in next(stream)
    assert calls == ["executed"]
    def fail():
        raise AppError(502, "search_failed", "failed")
    failed = stream_web_operation(fail, kind="search", provider=None)
    next(failed)
    assert '"status": "failed"' in next(failed)
    with pytest.raises(AppError):
        next(failed)


def test_native_search_activity_requires_a_real_upstream_event():
    provider = SimpleNamespace(provider_type="codex_chatgpt", provider_id="official")
    assert native_web_activity({"type": "response.output_text.delta", "delta": "I will search"}, provider) is None
    activity = native_web_activity({"type": "response.web_search_call.searching", "item_id": "ws-1"}, provider)
    assert activity["status"] == "streaming" and activity["provider_label"] == "OpenAI"
    final = native_web_activity({"type": "response.output_item.done", "item": {
        "type": "web_search_call", "id": "ws-1", "status": "completed", "action": {"type": "open_page", "url": "https://example.org"}}}, provider)
    assert final["operation"] == "fetch" and final["status"] == "completed"


def test_native_search_failure_closes_the_actual_running_activity():
    service = object.__new__(ChatService)
    service.agent_tool_runtime = None
    activity = {"id": "ws-1", "status": "streaming", "operation": "search", "provider_label": "OpenAI"}
    def stream(*_a, **_k):
        yield ProviderStreamEvent("web_activity", web_activity=activity)
        raise AppError(502, "upstream_failed", "failed")
    service.model_provider = SimpleNamespace(stream_chat=stream)
    events = service._stream_agent_chat([ProviderChatMessage("user", "search")], tools=None, session_id="chat")
    assert next(events).web_activity["status"] == "streaming"
    assert next(events).web_activity["status"] == "failed"
    with pytest.raises(AppError):
        next(events)
