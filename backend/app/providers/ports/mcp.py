from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class MCPTransportFailure(RuntimeError):
    code = "mcp_transport_failed"


class MCPTransportUnavailable(MCPTransportFailure):
    code = "mcp_transport_unavailable"


class MCPTransportTimeout(MCPTransportFailure):
    code = "mcp_transport_timeout"


class MCPResponseTooLarge(MCPTransportFailure):
    code = "mcp_result_too_large"


class MCPProtocolFailure(MCPTransportFailure):
    code = "mcp_protocol_error"


class MCPRunnerTimeout(MCPTransportFailure):
    """The isolated runner exceeded the invocation deadline."""

    code = "mcp_runner_timeout"


class MCPRunnerResourceExceeded(MCPTransportFailure):
    """The isolated runner hit a resource/output quota."""

    code = "mcp_runner_resource_exceeded"


@dataclass(frozen=True)
class MCPProbeResult:
    protocol_version: str
    server_identity: dict[str, Any]
    capabilities: dict[str, Any]
    tools: list[dict[str, Any]]
    resources: list[dict[str, Any]]
    prompts: list[dict[str, Any]]


@dataclass(frozen=True)
class MCPToolCallResult:
    result: dict[str, Any]


class MCPTransportPort(Protocol):
    transport_id: str
    available: bool
    unavailable_reason: str

    def probe(self) -> MCPProbeResult: ...

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolCallResult: ...
