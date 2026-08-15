"""Service authentication, request-id correlation and safe access logging."""

from __future__ import annotations

import hmac
import logging
import uuid

from fastapi import Header, HTTPException, Request, status

logger = logging.getLogger("sandboxd.access")

# Headers that must never be logged or echoed.
_SENSITIVE_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})
_AUTH_HEADER = "authorization"


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: "[REDACTED]" if k in _SENSITIVE_HEADERS else v for k, v in headers.items()}


def verify_token(token: str, provided: str | None) -> bool:
    """Constant-time comparison of the bearer token."""
    if not provided:
        return False
    try:
        return hmac.compare_digest(token.encode("utf-8"), provided.encode("utf-8"))
    except (TypeError, ValueError):
        return False


class ServiceAuth:
    """FastAPI dependency factory for service-token protected endpoints."""

    def __init__(self, token: str) -> None:
        self._token = token

    def __call__(self, authorization: str | None = Header(default=None, alias="Authorization")) -> None:
        if not authorization:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
        scheme, _, value = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not verify_token(self._token, value.strip()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")


class AdminAuth:
    """FastAPI dependency factory for the bootstrap/admin control plane.

    Bootstrap/build is a higher-privilege management surface; it stays
    disabled (fail closed) until an admin token file is configured.
    """

    def __init__(self, admin_token: str | None) -> None:
        self._admin_token = admin_token

    def __call__(self, authorization: str | None = Header(default=None, alias="Authorization")) -> None:
        if not self._admin_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="admin control plane is disabled",
            )
        if not authorization:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
        scheme, _, value = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not verify_token(self._admin_token, value.strip()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")


def request_id_header(request: Request) -> str:
    """Return the correlated request id (from header or generated)."""
    return getattr(request.state, "request_id", "")


async def request_id_middleware(request: Request, call_next):
    """Attach/echo X-Request-Id and log a redacted access line."""
    request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    try:
        logger.info(
            "request path=%s method=%s status=%s request_id=%s",
            request.url.path,
            request.method,
            response.status_code,
            request_id,
        )
    except Exception:  # noqa: BLE001 - logging must never break the request
        pass
    return response
