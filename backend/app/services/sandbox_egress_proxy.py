from __future__ import annotations

"""Executable outbound egress proxy for sandboxes.

Sandboxes have no direct egress. A deployment that enables reviewed outbound
access routes sandbox traffic through this proxy; every CONNECT is authorized
against a validated ``EgressPolicy`` and the resolved address is re-classified
at connection time. Uncertain, unapproved, private, or expired targets are
refused with an auditable reason.

The proxy is pure ``asyncio`` so it runs on any host (including inside a small
non-root container that is the only component with internet egress).
"""

import asyncio
from typing import Any, Awaitable, Callable

from app.services.sandbox_network_policy import (
    EgressPolicy,
    EgressPolicyDenied,
    AddressResolver,
    authorize_connect,
    system_resolver,
)

DecisionCallback = Callable[[dict[str, Any]], None]

DEFAULT_MAX_HEADER_BYTES = 8 * 1024
DEFAULT_MAX_IDLE_SECONDS = 30.0
DEFAULT_MAX_TUNNEL_BYTES = 256 * 1024 * 1024
DEFAULT_TUNNEL_CHUNK = 64 * 1024


class SandboxEgressProxy:
    """HTTP CONNECT proxy that enforces one reviewed policy.

    ``on_decision`` is an optional audit sink invoked for every allow/deny with
    a small, non-secret payload (policy digest, approval id, host, port, reason).
    """

    def __init__(
        self,
        policy: EgressPolicy,
        *,
        resolver: AddressResolver = system_resolver,
        on_decision: DecisionCallback | None = None,
        max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
        max_idle_seconds: float = DEFAULT_MAX_IDLE_SECONDS,
        max_tunnel_bytes: int = DEFAULT_MAX_TUNNEL_BYTES,
    ) -> None:
        self.policy = policy
        self.resolver = resolver
        self.on_decision = on_decision
        self.max_header_bytes = max_header_bytes
        self.max_idle_seconds = max_idle_seconds
        self.max_tunnel_bytes = max_tunnel_bytes
        self._server: asyncio.AbstractServer | None = None
        self._bound_port: int | None = None

    @property
    def port(self) -> int | None:
        return self._bound_port

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        """Bind the proxy and return the bound port."""
        self._server = await asyncio.start_server(self._handle_client, host=host, port=port)
        sockets = self._server.sockets or ()
        if sockets:
            bound = sockets[0].getsockname()
            self._bound_port = int(bound[1])
        return self._bound_port or 0

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> SandboxEgressProxy:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    def _audit(self, event: dict[str, Any]) -> None:
        if self.on_decision is not None:
            self.on_decision(event)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_address = f"{peer[0]}:{peer[1]}" if peer else "unknown"
        try:
            header = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, ValueError):
            self._audit({"decision": "denied", "reason": "request_header_invalid", "peer": peer_address})
            writer.close()
            return
        if len(header) > self.max_header_bytes:
            self._audit({"decision": "denied", "reason": "request_header_too_large", "peer": peer_address})
            writer.close()
            return

        first_line, *_ = header.split(b"\r\n", 1)
        parts = first_line.decode("latin-1").split()
        if len(parts) != 3 or parts[0] != "CONNECT" or parts[2] != "HTTP/1.1":
            self._audit({"decision": "denied", "reason": "non_connect_method", "peer": peer_address})
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        authority = parts[1]
        try:
            host, port_text = authority.rsplit(":", 1)
            port = int(port_text)
        except ValueError:
            self._audit({"decision": "denied", "reason": "authority_invalid", "target": authority, "peer": peer_address})
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        try:
            target_ip, audit = authorize_connect(
                self.policy,
                host,
                port,
                resolver=self.resolver,
            )
        except EgressPolicyDenied as exc:
            self._audit(
                {
                    "decision": "denied",
                    "target": authority,
                    **(exc.details or {}),
                    "reason": exc.reason,
                    "peer": peer_address,
                }
            )
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        self._audit(
            {
                "decision": "allowed",
                "target": authority,
                "resolved_ip": target_ip,
                **audit,
                "peer": peer_address,
            }
        )
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(target_ip, port),
                timeout=self.max_idle_seconds,
            )
        except Exception:
            self._audit({"decision": "denied", "reason": "upstream_connect_failed", "target": authority})
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        try:
            await self._pump_tunnel(reader, writer, upstream_reader, upstream_writer)
        finally:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except Exception:
                pass

    async def _pump_tunnel(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        total = 0

        async def client_to_upstream() -> None:
            nonlocal total
            while True:
                chunk = await client_reader.read(DEFAULT_TUNNEL_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.max_tunnel_bytes:
                    return
                upstream_writer.write(chunk)
                await upstream_writer.drain()

        async def upstream_to_client() -> None:
            nonlocal total
            while True:
                chunk = await upstream_reader.read(DEFAULT_TUNNEL_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.max_tunnel_bytes:
                    return
                client_writer.write(chunk)
                await client_writer.drain()

        try:
            await asyncio.wait_for(
                asyncio.gather(client_to_upstream(), upstream_to_client()),
                timeout=self.max_idle_seconds,
            )
        except (asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            return
        finally:
            try:
                client_writer.close()
            except Exception:
                pass
