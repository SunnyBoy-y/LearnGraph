from __future__ import annotations

from typing import Any

from app.providers.ports.mcp import (
    MCPProbeResult,
    MCPToolCallResult,
    MCPTransportUnavailable,
)
from app.providers.ports.mcp_runner import UnavailableIsolatedStdioRunner


class UnavailableStdioMCPAdapter:
    """Declaration-only adapter: host subprocess execution is intentionally forbidden.

    All execution must go through an isolated runner port. Until one is
    configured, probe/call remain unavailable and never spawn host commands.
    """

    transport_id = "stdio"
    available = False
    unavailable_reason = (
        "No isolated stdio MCP runner is configured; LearnGraph will not launch "
        "arbitrary commands inside the FastAPI host process"
    )

    def __init__(self, runner: UnavailableIsolatedStdioRunner | None = None) -> None:
        self._runner = runner or UnavailableIsolatedStdioRunner()

    def probe(self) -> MCPProbeResult:
        raise MCPTransportUnavailable(self.unavailable_reason)

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolCallResult:
        del tool_name, arguments
        raise MCPTransportUnavailable(self.unavailable_reason)

    def runner(self) -> UnavailableIsolatedStdioRunner:
        return self._runner
