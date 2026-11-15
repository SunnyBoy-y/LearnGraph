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
    MCPRemoteError,
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
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.endpoint_url = validate_mcp_http_endpoint(
            endpoint_url, allow_private_hosts=allow_private_hosts
        )
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_ms / 1000
        self.max_response_bytes = max_response_bytes
        self.extra_headers = dict(extra_headers or {})
        self.allow_private_hosts = allow_private_hosts

    def probe(self) -> MCPProbeResult:
        try:
            with self._session() as (client, protocol_version, session_id, initialize_result):
                capabilities = initialize_result.get("capabilities") or {}
                if not isinstance(capabilities, dict):
                    raise MCPProtocolFailure("MCP initialize capabilities must be an object")
                tools = (self._list_all(client, "tools/list", "tools", protocol_version, session_id)
                         if "tools" in capabilities else [])
                resources = (
                    self._list_all(
                        client, "resources/list", "resources", protocol_version, session_id
                    )
                    if "resources" in capabilities
                    else []
                )
                if "resources" in capabilities:
                    try:
                        templates = self._list_all(client, "resources/templates/list", "resourceTemplates", protocol_version, session_id)
                    except MCPRemoteError as exc:
                        if exc.rpc_code != -32601:
                            raise
                    else:
                        resources.extend({**item, "uri": item["uriTemplate"]} for item in templates if isinstance(item.get("uriTemplate"), str))
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
        return self._call_method("tools/call", {"name": tool_name, "arguments": arguments})

    def read_resource(self, uri: str) -> MCPToolCallResult:
        return self._call_method("resources/read", {"uri": uri})

    def get_prompt(self, name: str, arguments: dict[str, str]) -> MCPToolCallResult:
        return self._call_method("prompts/get", {"name": name, "arguments": arguments})

    def _call_method(self, method: str, params: dict[str, Any]) -> MCPToolCallResult:
        try:
            with self._session() as (client, protocol_version, session_id, _):
                from app.providers.mcp_metadata import MODERN_PROTOCOL_VERSION
                if protocol_version == MODERN_PROTOCOL_VERSION and method == "tools/call":
                    tools = self._list_all(client, "tools/list", "tools", protocol_version, session_id)
                    selected = next((tool for tool in tools if tool.get("name") == params["name"]), None)
                    if selected is None:
                        raise MCPProtocolFailure("MCP tool is unavailable or has invalid routing metadata")
                    self._tool_schema = selected.get("inputSchema", {})
                result, _ = self._request(
                    client,
                    method,
                    params,
                    protocol_version=protocol_version,
                    session_id=session_id,
                )
                result = self._object(result, f"{method} result")
                if result.get("resultType") not in {None, "complete"}:
                    raise MCPProtocolFailure("MCP server requested an unadvertised client interaction; no action was automatically replayed")
                return MCPToolCallResult(result=result)
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

    from contextlib import contextmanager

    @contextmanager
    def _session(self):
        import time
        with self._client() as client:
            self._deadline = time.monotonic() + self.timeout_seconds
            from app.providers.mcp_metadata import MODERN_PROTOCOL_VERSION, META_PREFIX
            modern = None
            try:
                modern, _ = self._request(client, "server/discover", {}, protocol_version=MODERN_PROTOCOL_VERSION)
            except MCPRemoteError as exc:
                if exc.rpc_code in {-32020, -32021, -32022}:
                    raise  # A recognized modern error must never trigger downgrade.
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {400, 404, 405}:
                    raise
            except MCPProtocolFailure:
                if getattr(self, "_last_status", None) != 400:
                    raise
            if modern is not None:
                modern = self._object(modern, "server/discover result")
                if MODERN_PROTOCOL_VERSION not in modern.get("supportedVersions", []):
                    raise MCPProtocolFailure("No mutually supported modern MCP protocol version")
                version, session_id = MODERN_PROTOCOL_VERSION, None
                initialize = {"capabilities": modern.get("capabilities", {}),
                              "serverInfo": (modern.get("_meta") or {}).get(META_PREFIX+"serverInfo", {})}
            else:
                version, session_id, initialize = self._initialize(client)
            try:
                if version != MODERN_PROTOCOL_VERSION:
                    self._initialized(client, version, session_id)
                yield client, version, session_id, initialize
            finally:
                if session_id:
                    headers = self._base_headers(version)
                    headers["Mcp-Session-Id"] = session_id
                    try:
                        # DELETE is optional. Never replace a business failure with a cleanup error.
                        with client.stream("DELETE", self.endpoint_url, headers=headers, timeout=2):
                            pass
                    except httpx.HTTPError:
                        pass

    def _client(self) -> httpx.Client:
        from app.providers.mcp_network import PinnedMCPTransport
        return httpx.Client(
            transport=PinnedMCPTransport(httpx.URL(self.endpoint_url).raw_host.decode("ascii"), self.allow_private_hosts),
            timeout=httpx.Timeout(self.timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )

    def _base_headers(self, protocol_version: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        # Bridge-hop credential (X-LearnGraph-Host-Bridge-Token) goes first so
        # it can never override the target MCP server's Authorization.
        headers.update(self.extra_headers)
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
        import time
        deadline = getattr(self, "_deadline", time.monotonic() + self.timeout_seconds)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MCPTransportTimeout("MCP session exceeded its total deadline")
        with client.stream("POST", self.endpoint_url, headers=headers, json=payload, timeout=remaining) as response:
            if response.status_code not in {200, 202, 204}:
                response.raise_for_status()
            size = 0
            for chunk in response.iter_bytes():
                if time.monotonic() > deadline:
                    raise MCPTransportTimeout("MCP session exceeded its total deadline")
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
        seen: set[str] = set()
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
            page = result_object.get(result_key)
            if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
                raise MCPProtocolFailure(f"{method} returned an invalid {result_key} list")
            if method == "tools/list":
                from app.providers.mcp_metadata import header_paths
                import logging
                filtered = []
                for tool in page:
                    try:
                        header_paths(tool.get("inputSchema", {}))
                    except MCPProtocolFailure:
                        logging.getLogger(__name__).warning("Rejected MCP tool with invalid HTTP routing annotations")
                    else:
                        filtered.append(tool)
                page = filtered
            values.extend(page)
            if len(values) > 500:
                raise MCPProtocolFailure(f"{method} exceeded the 500-item safety limit")
            next_cursor = result_object.get("nextCursor")
            if next_cursor is None:
                return values
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
                raise MCPProtocolFailure(f"{method} returned an invalid or repeated pagination cursor")
            seen.add(next_cursor)
            cursor = next_cursor
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
        from app.providers.mcp_metadata import MODERN_PROTOCOL_VERSION, request_params, routing_headers
        if protocol_version == MODERN_PROTOCOL_VERSION:
            headers.update(routing_headers(method, params, getattr(self, "_tool_schema", None)))
            params = request_params(params, protocol_version)
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        import time
        deadline = getattr(self, "_deadline", time.monotonic() + self.timeout_seconds)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MCPTransportTimeout("MCP session exceeded its total deadline")
        with client.stream("POST", self.endpoint_url, headers=headers, json=payload, timeout=remaining) as response:
            self._last_status = response.status_code
            if response.status_code != 400:
                response.raise_for_status()
            import codecs
            body = bytearray()
            event_stream = "text/event-stream" in response.headers.get("content-type", "").casefold()
            decoder = codecs.getincrementaldecoder("utf-8")("strict")
            buffer = ""
            message = None
            try:
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise MCPTransportTimeout("MCP response exceeded its total deadline")
                    body.extend(chunk)
                    if len(body) > self.max_response_bytes:
                        raise MCPResponseTooLarge("MCP response exceeded the configured result-size limit")
                    if event_stream:
                        buffer += decoder.decode(chunk)
                        # Split only complete frames. Keep CRLF intact across chunks.
                        import re
                        while (separator := re.search(r"\r?\n\r?\n", buffer)) is not None:
                            frame, buffer = buffer[:separator.start()], buffer[separator.end():]
                            data = "\n".join(line[5:].lstrip(" ") for line in frame.splitlines() if line.startswith("data:"))
                            if not data:
                                continue
                            candidate = self._parse_json(data)
                            if candidate.get("id") == request_id:
                                message = candidate
                                break
                        if message is not None:
                            break  # A live SSE connection need not close after its response.
                if message is None:
                    message = self._parse_sse(buffer + decoder.decode(b"", final=True), request_id) if event_stream else self._parse_json(bytes(body).decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise MCPProtocolFailure("MCP endpoint returned non-UTF-8 data") from exc
            if ("result" in message) == ("error" in message):
                raise MCPProtocolFailure("MCP response must contain exactly one of result or error")
            if message.get("id") != request_id:
                raise MCPProtocolFailure("MCP response id did not match the request")
            if "error" in message:
                error = message.get("error")
                raise MCPRemoteError(error.get("code") if isinstance(error, dict) else None,
                                     error.get("data") if isinstance(error, dict) else None)
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
