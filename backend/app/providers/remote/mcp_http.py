from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from app.providers.ports.mcp import (
    MCPProbeResult,
    MCPProtocolFailure,
    MCPResponseTooLarge,
    MCPToolCallResult,
    MCPTransportFailure,
    MCPTransportTimeout,
    MCPTransportUnavailable,
)


PROTOCOL_VERSION = "2025-11-25"
COMPATIBLE_PROTOCOL_VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26"}


def validate_mcp_http_endpoint(
    endpoint_url: str, *, allow_private_hosts: frozenset[str] = frozenset()
) -> str:
    """Reject credentials, fragments and non-loopback plaintext/private targets.

    Localhost HTTP is allowed for a deliberately configured local MCP service.
    Public endpoints must use HTTPS. Redirects remain disabled in the adapter.

    ``allow_private_hosts`` names explicitly trusted hosts (the Host Service
    Bridge gateway, e.g. ``host.docker.internal``) whose plaintext HTTP is
    accepted even though they do not resolve to loopback — the bridge itself
    is the authorized, audited path to real-machine loopback services.
    """

    parsed = urlsplit(endpoint_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MCPTransportUnavailable("MCP endpoint must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise MCPTransportUnavailable(
            "MCP endpoint must not contain credentials or a URL fragment"
        )
    host = parsed.hostname.casefold()
    try:
        addresses = {
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise MCPTransportUnavailable("MCP endpoint hostname could not be resolved") from exc
    is_loopback = bool(addresses) and all(address.is_loopback for address in addresses)
    explicitly_allowed = host in allow_private_hosts
    if parsed.scheme == "http" and not is_loopback and not explicitly_allowed:
        raise MCPTransportUnavailable(
            "Plain HTTP MCP endpoints are allowed only on loopback addresses"
        )
    if not is_loopback and any(not address.is_global for address in addresses) and not explicitly_allowed:
        raise MCPTransportUnavailable(
            "MCP endpoint resolved to a private, link-local, or reserved address"
        )
    return endpoint_url.strip()


class StreamableHTTPMCPAdapter:
    transport_id = "streamable_http"
    available = True
    unavailable_reason = ""

    def __init__(
        self,
        endpoint_url: str,
        *,
        bearer_token: str | None,
        timeout_ms: int,
        max_response_bytes: int,
        allow_private_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self.endpoint_url = validate_mcp_http_endpoint(
            endpoint_url, allow_private_hosts=allow_private_hosts
        )
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_ms / 1000
        self.max_response_bytes = max_response_bytes

    def probe(self) -> MCPProbeResult:
        try:
            with self._client() as client:
                protocol_version, session_id, initialize_result = self._initialize(client)
                self._initialized(client, protocol_version, session_id)
                capabilities = initialize_result.get("capabilities") or {}
                if not isinstance(capabilities, dict):
                    raise MCPProtocolFailure("MCP initialize capabilities must be an object")
                tools = self._list_all(client, "tools/list", "tools", protocol_version, session_id)
                resources = (
                    self._list_all(
                        client, "resources/list", "resources", protocol_version, session_id
                    )
                    if "resources" in capabilities
                    else []
                )
                prompts = (
                    self._list_all(
                        client, "prompts/list", "prompts", protocol_version, session_id
                    )
                    if "prompts" in capabilities
                    else []
                )
                return MCPProbeResult(
                    protocol_version=protocol_version,
                    server_identity=self._object(initialize_result.get("serverInfo"), "serverInfo"),
                    capabilities=capabilities,
                    tools=tools,
                    resources=resources,
                    prompts=prompts,
                )
        except MCPTransportFailure:
            raise
        except httpx.TimeoutException as exc:
            raise MCPTransportTimeout("MCP HTTP request exceeded the configured timeout") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                raise MCPTransportUnavailable(
                    f"MCP endpoint rejected the configured authorization reference ({status})"
                ) from exc
            raise MCPTransportUnavailable(f"MCP endpoint returned HTTP {status}") from exc
        except httpx.HTTPError as exc:
            raise MCPTransportUnavailable("MCP HTTP endpoint is unavailable") from exc

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolCallResult:
        try:
            with self._client() as client:
                protocol_version, session_id, _ = self._initialize(client)
                self._initialized(client, protocol_version, session_id)
                result, _ = self._request(
                    client,
                    "tools/call",
                    {"name": tool_name, "arguments": arguments},
                    protocol_version=protocol_version,
                    session_id=session_id,
                )
                return MCPToolCallResult(result=self._object(result, "tools/call result"))
        except MCPTransportFailure:
            raise
        except httpx.TimeoutException as exc:
            raise MCPTransportTimeout("MCP tool call exceeded the configured timeout") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                raise MCPTransportUnavailable(
                    f"MCP endpoint rejected the configured authorization reference ({status})"
                ) from exc
            raise MCPTransportUnavailable(f"MCP endpoint returned HTTP {status}") from exc
        except httpx.HTTPError as exc:
            raise MCPTransportUnavailable("MCP HTTP endpoint is unavailable") from exc

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(self.timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )

    def _base_headers(self, protocol_version: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if protocol_version:
            headers["MCP-Protocol-Version"] = protocol_version
        return headers

    def _initialize(self, client: httpx.Client) -> tuple[str, str | None, dict[str, Any]]:
        result, response = self._request(
            client,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "LearnGraph", "version": "0.1.0"},
            },
        )
        initialize = self._object(result, "initialize result")
        protocol_version = str(initialize.get("protocolVersion") or "")
        if protocol_version not in COMPATIBLE_PROTOCOL_VERSIONS:
            raise MCPProtocolFailure(
                f"MCP server negotiated unsupported protocol version {protocol_version or '<missing>'}"
            )
        return protocol_version, response.headers.get("Mcp-Session-Id"), initialize

    def _initialized(
        self,
        client: httpx.Client,
        protocol_version: str,
        session_id: str | None,
    ) -> None:
        headers = self._base_headers(protocol_version)
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        with client.stream("POST", self.endpoint_url, headers=headers, json=payload) as response:
            if response.status_code not in {200, 202, 204}:
                response.raise_for_status()
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self.max_response_bytes:
                    raise MCPResponseTooLarge(
                        "MCP initialized notification response exceeded the configured limit"
                    )

    def _list_all(
        self,
        client: httpx.Client,
        method: str,
        result_key: str,
        protocol_version: str,
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(10):
            params = {"cursor": cursor} if cursor else {}
            result, _ = self._request(
                client,
                method,
                params,
                protocol_version=protocol_version,
                session_id=session_id,
            )
            result_object = self._object(result, f"{method} result")
            page = result_object.get(result_key) or []
            if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
                raise MCPProtocolFailure(f"{method} returned an invalid {result_key} list")
            values.extend(page)
            if len(values) > 500:
                raise MCPProtocolFailure(f"{method} exceeded the 500-item safety limit")
            next_cursor = result_object.get("nextCursor")
            if not next_cursor:
                return values
            cursor = str(next_cursor)
        raise MCPProtocolFailure(f"{method} exceeded the 10-page safety limit")

    def _request(
        self,
        client: httpx.Client,
        method: str,
        params: dict[str, Any],
        *,
        protocol_version: str | None = None,
        session_id: str | None = None,
    ) -> tuple[Any, httpx.Response]:
        request_id = str(uuid4())
        headers = self._base_headers(protocol_version)
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        with client.stream("POST", self.endpoint_url, headers=headers, json=payload) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > self.max_response_bytes:
                    raise MCPResponseTooLarge(
                        "MCP response exceeded the configured result-size limit"
                    )
            content_type = response.headers.get("content-type", "").casefold()
            try:
                decoded = bytes(body).decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise MCPProtocolFailure("MCP endpoint returned non-UTF-8 data") from exc
            message = (
                self._parse_sse(decoded, request_id)
                if "text/event-stream" in content_type
                else self._parse_json(decoded)
            )
            if message.get("id") != request_id:
                raise MCPProtocolFailure("MCP response id did not match the request")
            if "error" in message:
                error = message.get("error")
                raise MCPProtocolFailure(
                    "MCP server returned a JSON-RPC error: "
                    + (str(error.get("message")) if isinstance(error, dict) else "unknown")
                )
            if "result" not in message:
                raise MCPProtocolFailure("MCP JSON-RPC response did not contain result")
            return message["result"], response

    @staticmethod
    def _parse_json(decoded: str) -> dict[str, Any]:
        try:
            value = json.loads(decoded)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MCPProtocolFailure("MCP endpoint returned invalid JSON") from exc
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
            raise MCPProtocolFailure("MCP endpoint returned an invalid JSON-RPC message")
        return value

    def _parse_sse(self, decoded: str, request_id: str) -> dict[str, Any]:
        for block in decoded.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(
                line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
            )
            if not data:
                continue
            message = self._parse_json(data)
            if message.get("id") == request_id:
                return message
        raise MCPProtocolFailure("MCP SSE response did not contain the request result")

    @staticmethod
    def _object(value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise MCPProtocolFailure(f"MCP {label} must be an object")
        return value
