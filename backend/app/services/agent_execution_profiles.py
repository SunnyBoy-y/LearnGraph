"""Server-authoritative execution profiles for durable sub-agents.

The profile layer intentionally does not execute tools or call a model.  It
freezes the three decisions that must be identical at task admission and at
execution time:

* which role owns the task;
* whether ``tools`` means the role default, no tools, or a strict allow-list;
* whether the task needs a sandbox-container lane or only a host/model lane.

``tools=None`` is the role default, ``tools=[]`` is an explicit no-tool task,
and a non-empty list is an exact allow-list.  Execution code must preserve that
distinction instead of treating an empty list as "unspecified".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable
from typing import Iterable, Mapping

from app.services.sandbox_network_policy import NetworkCapability


class AgentRole(str, Enum):
    RESEARCH = "research"
    REASON = "reason"
    TOOL = "tool"
    GENERIC = "generic"


class ToolSelectionMode(str, Enum):
    DEFAULT = "default"
    NONE = "none"
    ALLOWLIST = "allowlist"




_NETWORK_CAPABILITY_RANK: Mapping[NetworkCapability, int] = {
    NetworkCapability.OFFLINE: 0,
    NetworkCapability.FETCH: 1,
    NetworkCapability.BROWSER: 2,
    NetworkCapability.RESTRICTED_EGRESS: 3,
}

# Explicit broker/tool requirements. Unknown runtime tools default to
# RESTRICTED_EGRESS, so adding a new tool can never silently inherit OFFLINE.
_TOOL_NETWORK_REQUIREMENTS: Mapping[str, NetworkCapability] = {
    "search_web": NetworkCapability.FETCH,
    "parallel_web_research": NetworkCapability.FETCH,
    "fetch_web_page": NetworkCapability.FETCH,
    "download_external_image": NetworkCapability.FETCH,
    "download_github_source": NetworkCapability.FETCH,
    "sandbox_search_web": NetworkCapability.FETCH,
    "sandbox_fetch": NetworkCapability.FETCH,
    "sandbox_download": NetworkCapability.FETCH,
    "sandbox_git_clone": NetworkCapability.RESTRICTED_EGRESS,
}

_OFFLINE_SAFE_TOOL_NAMES = frozenset(
    {
        "get_current_time",
        "list_session_files",
        "read_session_file",
        "sandbox_env_info",
        "sandbox_list_files",
        "sandbox_grep",
        "sandbox_read_file",
        "sandbox_write_file",
        "sandbox_append_file",
        "sandbox_edit_file",
        "sandbox_delete_file",
        "sandbox_exec",
        "sandbox_bash",
        "sandbox_todo",
        "sandbox_apply_patch",
        "sandbox_git",
        "sandbox_skill_list",
        "sandbox_skill_read",
        "sandbox_notebook",
        "sandbox_pipeline",
        "sandbox_publish_file",
        "sandbox_publish_image",
        "sandbox_video_info",
        "sandbox_transcribe_audio",
    }
)


class AgentProfileError(ValueError):
    """The requested role/tool combination is not a valid execution profile."""


def normalize_network_capability(value: NetworkCapability | str | None) -> NetworkCapability:
    if isinstance(value, NetworkCapability):
        return value
    normalized = str(value or NetworkCapability.OFFLINE.value).strip().upper()
    try:
        return NetworkCapability(normalized)
    except ValueError as exc:
        raise AgentProfileError(f"Unsupported network capability: {value!r}") from exc


def clamp_network_capability(
    granted: NetworkCapability | str,
    requested: NetworkCapability | str | None,
) -> NetworkCapability:
    """Return the lower of the server grant and client intent.

    A client may request a narrower capability but can never elevate the
    server-computed profile (for example ReasonAgent cannot request FETCH).
    """
    granted_mode = normalize_network_capability(granted)
    if requested is None:
        return granted_mode
    requested_mode = normalize_network_capability(requested)
    return (
        requested_mode
        if _NETWORK_CAPABILITY_RANK[requested_mode] < _NETWORK_CAPABILITY_RANK[granted_mode]
        else granted_mode
    )


def tool_network_requirement(tool_name: str) -> NetworkCapability:
    name = str(tool_name or "").strip()
    if name.startswith("browser_"):
        return NetworkCapability.BROWSER
    if name in _TOOL_NETWORK_REQUIREMENTS:
        return _TOOL_NETWORK_REQUIREMENTS[name]
    if name in _OFFLINE_SAFE_TOOL_NAMES:
        return NetworkCapability.OFFLINE
    return NetworkCapability.RESTRICTED_EGRESS


def network_capability_allows_tool(
    capability: NetworkCapability | str,
    tool_name: str,
) -> bool:
    mode = normalize_network_capability(capability)
    required = tool_network_requirement(tool_name)
    return _NETWORK_CAPABILITY_RANK[mode] >= _NETWORK_CAPABILITY_RANK[required]


def required_network_capability_for_tools(
    tools: Iterable[str],
) -> NetworkCapability:
    required = NetworkCapability.OFFLINE
    for tool_name in tools:
        candidate = tool_network_requirement(str(tool_name))
        if _NETWORK_CAPABILITY_RANK[candidate] > _NETWORK_CAPABILITY_RANK[required]:
            required = candidate
    return required




_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
GENERIC_DEFAULT_TOOLS: tuple[str, ...] = (
    "sandbox_env_info",
    "sandbox_list_files",
    "sandbox_grep",
    "sandbox_read_file",
    "sandbox_write_file",
    "sandbox_append_file",
    "sandbox_edit_file",
    "sandbox_delete_file",
    "sandbox_exec",
    "sandbox_bash",
    "sandbox_todo",
    "sandbox_apply_patch",
    "sandbox_git",
    "sandbox_skill_list",
    "sandbox_skill_read",
)

RESEARCH_TOOLS: tuple[str, ...] = (
    "search_web",
    "fetch_web_page",
    "sandbox_download",
    "parallel_web_research",
)

READ_ONLY_TOOL_NAMES = frozenset(
    {
        "get_current_time",
        "search_web",
        "parallel_web_research",
        "fetch_web_page",
        "search_images",
        "list_session_files",
        "read_session_file",
        "sandbox_env_info",
        "sandbox_list_files",
        "sandbox_grep",
        "sandbox_read_file",
        "sandbox_skill_list",
        "sandbox_skill_read",
    }
)

_ROLE_ALIASES = {
    "research": AgentRole.RESEARCH,
    "researcher": AgentRole.RESEARCH,
    "research_agent": AgentRole.RESEARCH,
    "reason": AgentRole.REASON,
    "reasoner": AgentRole.REASON,
    "reason_agent": AgentRole.REASON,
    "tool": AgentRole.TOOL,
    "tool_agent": AgentRole.TOOL,
    "generic": AgentRole.GENERIC,
    "sandbox": AgentRole.GENERIC,
}

_ROLE_DEFAULTS: dict[AgentRole, tuple[str, ...]] = {
    AgentRole.RESEARCH: RESEARCH_TOOLS,
    AgentRole.REASON: (),
    AgentRole.TOOL: (),
    AgentRole.GENERIC: GENERIC_DEFAULT_TOOLS,
}

_ROLE_ALLOWED_TOOLS: dict[AgentRole, frozenset[str] | None] = {
    AgentRole.RESEARCH: frozenset(RESEARCH_TOOLS),
    AgentRole.REASON: frozenset(),
    # ToolAgent may use a dynamically discovered MCP/Skill/sandbox tool.  The
    # actual authorization still comes from AgentToolRuntime at execution time;
    # this layer only preserves the exact client-requested allow-list.
    AgentRole.TOOL: None,
    AgentRole.GENERIC: None,
}

_ROLE_DEFAULT_THINKING: dict[AgentRole, str] = {
    AgentRole.RESEARCH: "medium",
    AgentRole.REASON: "high",
    AgentRole.TOOL: "medium",
    AgentRole.GENERIC: "medium",
}

_THINKING_ORDER = ("off", "low", "medium", "high", "xhigh")


def normalize_agent_role(role_key: str | None) -> AgentRole:
    normalized = str(role_key or "generic").strip().casefold()
    return _ROLE_ALIASES.get(normalized, AgentRole.GENERIC)


def normalize_thinking_mode(value: str | None, *, default: str = "medium") -> str:
    normalized = str(value or default).strip().casefold()
    aliases = {"none": "off", "fast": "off", "max": "xhigh"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in _THINKING_ORDER:
        raise AgentProfileError(f"Unsupported thinking mode: {value!r}")
    return normalized


def fallback_thinking_modes(requested: str | None, *, default: str = "medium") -> tuple[str, ...]:
    """Return a deterministic graceful-degradation chain.

    Unsupported providers must never make a background task crash.  The caller
    tries the requested mode first, then lower supported tiers, ending at
    ``off``.  Duplicate values are removed while order is preserved.
    """
    requested_mode = normalize_thinking_mode(requested, default=default)
    index = _THINKING_ORDER.index(requested_mode)
    candidates = [requested_mode, *_THINKING_ORDER[:index][::-1]]
    return tuple(dict.fromkeys(candidates))


def _normalize_tool_names(tools: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in tools:
        name = str(raw or "").strip()
        if not name:
            continue
        if not _TOOL_NAME_RE.fullmatch(name):
            raise AgentProfileError(f"Invalid tool name: {raw!r}")
        if name not in seen:
            seen.add(name)
            result.append(name)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class AgentExecutionProfile:
    role: AgentRole
    tool_mode: ToolSelectionMode
    tools: tuple[str, ...]
    default_thinking_mode: str
    execution_lane: str
    read_only: bool
    network_capability: NetworkCapability

    @property
    def requires_search(self) -> bool:
        return self.role is AgentRole.RESEARCH

    def network_policy_payload(self) -> dict[str, object]:
        """Serializable policy projection stored with the task.

        The code sandbox never receives a direct route from this profile: all
        external I/O remains broker-only and subject to the existing egress/
        fetch approval checks.
        """
        return {
            "mode": self.network_capability.value,
            "broker_only": True,
            "direct_socket": False,
        }

    def requested_tools(self) -> tuple[str, ...]:
        """Return the exact server-side selection before runtime filtering."""
        return self.tools

    def resolve_available_tools(self, available: Iterable[str]) -> tuple[str, ...]:
        """Intersect the strict selection with the authorized runtime names."""
        available_set = {str(item) for item in available}
        return tuple(name for name in self.tools if name in available_set)

    def missing_tools(self, available: Iterable[str]) -> tuple[str, ...]:
        available_set = {str(item) for item in available}
        return tuple(name for name in self.tools if name not in available_set)


def resolve_agent_execution_profile(
    role_key: str | None,
    *,
    tools: list[str] | tuple[str, ...] | None,
) -> AgentExecutionProfile:
    """Resolve the durable execution policy for one sub-agent task.

    ``tools=None`` selects the role default, ``tools=[]`` selects no tools, and
    any non-empty list is a strict allow-list.  ReasonAgent always resolves to
    no tools; accepting a non-empty list there would silently turn a pure
    reasoning request into a tool-enabled task.
    """
    role = normalize_agent_role(role_key)
    if tools is None:
        mode = ToolSelectionMode.DEFAULT
        selected = _ROLE_DEFAULTS[role]
    elif len(tools) == 0:
        mode = ToolSelectionMode.NONE
        selected = ()
    else:
        mode = ToolSelectionMode.ALLOWLIST
        selected = _normalize_tool_names(tools)

    allowed = _ROLE_ALLOWED_TOOLS[role]
    if allowed is not None:
        invalid = [name for name in selected if name not in allowed]
        if invalid:
            raise AgentProfileError(
                f"Role {role.value!r} does not allow tools: {', '.join(invalid)}"
            )

    if role in {AgentRole.RESEARCH, AgentRole.REASON}:
        execution_lane = "model_network"
    elif not selected:
        execution_lane = "model_network"
    elif set(selected).issubset(READ_ONLY_TOOL_NAMES):
        execution_lane = "model_network"
    else:
        execution_lane = "sandbox"

    read_only = role in {AgentRole.RESEARCH, AgentRole.REASON} or (
        bool(selected) and set(selected).issubset(READ_ONLY_TOOL_NAMES)
    )
    network_capability = required_network_capability_for_tools(selected)
    return AgentExecutionProfile(
        role=role,
        tool_mode=mode,
        tools=tuple(selected),
        default_thinking_mode=_ROLE_DEFAULT_THINKING[role],
        execution_lane=execution_lane,
        read_only=read_only,
        network_capability=network_capability,
    )


def task_requires_sandbox_capacity(
    role_key: str | None,
    *,
    tools: list[str] | tuple[str, ...] | None,
    execution_lane: str | None = None,
) -> bool:
    """Whether a durable task must wait for a Docker/container reservation."""
    lane = str(execution_lane or "").strip()
    if lane in {"model_network", "sandbox"}:
        return lane == "sandbox"
    return (
        resolve_agent_execution_profile(role_key, tools=tools).execution_lane
        == "sandbox"
    )
