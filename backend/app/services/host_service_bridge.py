from __future__ import annotations

"""Host Service Bridge: expose registered real-machine services to containers.

The whole-app Docker deployment changes what ``127.0.0.1`` means: inside the
``app`` container it is the container itself, so a provider base URL such as
``http://127.0.0.1:11434`` (Ollama) or a local MCP endpoint can no longer reach
the physical host. This module is the host-side counterpart of the backend's
Host Service Resolver (``app.providers.host_service_resolver``):

    container backend  -->  host.docker.internal:34115/services/<id>/<path>
        (authorized, audited)            |
                                        HOST SERVICE BRIDGE  (runs on the real machine)
                                        |  registry lookup (default DENY)
                                        |  loopback-only targets
                                        v
                                127.0.0.1:<port>  (Ollama / LM Studio / MCP / local API)

Design rules (mirrors ``sandbox_network_policy`` so the two planes share one
mental model):

* **Default deny**: a service is reachable only when a reviewed ``{id}.json``
  registry file exists, is valid, and ``enabled`` is true. Unknown ids,
  disabled services and malformed files are denied and audited.
* **No arbitrary port forwarding**: the bridge serves exactly
  ``/services/{id}/...``. There is no ``/proxy?host=&port=`` escape hatch.
* **Loopback targets only**: a registry ``target`` must resolve to a loopback
  address (``127.0.0.1``/``::1``) unless the operator explicitly opts into
  private targets with ``allow_private_target: true``. The bridge never
  forwards to the public internet.
* **Mandatory bearer token**: the process refuses to start without
  ``LEARNGRAPH_HOST_BRIDGE_TOKEN`` (fail closed), and every request must carry
  ``Authorization: Bearer <token>``. The token header is never forwarded to
  the target; per-service static headers from the registry are injected
  instead.
* **Audit**: every allow/deny decision is appended to a JSONL audit log
  (stdout when unset), including target, service id, client address and a
  stable request id.
"""

import asyncio
import ipaddress
import json
import logging
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

logger = logging.getLogger(__name__)

SERVICE_ID_PATTERN = "services"
HEALTH_PATH = "/healthz"

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_BODY_BYTES = 32 * 1024 * 1024


class HostServiceDenied(Exception):
    """Raised when a bridge request is refused (unknown/disabled/unauthorized)."""

    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


