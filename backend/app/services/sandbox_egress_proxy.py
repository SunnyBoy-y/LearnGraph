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
from typing import Any, Awaitable, Callable, Optional

from app.services.sandbox_network_policy import (
    EgressPolicy,
    EgressPolicyDenied,
    AddressResolver,
    authorize_connect,
    system_resolver,
)

DecisionCallback = Callable[[dict[str, Any]], None]
PolicyProvider = Callable[[], Optional[EgressPolicy]]

# CONNECT header carrying the container's approved policy digest (multi-tenant).
# The fetch runner reads LEARNGRAPH_EGRESS_POLICY_DIGEST from its environment
# and echoes it here so the proxy can resolve the right per-workspace policy.
POLICY_DIGEST_HEADER = b"x-learngraph-policy-digest"

# Standard HTTP CONNECT proxy authentication. Playwright / Chromium cannot send
# custom CONNECT headers, so browser rendering (web_render) authenticates via
# Proxy-Authorization: Basic base64("<digest>:<anything>") — the digest rides
# the username field, which is the only per-workspace credential the proxy
# accepts. Both channels are equivalent and neither is ever a fallback to a
# different policy.
PROXY_AUTH_HEADER = b"proxy-authorization"


def _policy_digest_from_proxy_auth(value: bytes) -> str | None:
    """Extract the policy digest from ``Proxy-Authorization: Basic ...``."""
    import base64

    scheme, _, credential = value.partition(b" ")
    if scheme.strip().lower() != b"basic" or not credential.strip():
        return None
    try:
        decoded = base64.b64decode(credential.strip()).decode("utf-8", errors="ignore")
    except Exception:
        return None
    username, _, _ = decoded.partition(":")
    username = username.strip()
    return username or None

DEFAULT_MAX_HEADER_BYTES = 8 * 1024
DEFAULT_MAX_IDLE_SECONDS = 30.0
DEFAULT_MAX_TUNNEL_BYTES = 256 * 1024 * 1024
DEFAULT_TUNNEL_CHUNK = 64 * 1024


class SandboxEgressProxy:
    """HTTP CONNECT proxy that enforces one reviewed policy per connection.

    Policy resolution, most specific first:
    1. ``policy_registry`` (multi-tenant): a dict keyed by policy digest. The
       client must identify itself with the ``X-LearnGraph-Policy-Digest``
       CONNECT header (the container's ``LEARNGRAPH_EGRESS_POLICY_DIGEST``);
       an absent or unknown digest is denied with 403.
    2. ``policy_provider``: a zero-argument callable returning the policy for
       the current connection (reload seam for single-tenant deployments).
    3. ``policy``: one immutable policy for the simplest deployments.

    ``on_decision`` is an optional audit sink invoked for every allow/deny with
    a small, non-secret payload (policy digest, approval id, host, port, reason).
    """

    def __init__(
        self,
        policy: EgressPolicy | None = None,
        *,
        policy_provider: PolicyProvider | None = None,
        policy_registry: dict[str, EgressPolicy] | None = None,
        resolver: AddressResolver = system_resolver,
        on_decision: DecisionCallback | None = None,
        max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
        max_idle_seconds: float = DEFAULT_MAX_IDLE_SECONDS,
        max_tunnel_bytes: int = DEFAULT_MAX_TUNNEL_BYTES,
    ) -> None:
        if policy is None and policy_provider is None and not policy_registry:
            raise ValueError("SandboxEgressProxy requires a policy, policy_provider, or policy_registry")
        self.policy = policy
        self.policy_provider = policy_provider
        self.policy_registry = policy_registry
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

    def _resolve_policy(self, digest: str | None) -> EgressPolicy | None:
        if self.policy_registry is not None:
            # Multi-tenant: the digest IS the identity. Absent or unknown digest
            # fails closed (403) — never falls back to another policy.
            if not digest or digest not in self.policy_registry:
                return None
            return self.policy_registry[digest]
        if self.policy_provider is not None:
            return self.policy_provider()
        return self.policy

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

        first_line, *rest_lines = header.split(b"\r\n")
        parts = first_line.decode("latin-1").split()
        if len(parts) != 3 or parts[0] != "CONNECT" or parts[2] != "HTTP/1.1":
            self._audit({"decision": "denied", "reason": "non_connect_method", "peer": peer_address})
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        # The policy digest is the only per-workspace credential. It may ride
        # the X-LearnGraph-Policy-Digest CONNECT header (httpx fetch runner) or
        # Proxy-Authorization Basic (browser rendering via Chromium); both must
        # resolve to a registered policy or the CONNECT fails closed.
        policy_digest: str | None = None
        for line in rest_lines:
            if b":" not in line:
                continue
            name, _, value = line.partition(b":")
            lowered = name.strip().lower()
            if lowered == POLICY_DIGEST_HEADER:
                policy_digest = value.strip().decode("latin-1") or None
                break
            if lowered == PROXY_AUTH_HEADER:
                auth_digest = _policy_digest_from_proxy_auth(value.strip())
                if auth_digest is not None:
                    policy_digest = auth_digest
                break

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

        resolved_policy = self._resolve_policy(policy_digest)
        if resolved_policy is None:
            self._audit(
                {
                    "decision": "denied",
                    "reason": "policy_unavailable",
                    "target": authority,
                    "peer": peer_address,
                    "policy_digest": policy_digest or None,
                }
            )
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        try:
            target_ip, audit = authorize_connect(
                resolved_policy,
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
