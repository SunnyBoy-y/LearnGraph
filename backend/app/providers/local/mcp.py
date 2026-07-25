from __future__ import annotations

from typing import Any

from app.providers.ports.mcp import (
    MCPProbeResult,
    MCPToolCallResult,
    MCPTransportUnavailable,
)


class UnavailableStdioMCPAdapter:
    """Declaration-only adapter: host subprocess execution is intentionally forbidden."""

    transport_id = "stdio"
    available = False
    unavailable_reason = (
        "No isolated stdio MCP runner is configured; LearnGraph will not launch "
        "arbitrary commands inside the FastAPI host process"
    )

    def probe(self) -> MCPProbeResult:
        raise MCPTransportUnavailable(self.unavailable_reason)

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolCallResult:
        del tool_name, arguments
        raise MCPTransportUnavailable(self.unavailable_reason)