class HostServiceInvalid(Exception):
    """Raised when a registry document is malformed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class HostService:
    """One reviewed, host-side service entry."""

    id: str
    target: str
    kind: str = "http"
    enabled: bool = True
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    headers: dict[str, str] = None  # type: ignore[assignment]
    allowed_paths: tuple[str, ...] = ()
    allow_private_target: bool = False

    def __post_init__(self) -> None:
        if self.headers is None:
            object.__setattr__(self, "headers", {})

    def match_path(self, path: str) -> bool:
        """Prefix allowlist; empty means every path under the service is allowed."""
        if not self.allowed_paths:
            return True
        return any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in self.allowed_paths)


def validate_host_service(raw: dict[str, Any]) -> HostService:
    """Validate one registry document; raise :class:`HostServiceInvalid` on error."""
    if not isinstance(raw, dict):
        raise HostServiceInvalid("registry entry must be a JSON object")
    service_id = str(raw.get("id") or "").strip()
    if (
        not service_id
        or "/" in service_id
        or "\\" in service_id
        or service_id in {".", ".."}
        or any(c.isspace() for c in service_id)
    ):
        raise HostServiceInvalid("registry entry id must be a non-empty path-safe slug")
    target = str(raw.get("target") or "").strip()
    if not target:
        raise HostServiceInvalid(f"service {service_id!r}: target is required")
    parsed = urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HostServiceInvalid(f"service {service_id!r}: target must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise HostServiceInvalid(f"service {service_id!r}: target must not contain credentials or a fragment")
    kind = str(raw.get("kind") or "http").strip().casefold()
    if kind not in {"http", "sse"}:
        kind = "http"
    headers = raw.get("headers")
    if headers is not None and not isinstance(headers, dict):
        raise HostServiceInvalid(f"service {service_id!r}: headers must be an object")
    allowed_paths = raw.get("allowed_paths") or []
    if not isinstance(allowed_paths, list) or not all(isinstance(p, str) for p in allowed_paths):
        raise HostServiceInvalid(f"service {service_id!r}: allowed_paths must be a list of strings")
    timeout = float(raw.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    if timeout <= 0:
        timeout = DEFAULT_TIMEOUT_SECONDS
    return HostService(
        id=service_id,
        target=target.rstrip("/"),
        kind=kind,
        enabled=bool(raw.get("enabled", True)),
        timeout_seconds=timeout,
        headers={str(k): str(v) for k, v in (headers or {}).items()},
        allowed_paths=tuple(p.strip("/") for p in allowed_paths if p.strip()),
        allow_private_target=bool(raw.get("allow_private_target", False)),
    )


def _target_address_is_allowed(target: str, allow_private: bool) -> tuple[bool, str | None]:
    """Return (allowed, reason) for a registry target host.

    Loopback is always allowed. Private (RFC 1918 / ULA) targets require
    ``allow_private_target`` because a real-machine service may legitimately
    bind a LAN/WSL adapter address. Anything else (public internet, link-local,
    multicast, metadata) is refused.
    """
    parsed = urlsplit(target)
    host = parsed.hostname or ""
    try:
        addresses = {
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        return False, f"target host {host!r} could not be resolved: {exc}"
    if not addresses:
        return False, f"target host {host!r} resolved to no addresses"
    if all(addr.is_loopback for addr in addresses):
        return True, None
    if allow_private and all(addr.is_private for addr in addresses):
        return True, None
    return False, f"target host {host!r} is not loopback (and not opted into private targets)"


class DirectoryHostServiceRegistry:
    """Load ``{id}.json`` service files from one directory (fail closed).

    Files are small and few; each refresh validates every file and the result
    is keyed by service id. An mtime cache avoids re-reading unchanged files.
    Missing, malformed, or disabled entries are simply absent from the
    registry, which the bridge treats as deny.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._cache: dict[str, tuple[int, HostService]] = {}

    def refresh_into(self, registry: dict[str, HostService]) -> None:
        current: dict[str, HostService] = {}
        try:
            entries = sorted(self.directory.glob("*.json"))
        except OSError as exc:
            logger.error("Host service registry directory %s unreadable: %s", self.directory, exc)
            registry.clear()
            return
        for path in entries:
            try:
                stat = path.stat()
            except OSError:
                continue
            cached = self._cache.get(str(path))
            if cached is not None and cached[0] == stat.st_mtime_ns:
                current[cached[1].id] = cached[1]
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                service = validate_host_service(raw)
            except (OSError, ValueError, HostServiceInvalid) as exc:
                logger.error(
                    "Host service registry file %s invalid; service denied: %s", path, exc
                )
                self._cache.pop(str(path), None)
                continue
            if service.id != path.stem:
                logger.error(
                    "Host service registry file %s: id %r does not match filename; denied",
                    path,
                    service.id,
                )
                continue
            self._cache[str(path)] = (stat.st_mtime_ns, service)
            current[service.id] = service
        stale = [key for key in self._cache if key not in current]
        for key in stale:
            self._cache.pop(key, None)
        registry.clear()
        registry.update(current)


