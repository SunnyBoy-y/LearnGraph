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


class MCPRemoteError(MCPProtocolFailure):
    def __init__(self, rpc_code: int | None, data: Any = None):
        super().__init__(f"MCP server returned JSON-RPC error {rpc_code}")
        self.rpc_code, self.data = rpc_code, data


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

    def read_resource(self, uri: str) -> MCPToolCallResult: ...

    def get_prompt(self, name: str, arguments: dict[str, str]) -> MCPToolCallResult: ...
