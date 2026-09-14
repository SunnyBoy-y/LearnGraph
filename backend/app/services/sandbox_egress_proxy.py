from __future__ import annotations

"""Executable outbound egress proxy for sandboxes.

Sandboxes have no direct egress. A deployment that enables reviewed outbound
access routes sandbox traffic through this proxy; every HTTPS CONNECT or
bounded HTTP GET/HEAD is authorized against a validated ``EgressPolicy`` and
the resolved address is re-classified at connection time. Uncertain,
unapproved, private, or expired targets are refused with an auditable reason.

The proxy is pure ``asyncio`` so it runs on any host (including inside a small
non-root container that is the only component with internet egress).
"""

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass
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


@dataclass(slots=True)
class _PolicyUsage:
    requests: int = 0
    bytes_total: int = 0
    active: int = 0


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
        if policy is None and policy_provider is None and policy_registry is None:
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
        self._usage_lock = threading.Lock()
        self._usage: dict[str, _PolicyUsage] = {}

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

    def _reserve_policy_attempt(
        self, policy: EgressPolicy
    ) -> tuple[str | None, int | None]:
        """Reserve one attempt and return ``(blocked_reason, remaining_bytes)``.

        Callers MUST pair a successful reservation with
        ``_finish_policy_attempt`` in ``finally``.
        """
        if policy.max_bytes is not None and policy.max_bytes <= 0:
            return "byte_quota_exceeded", None
        with self._usage_lock:
            # Drop inactive entries whose immutable revision is no longer in
            # the registry (normal policy rotation). Active tunnels retain
            # their state until they finish.
            if self.policy_registry is not None:
                live = set(self.policy_registry)
                for digest in list(self._usage):
                    if digest not in live and self._usage[digest].active == 0:
                        del self._usage[digest]
            usage = self._usage.setdefault(policy.digest, _PolicyUsage())
            if policy.max_requests is not None and usage.requests >= policy.max_requests:
                return "request_quota_exceeded", None
            if policy.max_bytes is not None and usage.bytes_total >= policy.max_bytes:
                return "byte_quota_exceeded", None
            if policy.max_concurrency is not None and usage.active >= policy.max_concurrency:
                return "concurrency_quota_exceeded", None
            usage.requests += 1
            usage.active += 1
            remaining_bytes = (
                policy.max_bytes - usage.bytes_total
                if policy.max_bytes is not None
                else None
            )
        return None, remaining_bytes

    def _finish_policy_attempt(
        self,
        policy: EgressPolicy,
        *,
        bytes_in: int,
        bytes_out: int,
    ) -> None:
        with self._usage_lock:
            usage = self._usage.setdefault(policy.digest, _PolicyUsage())
            usage.active = max(0, usage.active - 1)
            usage.bytes_total += max(0, int(bytes_in)) + max(0, int(bytes_out))

    @staticmethod
    def _safe_forward_headers(lines: list[bytes], *, host: str) -> list[bytes]:
        allowed = {"accept", "accept-language", "cache-control", "pragma", "user-agent"}
        forwarded: list[bytes] = [f"Host: {host}".encode("latin-1")]
        for line in lines:
            if b":" not in line:
                continue
            name, _, value = line.partition(b":")
            if name.strip().lower().decode("latin-1") not in allowed:
                continue
            forwarded.append(name.strip() + b": " + value.strip())
        forwarded.append(b"Connection: close")
        forwarded.append(b"Accept-Encoding: identity")
        return forwarded

    async def _handle_http_proxy_request(
        self,
        method: str,
        target: str,
        header_lines: list[bytes],
        writer: asyncio.StreamWriter,
        policy_digest: str | None,
        peer_address: str,
    ) -> None:
        from urllib.parse import urlsplit

        parsed = urlsplit(target)
        if (
            parsed.scheme.casefold() != "http"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            self._audit(
                {
                    "decision": "denied",
                    "method": method,
                    "reason": "http_proxy_target_invalid",
                    "target": "<redacted>",
                    "peer": peer_address,
                }
            )
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        try:
            port = parsed.port or 80
        except ValueError:
            port = -1
        host = parsed.hostname.casefold().rstrip(".")
        audit_target = f"http://{host}:{port}"

        policy = self._resolve_policy(policy_digest)
        if policy is None:
            self._audit(
                {
                    "decision": "denied",
                    "reason": "policy_unavailable",
                    "method": method,
                    "target": audit_target,
                    "peer": peer_address,
                }
            )
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        quota_block, remaining_bytes = self._reserve_policy_attempt(policy)
        if quota_block is not None:
            self._audit(
                {
                    "decision": "denied",
                    "reason": quota_block,
                    "method": method,
                    "target": audit_target,
                    "capability": policy.capability.value,
                    "workspace_id": policy.workspace_id,
                    "policy_digest": policy.digest,
                    "peer": peer_address,
                }
            )
            writer.write(b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        request_id = uuid.uuid4().hex
        started = time.monotonic()
        bytes_in = 0
        bytes_out = 0
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            try:
                target_ip, audit = authorize_connect(
                    policy,
                    host,
                    port,
                    protocol="http",
                    resolver=self.resolver,
                )
            except EgressPolicyDenied as exc:
                self._audit(
                    {
                        "request_id": request_id,
                        "decision": "denied",
                        "method": method,
                        "target": audit_target,
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
                    "request_id": request_id,
                    "decision": "allowed",
                    "method": method,
                    "target": audit_target,
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
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                writer.close()
                return

            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            try:
                request_line = f"{method} {path} HTTP/1.1".encode("latin-1")
            except UnicodeEncodeError:
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            request_head = (
                b"\r\n".join(
                    [
                        request_line,
                        *self._safe_forward_headers(
                            header_lines,
                            host=str(audit.get("host") or host),
                        ),
                    ]
                )
                + b"\r\n\r\n"
            )
            upstream_writer.write(request_head)
            await upstream_writer.drain()
            bytes_in = len(request_head)

            limit = (
                min(self.max_tunnel_bytes, remaining_bytes)
                if remaining_bytes is not None
                else self.max_tunnel_bytes
            )
            byte_limit_exceeded = False
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        upstream_reader.read(DEFAULT_TUNNEL_CHUNK),
                        timeout=self.max_idle_seconds,
                    )
                except (asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError):
                    break
                if not chunk:
                    break
                bytes_out += len(chunk)
                if bytes_in + bytes_out > limit:
                    byte_limit_exceeded = True
                    break
                writer.write(chunk)
                await writer.drain()
            self._audit(
                {
                    "request_id": request_id,
                    "decision": "completed",
                    "method": method,
                    "capability": policy.capability.value,
                    "workspace_id": policy.workspace_id,
                    "policy_digest": policy.digest,
                    "target": audit_target,
                    "resolved_ip": target_ip,
                    "bytes_in": bytes_in,
                    "bytes_out": bytes_out,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "blocked_reason": ("byte_quota_exceeded" if byte_limit_exceeded else None),
                }
            )
        finally:
            self._finish_policy_attempt(policy, bytes_in=bytes_in, bytes_out=bytes_out)
            if upstream_writer is not None:
                upstream_writer.close()
                try:
                    await upstream_writer.wait_closed()
                except Exception:
                    pass
            try:
                writer.close()
            except Exception:
                pass

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
        if (
            len(parts) != 3
            or parts[0] not in {"CONNECT", "GET", "HEAD"}
            or parts[2] != "HTTP/1.1"
        ):
            self._audit({"decision": "denied", "reason": "method_not_allowed", "peer": peer_address})
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

        if parts[0] in {"GET", "HEAD"}:
            await self._handle_http_proxy_request(
                parts[0],
                parts[1],
                rest_lines,
                writer,
                policy_digest,
                peer_address,
            )
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

        quota_block, remaining_bytes = self._reserve_policy_attempt(resolved_policy)
        if quota_block is not None:
            self._audit(
                {
                    "decision": "denied",
                    "reason": quota_block,
                    "target": authority,
                    "capability": resolved_policy.capability.value,
                    "workspace_id": resolved_policy.workspace_id,
                    "policy_digest": resolved_policy.digest,
                    "peer": peer_address,
                }
            )
            writer.write(b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        request_id = uuid.uuid4().hex
        started = time.monotonic()
        bytes_in = 0
        bytes_out = 0
        try:
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
                        "request_id": request_id,
                        "decision": "denied",
                        "method": "CONNECT",
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
                    "request_id": request_id,
                    "decision": "allowed",
                    "method": "CONNECT",
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
                self._audit(
                    {
                        "request_id": request_id,
                        "decision": "denied",
                        "method": "CONNECT",
                        "reason": "upstream_connect_failed",
                        "capability": resolved_policy.capability.value,
                        "workspace_id": resolved_policy.workspace_id,
                        "policy_digest": resolved_policy.digest,
                        "target": authority,
                    }
                )
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                writer.close()
                return

            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            try:
                bytes_in, bytes_out, byte_limit_exceeded = await self._pump_tunnel(
                    reader,
                    writer,
                    upstream_reader,
                    upstream_writer,
                    max_bytes=(
                        min(self.max_tunnel_bytes, remaining_bytes)
                        if remaining_bytes is not None
                        else self.max_tunnel_bytes
                    ),
                )
            finally:
                upstream_writer.close()
                try:
                    await upstream_writer.wait_closed()
                except Exception:
                    pass
            self._audit(
                {
                    "request_id": request_id,
                    "decision": "completed",
                    "method": "CONNECT",
                    "capability": resolved_policy.capability.value,
                    "workspace_id": resolved_policy.workspace_id,
                    "policy_digest": resolved_policy.digest,
                    "target": authority,
                    "resolved_ip": target_ip,
                    "bytes_in": bytes_in,
                    "bytes_out": bytes_out,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "blocked_reason": ("byte_quota_exceeded" if byte_limit_exceeded else None),
                }
            )
        finally:
            self._finish_policy_attempt(
                resolved_policy,
                bytes_in=bytes_in,
                bytes_out=bytes_out,
            )

    async def _pump_tunnel(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
        *,
        max_bytes: int,
    ) -> tuple[int, int, bool]:
        bytes_in = 0
        bytes_out = 0
        byte_limit_exceeded = False

        async def client_to_upstream() -> None:
            nonlocal bytes_in, byte_limit_exceeded
            while True:
                chunk = await client_reader.read(DEFAULT_TUNNEL_CHUNK)
                if not chunk:
                    break
                bytes_in += len(chunk)
                if bytes_in + bytes_out > max_bytes:
                    byte_limit_exceeded = True
                    client_writer.close()
                    upstream_writer.close()
                    return
                upstream_writer.write(chunk)
                await upstream_writer.drain()

        async def upstream_to_client() -> None:
            nonlocal bytes_out, byte_limit_exceeded
            while True:
                chunk = await upstream_reader.read(DEFAULT_TUNNEL_CHUNK)
                if not chunk:
                    break
                bytes_out += len(chunk)
                if bytes_in + bytes_out > max_bytes:
                    byte_limit_exceeded = True
                    client_writer.close()
                    upstream_writer.close()
                    return
                client_writer.write(chunk)
                await client_writer.drain()

        try:
            await asyncio.wait_for(
                asyncio.gather(client_to_upstream(), upstream_to_client()),
                timeout=self.max_idle_seconds,
            )
        except (asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                client_writer.close()
            except Exception:
                pass
        return bytes_in, bytes_out, byte_limit_exceeded