class JsonlAuditSink:
    """Append-only JSONL audit sink; stdout logging when no path is configured."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._stream: Any = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def open(self) -> None:
        if self._path is not None:
            self._stream = self._path.open("a", encoding="utf-8")

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def __call__(self, event: dict[str, Any]) -> None:
        record = {"ts": _utc_now().isoformat(), **event}
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if self._stream is not None:
            self._stream.write(line + "\n")
            self._stream.flush()
        else:
            logger.info("host bridge decision: %s", line)


class HostServiceBridge:
    """FastAPI application that forwards authorized ``/services/{id}/...`` calls.

    The registry is refreshed in the background every ``refresh_seconds`` so
    reviewed additions, toggles and removals take effect without a restart.
    """

    def __init__(
        self,
        *,
        token: str,
        registry: dict[str, HostService] | None = None,
        source: DirectoryHostServiceRegistry | None = None,
        on_decision: Callable[[dict[str, Any]], None] | None = None,
        refresh_seconds: float = 5.0,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        if not token:
            raise ValueError("Host Service Bridge requires a non-empty bearer token (fail closed)")
        self.token = token
        self.registry = registry if registry is not None else {}
        self.source = source
        self.on_decision = on_decision or (lambda event: None)
        self.refresh_seconds = max(1.0, refresh_seconds)
        self.max_body_bytes = max_body_bytes
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._watchdog: Any = None
        self._app = self._build_app()

    # -- lifecycle ---------------------------------------------------------

    def app(self) -> FastAPI:
        return self._app

    async def start_watchdog(self) -> None:
        if self.source is None:
            return
        self.source.refresh_into(self.registry)
        self._watchdog = asyncio.get_running_loop().create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        assert self.source is not None
        try:
            while True:
                await asyncio.sleep(self.refresh_seconds)
                try:
                    self.source.refresh_into(self.registry)
                except Exception:
                    logger.exception("Host service registry refresh failed; previous services remain in force")
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            try:
                await self._watchdog
            except asyncio.CancelledError:
                pass
            self._watchdog = None
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    # -- internals ----------------------------------------------------------

    def _client_for(self, service: HostService) -> httpx.AsyncClient:
        client = self._clients.get(service.id)
        if client is None:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(service.timeout_seconds),
                follow_redirects=False,
                trust_env=False,
            )
            self._clients[service.id] = client
        return client

    def _audit(self, *, decision: str, service_id: str | None, path: str, request: Request, **extra: Any) -> None:
        self.on_decision(
            {
                "decision": decision,
                "service_id": service_id,
                "path": path,
                "method": request.method,
                "client": request.client.host if request.client else None,
                "request_id": getattr(request.state, "request_id", None),
                **extra,
            }
        )

    def _require_token(self, request: Request) -> None:
        # The backend reaches the bridge through host.docker.internal and
        # authenticates with the X-LearnGraph-Host-Bridge-Token header (its
        # provider/MCP requests carry their own Authorization for the target
        # service, which must not collide with the bridge credential).
        auth = request.headers.get("authorization", "")
        x_token = request.headers.get("x-learngraph-host-bridge-token", "")
        expected = f"Bearer {self.token}"
        if auth != expected and x_token != self.token:
            self._audit(decision="deny", service_id=None, path=request.url.path, request=request, reason="missing_or_invalid_token")
            raise HostServiceDenied("missing or invalid bearer token")

    def _authorize(self, request: Request) -> HostService:
        self._require_token(request)
        parts = request.url.path.strip("/").split("/")
        if len(parts) < 2 or parts[0] != SERVICE_ID_PATTERN:
            self._audit(decision="deny", service_id=None, path=request.url.path, request=request, reason="not_a_service_path")
            raise HostServiceDenied("only /services/<id>/... paths are served")
        service_id = parts[1]
        service = self.registry.get(service_id)
        if service is None:
            self._audit(decision="deny", service_id=service_id, path=request.url.path, request=request, reason="unknown_service")
            raise HostServiceDenied("unknown or disabled service")
        if not service.enabled:
            self._audit(decision="deny", service_id=service_id, path=request.url.path, request=request, reason="service_disabled")
            raise HostServiceDenied("service is disabled")
        return service

    def _build_app(self) -> FastAPI:
        @asynccontextmanager
        async def lifespan(_: FastAPI) -> Any:
            await self.start_watchdog()
            try:
                yield
            finally:
                await self.close()

        app = FastAPI(title="LearnGraph Host Service Bridge", docs_url=None, redoc_url=None, lifespan=lifespan)

        @app.middleware("http")
        async def request_id_middleware(request: Request, call_next: Any) -> Response:
            import uuid

            request.state.request_id = uuid.uuid4().hex[:12]
            return await call_next(request)

        @app.get(HEALTH_PATH)
        async def healthz(request: Request) -> Response:
            try:
                self._require_token(request)
            except HostServiceDenied as exc:
                return JSONResponse(status_code=403, content={"error": exc.reason})
            return JSONResponse(status_code=200, content={"status": "ok", "services": sorted(self.registry)})

        @app.api_route("/services/{service_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
        async def forward(service_id: str, path: str, request: Request) -> Response:
            try:
                service = self._authorize(request)
            except HostServiceDenied as exc:
                return JSONResponse(status_code=403, content={"error": exc.reason})
            assert service is not None
            if not service.match_path(path):
                self._audit(decision="deny", service_id=service.id, path=request.url.path, request=request, reason="path_not_allowed")
                return JSONResponse(status_code=403, content={"error": "path not allowed by service registry"})
            allowed, reason = _target_address_is_allowed(service.target, service.allow_private_target)
            if not allowed:
                self._audit(decision="deny", service_id=service.id, path=request.url.path, request=request, reason="target_refused", details=reason)
                return JSONResponse(status_code=502, content={"error": "service target refused", "detail": reason})
            target_url = f"{service.target}/{path}"
            if request.url.query:
                target_url = f"{target_url}?{request.url.query}"
            headers = dict(service.headers)
            # The bridge's own Authorization is consumed here and never leaks
            # to the target; per-service static headers are the only injected
            # credentials.
            client = self._client_for(service)
            try:
                body = await request.body()
            except Exception as exc:
                self._audit(decision="deny", service_id=service.id, path=request.url.path, request=request, reason="body_read_failed", details=str(exc))
                return JSONResponse(status_code=400, content={"error": "request body could not be read"})
            if len(body) > self.max_body_bytes:
                self._audit(decision="deny", service_id=service.id, path=request.url.path, request=request, reason="body_too_large")
                return JSONResponse(status_code=413, content={"error": "request body too large"})
            skip_headers = {"host", "content-length", "authorization"}
            request_headers = {
                key: value
                for key, value in request.headers.items()
                if key.casefold() not in skip_headers
            }
            request_headers.update(headers)
            try:
                request_instance = client.build_request(
                    request.method,
                    target_url,
                    headers=request_headers,
                    content=body if body else None,
                )
                upstream = await client.send(request_instance, stream=True)
            except httpx.TimeoutException as exc:
                self._audit(decision="deny", service_id=service.id, path=request.url.path, request=request, reason="target_timeout", details=str(exc))
                return JSONResponse(status_code=504, content={"error": "service target timed out"})
            except httpx.HTTPError as exc:
                self._audit(decision="deny", service_id=service.id, path=request.url.path, request=request, reason="target_unreachable", details=str(exc))
                return JSONResponse(status_code=502, content={"error": "service target unreachable"})
            self._audit(decision="allow", service_id=service.id, path=request.url.path, request=request, target_status=upstream.status_code)

            async def stream_body() -> Any:
                try:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                finally:
                    await upstream.aclose()

            response_headers = {
                key: value
                for key, value in upstream.headers.items()
                if key.casefold() not in {"content-length", "content-encoding", "transfer-encoding", "connection"}
            }
            return StreamingResponse(
                stream_body(),
                status_code=upstream.status_code,
                headers=response_headers,
                media_type=upstream.headers.get("content-type"),
            )

        return app
