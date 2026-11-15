from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
from app.providers.remote.fetch import UnsafeFetchURL
from app.services.browser_page_read import read_browser_page
from app.services.managed_agent_session import DelegatedRuntime, bind_chat_session
from app.services.web_tool_policy import authorize_mcp_page, bind_web_route, mcp_web_contract, mcp_web_result, web_tool_allowed


def runtime_for_contract(contract):
    server = SimpleNamespace(id="server", display_name="知识库", requested_tools=["lookup"],
        manifest_json={"permissions": ["network"], "web_tools": {"lookup": contract}})
    extensions = SimpleNamespace(list_servers=lambda: [server], db=MagicMock(),
        _agent_mcp_function_name=lambda sid, name: f"lg_mcp_{sid}_{name}")
    return SimpleNamespace(extensions=extensions, workspace_id="ws", search_route="model_native", search_provider=None), server


@pytest.mark.parametrize("contract", [{}, {"kind": "unknown"}, {"kind": None}, {"kind": "fetchh"}])
def test_invalid_cached_contract_cannot_fail_open(contract):
    runtime, _ = runtime_for_contract(contract)
    assert mcp_web_contract(runtime.extensions, "lg_mcp_server_lookup")["kind"] == "opaque"
    assert not web_tool_allowed(runtime, "lg_mcp_server_lookup")


def test_delegated_runtime_assignments_reach_wrapped_runtime_and_sandbox():
    inner = SimpleNamespace(extensions=SimpleNamespace(), sandbox=SimpleNamespace())
    delegated = DelegatedRuntime(inner, None)
    bind_web_route(delegated, "disabled")
    assert inner.search_route == inner.extensions.web_search_route == inner.sandbox.web_search_route == "disabled"


def test_managed_child_cannot_replace_immutable_parent_route(monkeypatch):
    task = SimpleNamespace(owner_user_id="owner", spec_json={"input_snapshot": {"web_search_route": "model_native"}})
    monkeypatch.setattr("app.services.managed_agent_session._task_for_child", lambda *_: (task, SimpleNamespace()))
    service = SimpleNamespace(db=MagicMock(), workspace_id="ws", actor_id="owner",
                              model_provider=SimpleNamespace(search_route="external"))
    with pytest.raises(AppError) as exc:
        bind_chat_session(service, "child")
    assert exc.value.code == "delegated_search_route_locked"
    service.db.get.assert_not_called()


def test_mcp_page_read_requests_the_existing_domain_authorization():
    runtime, _ = runtime_for_contract({"kind": "fetch"})
    runtime._web_fetch_policy = lambda: {"allowed_domains": [], "allow_without_confirmation": False}
    runtime.extensions.db.scalar.return_value = None
    runtime._fetch_authorization_challenge = MagicMock(return_value=("request", {"status": "failed"}, []))
    domains, challenge = authorize_mcp_page(runtime, "lg_mcp_server_lookup", {"url": "https://example.org"},
                                          {"id": "tool"}, [], "chat")
    assert domains == [] and challenge is not None
    assert runtime._fetch_authorization_challenge.call_count == 1


def test_mcp_page_url_cannot_bypass_public_domain_validation(monkeypatch):
    runtime, _ = runtime_for_contract({"kind": "fetch"})
    runtime._web_fetch_policy = lambda: {"allowed_domains": ["example.org"], "allow_without_confirmation": False}
    def deny(*_):
        raise UnsafeFetchURL("blocked")
    monkeypatch.setattr("app.providers.remote.fetch.require_public_http_url", deny)
    with pytest.raises(AppError) as exc:
        authorize_mcp_page(runtime, "lg_mcp_server_lookup", {"url": "http://127.0.0.1/admin"}, {}, [], "chat")
    assert exc.value.code == "fetch_url_blocked"


def test_mcp_unsafe_final_page_is_never_published_as_a_read_source(monkeypatch):
    runtime, _ = runtime_for_contract({"kind": "fetch", "results_path": "results"})
    def deny(*_):
        raise UnsafeFetchURL("private redirect")
    monkeypatch.setattr("app.providers.remote.fetch.require_public_http_url", deny)
    with pytest.raises(AppError) as exc:
        mcp_web_result(runtime, "lg_mcp_server_lookup", {"status": "succeeded", "result": {
            "structuredContent": {"results": [{"url": "http://127.0.0.1/private", "content": "do not return"}]}
        }}, ["example.org"])
    assert exc.value.code == "fetch_url_blocked"


def test_browser_private_url_is_blocked_before_starting_chromium(monkeypatch):
    monkeypatch.setattr("app.providers.remote.fetch._configured_fake_ip_networks", lambda: [])
    sandbox = MagicMock()
    with pytest.raises(AppError) as exc:
        read_browser_page(sandbox, {"url": "http://127.0.0.1/private"}, chat_session_id="chat",
                          allowed_domains=["127.0.0.1"], agent_authorized=True)
    assert exc.value.code == "fetch_url_blocked"
    sandbox.execute_agent_tool.assert_not_called()


@pytest.mark.parametrize("route", ["disabled", "model_native"])
def test_direct_sandbox_search_cannot_bypass_inherited_route(route):
    from app.services.sandbox_toolkit import SandboxToolkitMixin
    sandbox = SimpleNamespace(web_search_route=route)
    with pytest.raises(AppError) as error:
        SandboxToolkitMixin.toolkit_search_web(sandbox, SimpleNamespace(query="news"))
    assert error.value.code == "web_search_route_denied"


def test_browser_final_redirect_is_validated_before_returning_body(monkeypatch):
    def validate(url, _domains):
        if "127.0.0.1" in url:
            raise UnsafeFetchURL("private")
        return "example.org"
    monkeypatch.setattr("app.services.browser_page_read.require_public_http_url", validate)
    sandbox = MagicMock()
    sandbox.execute_agent_tool.return_value = {"status": "SUCCEEDED", "exit_code": 0,
        "stdout": 'LG_BROWSER_RESULT:{"url":"http://127.0.0.1/private","content":"do not read"}'}
    with pytest.raises(AppError) as exc:
        read_browser_page(sandbox, {"url": "https://example.org"}, chat_session_id="chat",
                          allowed_domains=["example.org"], agent_authorized=True)
    assert exc.value.code == "fetch_url_blocked"
