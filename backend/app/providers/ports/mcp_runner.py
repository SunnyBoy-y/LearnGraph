from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.providers.ports.mcp import MCPProbeResult, MCPToolCallResult, MCPTransportUnavailable


class MCPRunnerPort(Protocol):
    """Isolated runner contract. The FastAPI process only speaks over this port."""

    transport_id: str
    available: bool

    def provision(self, launch_spec: dict[str, Any]) -> dict[str, Any]:
        """Create or reuse an isolated runtime for one approved MCP server."""

    def probe(self, launch_spec: dict[str, Any]) -> MCPProbeResult:
        """Health-check an approved runtime without launching host subprocesses."""

    def invoke(
        self,
        launch_spec: dict[str, Any],
        *,
        tool_name: str,
        arguments: dict[str, Any],
        credential_envelope: dict[str, Any] | None = None,
    ) -> MCPToolCallResult:
        """Execute one tool call inside the isolated runner."""

    def terminate(self, launch_spec: dict[str, Any]) -> None:
        """Stop and clean up the isolated runtime."""


@dataclass(frozen=True)
class IsolatedStdioLaunchSpec:
    """Immutable, reviewed launch envelope for an MCP stdio server."""

    server_id: str
    workspace_id: str
    image_digest: str
    command: tuple[str, ...]
    protocol_version: str
    capability_hash: str
    resource_limits: dict[str, Any]
    network_mode: str = "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "workspace_id": self.workspace_id,
            "image_digest": self.image_digest,
            "command": list(self.command),
            "protocol_version": self.protocol_version,
            "capability_hash": self.capability_hash,
            "resource_limits": self.resource_limits,
            "network_mode": self.network_mode,
        }


class UnavailableIsolatedStdioRunner:
    """Default runner: stdio remains unavailable until a real isolated backend is configured."""

    transport_id = "stdio"
    available = False
    unavailable_reason = (
        "No isolated stdio MCP runner is configured; LearnGraph will not launch "
        "arbitrary commands inside the FastAPI host process"
    )

    def provision(self, launch_spec: dict[str, Any]) -> dict[str, Any]:
        del launch_spec
        raise MCPTransportUnavailable(self.unavailable_reason)

    def probe(self, launch_spec: dict[str, Any]) -> MCPProbeResult:
        del launch_spec
        raise MCPTransportUnavailable(self.unavailable_reason)

    def invoke(
        self,
        launch_spec: dict[str, Any],
        *,
        tool_name: str,
        arguments: dict[str, Any],
        credential_envelope: dict[str, Any] | None = None,
    ) -> MCPToolCallResult:
        del launch_spec, tool_name, arguments, credential_envelope
        raise MCPTransportUnavailable(self.unavailable_reason)

    def terminate(self, launch_spec: dict[str, Any]) -> None:
        del launch_spec
        raise MCPTransportUnavailable(self.unavailable_reason)
