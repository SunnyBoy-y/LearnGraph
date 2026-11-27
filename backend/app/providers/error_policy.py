"""Shared classification of provider failures (retryable or terminal).

Every place that retries a provider call needs the same answer to one question:
*is this failure worth repeating?* A rejected request — unknown model, bad
payload, revoked credential — cannot start succeeding by being sent again; the
repeat only spends another round trip and buries the actionable error under a
generic "failed after N attempts".

Kept next to the provider layer (rather than inside one service) so the chat
stream backoff loop, the structured-generation retry loops and any future
caller can share one policy instead of growing near-duplicates.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.core.errors import AppError
from app.providers.local.model import ModelProviderUnavailableError
from app.providers.remote.openai import (
    ProviderHTTPError,
    ProviderInvalidUrlError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransportError,
)

# Upstream statuses that signal a transient gateway/overload condition:
# 502/503/504 are relay (Cloudflare) failures, 500 covers flaky origins,
# 408/429 are explicit try-again signals, 529 is Anthropic "overloaded".
# Mirrors the chat stream policy in ``services/chat.py``.
RETRYABLE_PROVIDER_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})

_AUTH_ERROR_RE = re.compile(
    r"(unauthorized|forbidden|invalid[_ -]?api[_ -]?key|authentication|incorrect api key|api key not valid|invalid token|expired token|permission denied|未授权|密钥无效|认证失败)",
    re.IGNORECASE,
)
_RATE_LIMIT_RE = re.compile(
    r"(rate[_ -]?limit|too many requests|quota exceeded|insufficient_quota|overloaded|throttl|限流|配额不足|请求过于频繁)",
    re.IGNORECASE,
)
_CONTEXT_LIMIT_RE = re.compile(
    r"(context[_ -]?length|maximum context|max_tokens|token limit|prompt is too long|input is too long|request too large|too many tokens|exceeds the context|上下文过长|超出上下文)",
    re.IGNORECASE,
)
_TIMEOUT_RE = re.compile(
    r"(timed?\s*out|deadline exceeded|read timeout|connect timeout|请求超时|连接超时)",
    re.IGNORECASE,
)
_CONNECTION_RE = re.compile(
    r"(connection reset|connection refused|econnrefused|econnreset|enotfound|broken pipe|eof occurred|dns|network error|remote protocol error|socket hang up|网络异常|连接中断)",
    re.IGNORECASE,
)


def classify_provider_error(error: BaseException) -> dict[str, Any]:
    """Classify a provider/stream failure into a UI-actionable structured kind.

    Kinds align with frontend TurnErrorNotice recovery flows:
    ``auth`` | ``rate_limit`` | ``context_limit`` | ``timeout`` | ``server`` |
    ``connection`` | ``bad_request`` | ``unknown``.
    """
    status_code = getattr(error, "status_code", None)
    if not isinstance(status_code, int):
        status_code = None
    message = str(error or "")
    code = getattr(error, "code", "") if isinstance(error, AppError) else ""
    combined = f"{code} {message}".strip()

    if isinstance(error, (ProviderTimeoutError, TimeoutError, httpx.TimeoutException)) or status_code in {408, 504}:
        return {"kind": "timeout", "status_code": status_code, "retryable": True}

    if status_code in {401, 403} or _AUTH_ERROR_RE.search(combined):
        return {"kind": "auth", "status_code": status_code, "retryable": False}

    if status_code in {429, 529} or _RATE_LIMIT_RE.search(combined):
        return {"kind": "rate_limit", "status_code": status_code, "retryable": True}

    if status_code == 413 or _CONTEXT_LIMIT_RE.search(combined):
        return {"kind": "context_limit", "status_code": status_code, "retryable": False}

    if status_code is not None and status_code >= 500:
        return {"kind": "server", "status_code": status_code, "retryable": True}

    if (
        isinstance(error, ProviderTransportError)
        or isinstance(error, httpx.TransportError)
        or isinstance(getattr(error, "__cause__", None), httpx.TransportError)
        or _CONNECTION_RE.search(combined)
    ):
        return {"kind": "connection", "status_code": status_code, "retryable": True}

    if _TIMEOUT_RE.search(combined):
        return {"kind": "timeout", "status_code": status_code, "retryable": True}

    if isinstance(error, ModelProviderUnavailableError):
        return {
            "kind": "auth" if _AUTH_ERROR_RE.search(combined) else "bad_request",
            "status_code": status_code,
            "retryable": False,
        }

    if isinstance(error, (ProviderResponseError, ProviderInvalidUrlError)) or (
        status_code is not None and 400 <= status_code < 500
    ):
        return {"kind": "bad_request", "status_code": status_code, "retryable": False}

    if isinstance(error, AppError):
        if error.status_code in {401, 403}:
            return {"kind": "auth", "status_code": error.status_code, "retryable": False}
        if error.status_code == 429:
            return {"kind": "rate_limit", "status_code": 429, "retryable": True}
        if error.status_code >= 500:
            return {"kind": "server", "status_code": error.status_code, "retryable": True}
        return {"kind": "bad_request", "status_code": error.status_code, "retryable": False}

    return {
        "kind": "unknown",
        "status_code": status_code,
        "retryable": provider_failure_is_transient(error),
    }


def provider_stream_retry_category(error: BaseException) -> str | None:
    """Retry connection failures, while keeping invalid requests terminal.

    Older adapters wrap httpx transport exceptions in ProviderHTTPError. Keep
    their cause useful without treating status-less vendor error events as
    transient: those can contain authentication or request validation errors.
    """
    if isinstance(error, (ProviderTimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(error, (ProviderResponseError, ProviderInvalidUrlError)):
        return None
    if not isinstance(error, ProviderHTTPError):
        return None
    if error.status_code is not None:
        return (
            "upstream_http"
            if error.status_code in RETRYABLE_PROVIDER_HTTP_STATUSES
            else None
        )
    if isinstance(error, ProviderTransportError) or isinstance(
        error.__cause__, httpx.TransportError
    ):
        return "upstream_transport"
    return None


def provider_failure_is_transient(error: BaseException | None) -> bool:
    """Whether repeating the identical provider request could plausibly work."""

    if error is None:
        return True
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status in RETRYABLE_PROVIDER_HTTP_STATUSES
    # No HTTP status at all (connection reset, timeout, truncated SSE): these
    # describe transport trouble rather than a rejected request, so they stay
    # retryable. A provider we resolved as unusable, however, is a
    # configuration fact that a retry cannot change.
    return not isinstance(error, ModelProviderUnavailableError)
