"""MCP bridge validation tests (stage 3: HTTP/SSE MCP via Host Service Bridge).

整体 Docker 部署时，MCP 的 loopback endpoint（如 http://127.0.0.1:3001/mcp）
由 mcp_skills._adapter 改写为 bridge 地址；bridge host（host.docker.internal）
不在 loopback 上，因此 validate_mcp_http_endpoint 必须通过
``allow_private_hosts`` 显式放行该主机的明文 HTTP，同时保持默认拒绝不变。
"""
from __future__ import annotations

import pytest

from app.providers.host_service_resolver import resolve_loopback_url, sanitize_service_id
from app.providers.remote.mcp_http import (
    StreamableHTTPMCPAdapter,
    validate_mcp_http_endpoint,
)
from app.providers.ports.mcp import MCPTransportUnavailable

BRIDGE = "http://host.docker.internal:34115"


@pytest.mark.usefixtures("docker_host_dns")
class TestValidateMcpHttpEndpoint:
    def test_loopback_http_still_allowed(self) -> None:
        assert validate_mcp_http_endpoint("http://127.0.0.1:3001/mcp") == "http://127.0.0.1:3001/mcp"

    def test_public_https_allowed(self) -> None:
        # IP literal so the assertion never depends on ambient DNS.
        assert (
            validate_mcp_http_endpoint("https://1.1.1.1/mcp")
            == "https://1.1.1.1/mcp"
        )

    def test_private_http_denied_by_default(self) -> None:
        # host.docker.internal resolves to a private adapter address (or is
        # synthetic); without an explicit allow it must be refused.
        with pytest.raises(MCPTransportUnavailable):
            validate_mcp_http_endpoint(f"{BRIDGE}/services/mcp-github/mcp")

    def test_private_http_allowed_with_bridge_host(self) -> None:
        assert (
            validate_mcp_http_endpoint(
                f"{BRIDGE}/services/mcp-github/mcp",
                allow_private_hosts=frozenset({"host.docker.internal"}),
            )
            == f"{BRIDGE}/services/mcp-github/mcp"
        )

    def test_adapter_accepts_bridge_host(self) -> None:
        adapter = StreamableHTTPMCPAdapter(
            f"{BRIDGE}/services/mcp-github/mcp",
            bearer_token=None,
            timeout_ms=5000,
            max_response_bytes=65536,
            allow_private_hosts=frozenset({"host.docker.internal"}),
        )
        assert adapter.endpoint_url.startswith(f"{BRIDGE}/services/mcp-github/")

    def test_adapter_still_rejects_without_allow(self) -> None:
        with pytest.raises(MCPTransportUnavailable):
            StreamableHTTPMCPAdapter(
                f"{BRIDGE}/services/mcp-github/mcp",
                bearer_token=None,
                timeout_ms=5000,
                max_response_bytes=65536,
            )


class TestMcpBridgeRewrite:
    def test_loopback_endpoint_rewritten(self) -> None:
        result = resolve_loopback_url(
            service_id=f"mcp-{sanitize_service_id('GitHub MCP')}",
            base_url="http://127.0.0.1:3001/mcp",
            host_bridge_url=BRIDGE,
            deployment_profile="self_hosted_team",
        )
        assert result == f"{BRIDGE}/services/mcp-github-mcp/mcp"

    def test_remote_endpoint_never_rewritten(self) -> None:
        url = "https://mcp.example.com/mcp"
        assert (
            resolve_loopback_url(
                service_id="mcp-x",
                base_url=url,
                host_bridge_url=BRIDGE,
                deployment_profile="self_hosted_team",
            )
            == url
        )
