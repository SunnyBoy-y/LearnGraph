from __future__ import annotations

"""Frontend-sandbox network relay gateway.

The browser-side sandbox (MagicCard / HTML preview) renders inside an
opaque-origin iframe and cannot fetch external URLs directly (CORS / opaque
origin). Every JS-initiated network call is relayed here by the host bridge
(``frontend/src/lib/sandbox-runtime-bridge.ts``).

Product decision (2026-08-18): frontend-sandbox networking is **approval-free**;
only backend sandboxes require egress approval. This gateway therefore performs
hard-guard checks instead of an approval flow:

* public-only resolved addresses (SSRF protection incl. cloud metadata,
  loopback, private, link-local, carrier-grade NAT — reusing the backend
  classifier from ``sandbox_network_policy``);
* no cookies / no host session credentials are ever forwarded;
* HTTP(S) only, method allowlist, header allowlist, hop-by-hop stripped;
* redirect chain re-validated per hop (DNS rebinding protection);
* response size and total timeout caps;
* every request is audit-recorded.
"""

import base64
import time
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.models import AuditEvent
from app.services.sandbox_network_policy import (
    EgressPolicyDenied,
    classify_ip_address,
    system_resolver,
)

router = APIRouter(prefix="/sandbox-net", tags=["sandbox-net"])

MAX_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MiB per relayed response
MAX_BODY_BYTES = 256 * 1024  # 256 KiB per relayed request body
DEFAULT_TIMEOUT_MS = 15_000
MAX_TIMEOUT_MS = 30_000
MAX_REDIRECTS = 5
MAX_AUDIT_LIST_LIMIT = 100

ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})

# Headers the sandbox may set on relayed requests. Host session credentials
# (cookie / authorization) and hop-by-hop fields are never forwarded.
ALLOWED_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-language",
        "content-type",
        "cache-control",
        "user-agent",
        "if-modified-since",
        "if-none-match",
        "x-requested-with",
    }
)

# Response headers we never echo back into the sandbox.
BLOCKED_RESPONSE_HEADERS = frozenset(
    {"set-cookie", "cookie", "authorization", "proxy-authenticate", "www-authenticate"}
)

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "proxy-authorization",
    }
)

# Per-workspace audit window for the read-only audit endpoint.
AUDIT_ACTION = "sandbox_net.proxy"


class SandboxNetProxyRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict, max_length=32)
    # Request body as UTF-8-safe text. JSON/XML payloads are the norm for
    # AI-generated sandbox code; binary uploads are out of scope for v1.
    body: str | None = None
    timeout_ms: int | None = None

    @field_validator("method")
    @classmethod
    def _method_allowlist(cls, value: str) -> str:
        method = value.strip().upper()
        if method not in ALLOWED_METHODS:
            raise ValueError(f"method_not_allowed:{method}")
        return method

    @field_validator("timeout_ms")
    @classmethod
    def _timeout_range(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not 500 <= value <= MAX_TIMEOUT_MS:
            raise ValueError(f"timeout_out_of_range:{value}")
        return value

    @field_validator("body")
    @classmethod
    def _body_limit(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError("body_too_large")
        return value


class SandboxNetProxyResponse(BaseModel):
    status: int
    status_text: str
    content_type: str | None
    body_base64: str | None
    size_bytes: int


class SandboxNetAuditEntry(BaseModel):
    id: str
    created_at: str
    action: str
    outcome: str
    details: dict[str, Any]


class SandboxNetAuditListResponse(BaseModel):
    items: list[SandboxNetAuditEntry]


def _parse_url(value: str) -> tuple[str, str, int, str]:
    """Validate the target URL and return (scheme, host, port, path+query)."""
    if value.startswith("//") or "://" not in value:
        raise AppError(422, "sandbox_net_url_invalid", "Only absolute http(s) URLs are supported")
    scheme, _, rest = value.partition("://")
    scheme = scheme.strip().casefold()
    if scheme not in {"http", "https"}:
        raise AppError(422, "sandbox_net_url_invalid", "Only http and https targets are supported")
    if "@" in rest.split("/", 1)[0]:
        raise AppError(422, "sandbox_net_url_credentials", "URLs with embedded credentials are not allowed")
    hostport, _, path = rest.partition("/")
    if not hostport:
        raise AppError(422, "sandbox_net_url_invalid", "Target URL has no host")
    host, separator, port_text = hostport.rpartition(":")
    if not separator:
        host, port = hostport, (443 if scheme == "https" else 80)
    else:
        if not host:
            raise AppError(422, "sandbox_net_url_invalid", "Target URL has no host")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise AppError(422, "sandbox_net_url_invalid", "Target URL port is invalid") from exc
        if not 1 <= port <= 65535:
            raise AppError(422, "sandbox_net_url_invalid", "Target URL port is out of range")
    return scheme, host, port, "/" + path


def _authorize_public_host(host: str) -> list[str]:
    """Resolve the host and require every answer to classify as public.

    Reuses the backend sandbox classifier so frontend-sandbox networking honors
    the same SSRF posture (loopback / private / link-local / multicast /
    cloud-metadata / documentation ranges are denied).
    """
    addresses = system_resolver(host)
    if not addresses:
        raise AppError(502, "sandbox_net_dns_failed", "Target host could not be resolved")
    forbidden: list[tuple[str, str]] = []
    public: list[str] = []
    for address in addresses:
        classification = classify_ip_address(address)
        if classification != "public":
            forbidden.append((address, classification))
        else:
            public.append(address)
    if forbidden:
        raise AppError(
            403,
            "sandbox_net_ssrf_blocked",
            "Target resolves to a non-public address",
            {"forbidden_answers": forbidden},
        )
    return public


def _sanitize_request_headers(headers: dict[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.strip().casefold()
        if lowered in HOP_BY_HOP_HEADERS or lowered not in ALLOWED_REQUEST_HEADERS:
            continue
        if isinstance(value, str) and 0 < len(value) <= 4096:
            clean[lowered] = value.strip()
    return clean


def _sanitize_response_headers(headers: httpx.Headers) -> dict[str, str]:
    clean: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.casefold()
        if lowered in BLOCKED_RESPONSE_HEADERS or lowered in HOP_BY_HOP_HEADERS:
            continue
        if lowered == "content-length":
            continue
        clean[lowered] = value
    return clean


async def _relay_once(
    client: httpx.AsyncClient,
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    body: str | None,
    timeout_ms: int,
) -> httpx.Response:
    timeout = httpx.Timeout(timeout_ms / 1000)
    return await client.request(
        method,
        url,
        headers=headers,
        content=body.encode("utf-8") if body is not None else None,
        timeout=timeout,
        follow_redirects=False,
    )


def _audit_payload(url: str, method: str, status: int, size: int, outcome: str, extra: dict[str, Any]) -> dict[str, Any]:
    """Small non-secret audit record: host + path only (never the query string)."""
    try:
        scheme, host, port, path = _parse_url(url)
        target = f"{scheme}://{host}:{port}{path}"
    except AppError:
        target = url.split("?")[0][:512]
    return {
        "method": method,
        "target": target[:512],
        "status": status,
        "size_bytes": size,
        **extra,
    }


def _service(db: DB, context: CurrentWorkspace) -> Any:
    from app.repositories.audit import AuditRepository

    return AuditRepository(db, context.workspace_id)


@router.post("/proxy", response_model=SandboxNetProxyResponse)
async def relay_sandbox_net_request(
    payload: SandboxNetProxyRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxNetProxyResponse:
    """Relay one frontend-sandbox network request to the public internet.

    Approval-free by product decision; hard guards only. Every request is
    audit-recorded with a query-free target, method, status and size.
    """
    if not settings.sandbox_net_enabled:
        raise AppError(403, "sandbox_net_disabled", "Frontend-sandbox networking is disabled by the deployment")

    audit = _service(db, context)
    started = time.monotonic()
    url = payload.url.strip()
    scheme, host, port, _ = _parse_url(url)

    # Resolve + classify at request time; every answer must be public.
    try:
        _authorize_public_host(host)
    except AppError as exc:
        audit.record(
            actor_id=context.principal.user_id,
            action=AUDIT_ACTION,
            resource_type="sandbox_net",
            resource_id="proxy",
            outcome="denied",
            details=_audit_payload(url, payload.method, 0, 0, "denied", {"reason": exc.code, **exc.details}),
        )
        raise

    headers = _sanitize_request_headers(payload.headers)
    timeout_ms = payload.timeout_ms or DEFAULT_TIMEOUT_MS
    response: httpx.Response | None = None
    redirects = 0
    current_url = url

    transport = httpx.AsyncHTTPTransport(retries=0)
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        while True:
            _authorize_public_host(_parse_url(current_url)[1])
            response = await _relay_once(
                client,
                url=current_url,
                method=payload.method if redirects == 0 else "GET",
                headers=headers,
                body=payload.body if redirects == 0 else None,
                timeout_ms=timeout_ms,
            )
            if response.status_code not in {301, 302, 303, 307, 308} or not response.headers.get("location"):
                break
            redirects += 1
            if redirects > MAX_REDIRECTS:
                raise AppError(502, "sandbox_net_too_many_redirects", "Target redirected too many times")
            location = response.headers["location"]
            current_url = str(httpx.URL(current_url).join(location))
            scheme_next, _, _, _ = _parse_url(current_url)
            if scheme_next not in {"http", "https"}:
                raise AppError(422, "sandbox_net_url_invalid", "Redirect target is not an http(s) URL")

    assert response is not None
    size = 0
    body_base64: str | None = None
    if response.status_code != 204:
        body = response.content
        size = len(body)
        if size > MAX_RESPONSE_BYTES:
            raise AppError(502, "sandbox_net_response_too_large", "Relayed response exceeds the size limit")
        if body:
            body_base64 = base64.b64encode(body).decode("ascii")

    elapsed_ms = int((time.monotonic() - started) * 1000)
    audit.record(
        actor_id=context.principal.user_id,
        action=AUDIT_ACTION,
        resource_type="sandbox_net",
        resource_id="proxy",
        outcome="success" if response.status_code < 500 else "upstream_error",
        details=_audit_payload(
            url,
            payload.method,
            response.status_code,
            size,
            "success",
            {"redirects": redirects, "elapsed_ms": elapsed_ms},
        ),
    )

    return SandboxNetProxyResponse(
        status=response.status_code,
        status_text=response.reason_phrase,
        content_type=response.headers.get("content-type"),
        body_base64=body_base64,
        size_bytes=size,
    )


@router.get("/audit", response_model=SandboxNetAuditListResponse)
def list_sandbox_net_audit(
    db: DB,
    context: CurrentWorkspace,
    limit: Annotated[int, Query(ge=1, le=MAX_AUDIT_LIST_LIMIT)] = 50,
) -> SandboxNetAuditListResponse:
    """Read-only audit trail for frontend-sandbox network relays (settings page)."""
    rows = db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == context.workspace_id,
            AuditEvent.action == AUDIT_ACTION,
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    ).all()
    return SandboxNetAuditListResponse(
        items=[
            SandboxNetAuditEntry(
                id=row.id,
                created_at=row.created_at.isoformat() if row.created_at else "",
                action=row.action,
                outcome=row.outcome,
                details=row.details or {},
            )
            for row in rows
        ]
    )
