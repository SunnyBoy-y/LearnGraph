from __future__ import annotations

import pytest

from app.domain.schemas.sandbox import AgentTaskResult
from app.services.agent_execution_profiles import (
    AgentProfileError,
    ToolSelectionMode,
    fallback_thinking_modes,
    resolve_agent_execution_profile,
    task_requires_sandbox_capacity,
)
from app.services.agent_execution_profiles import (
    NetworkCapability,
    clamp_network_capability,
    network_capability_allows_tool,
    tool_network_requirement,
)


def test_none_selects_role_default_and_empty_list_means_no_tools():
    research_default = resolve_agent_execution_profile("research", tools=None)
    research_none = resolve_agent_execution_profile("research", tools=[])
    generic_default = resolve_agent_execution_profile("generic", tools=None)
    generic_none = resolve_agent_execution_profile("generic", tools=[])

    assert research_default.tool_mode is ToolSelectionMode.DEFAULT
    assert research_default.tools == (
        "search_web",
        "fetch_web_page",
        "sandbox_download",
        "parallel_web_research",
    )
    assert research_none.tool_mode is ToolSelectionMode.NONE
    assert research_none.tools == ()
    assert generic_default.tool_mode is ToolSelectionMode.DEFAULT
    assert "sandbox_exec" in generic_default.tools
    assert generic_none.tools == ()


def test_non_empty_tool_list_is_a_strict_allowlist():
    profile = resolve_agent_execution_profile(
        "research", tools=["fetch_web_page", "search_web", "search_web"]
    )
    assert profile.tool_mode is ToolSelectionMode.ALLOWLIST
    assert profile.tools == ("fetch_web_page", "search_web")
    available = {"search_web", "parallel_web_research", "sandbox_exec"}
    assert profile.resolve_available_tools(available) == ("search_web",)
    assert profile.missing_tools(available) == ("fetch_web_page",)


def test_reason_agent_rejects_tools():
    with pytest.raises(AgentProfileError):
        resolve_agent_execution_profile("reason", tools=["search_web"])


def test_read_only_profiles_do_not_require_docker_capacity():
    assert not task_requires_sandbox_capacity("research", tools=None)
    assert not task_requires_sandbox_capacity("reason", tools=None)
    assert not task_requires_sandbox_capacity("tool", tools=["sandbox_read_file"])
    assert task_requires_sandbox_capacity("tool", tools=["sandbox_exec"])


def test_thinking_fallback_is_ordered_and_graceful():
    assert fallback_thinking_modes("high") == ("high", "medium", "low", "off")
    assert fallback_thinking_modes("off") == ("off",)


def test_network_capability_is_derived_from_role_and_tools():
    assert (
        resolve_agent_execution_profile("reason", tools=None).network_capability
        is NetworkCapability.OFFLINE
    )
    assert (
        resolve_agent_execution_profile("research", tools=None).network_capability
        is NetworkCapability.FETCH
    )
    assert (
        resolve_agent_execution_profile("research", tools=[]).network_capability
        is NetworkCapability.OFFLINE
    )
    assert (
        resolve_agent_execution_profile("tool", tools=["sandbox_download"]).network_capability
        is NetworkCapability.FETCH
    )
    assert (
        resolve_agent_execution_profile("tool", tools=["sandbox_git_clone"]).network_capability
        is NetworkCapability.RESTRICTED_EGRESS
    )


def test_network_capability_clamps_client_intent_and_unknown_tools():
    assert clamp_network_capability(
        NetworkCapability.FETCH, NetworkCapability.RESTRICTED_EGRESS
    ) is NetworkCapability.FETCH
    assert clamp_network_capability(
        NetworkCapability.RESTRICTED_EGRESS, NetworkCapability.OFFLINE
    ) is NetworkCapability.OFFLINE
    assert tool_network_requirement("unclassified_new_tool") is NetworkCapability.RESTRICTED_EGRESS
    assert not network_capability_allows_tool(
        NetworkCapability.OFFLINE, "sandbox_download"
    )
    assert network_capability_allows_tool(
        NetworkCapability.FETCH, "sandbox_download"
    )
    assert not network_capability_allows_tool(
        NetworkCapability.FETCH, "browser_open"
    )
    assert network_capability_allows_tool(
        NetworkCapability.BROWSER, "browser_open"
    )


def test_typed_result_rejects_non_http_source():
    with pytest.raises(ValueError):
        AgentTaskResult(
            task_id="task",
            task_type="research",
            status="succeeded",
            completed_at="2026-01-01T00:00:00Z",
            requirement_version=1,
            sources=[{"title": "fake", "url": "sandbox:not-a-url"}],
        )
