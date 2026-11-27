from __future__ import annotations

import httpx

from app.core.errors import AppError
from app.providers.error_policy import classify_provider_error
from app.providers.remote.openai import (
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from app.services.chat import _provider_stream_error_payload, _record_retry_attempt_trace


def test_classify_provider_error_covers_actionable_kinds() -> None:
    auth = classify_provider_error(ProviderHTTPError("Invalid API key", status_code=401))
    assert auth == {"kind": "auth", "status_code": 401, "retryable": False}

    rate = classify_provider_error(ProviderHTTPError("Rate limit exceeded", status_code=429))
    assert rate == {"kind": "rate_limit", "status_code": 429, "retryable": True}

    ctx = classify_provider_error(
        ProviderHTTPError("maximum context length exceeded: 128000 tokens", status_code=400)
    )
    assert ctx == {"kind": "context_limit", "status_code": 400, "retryable": False}

    timeout = classify_provider_error(ProviderTimeoutError("read timed out"))
    assert timeout == {"kind": "timeout", "status_code": None, "retryable": True}

    server = classify_provider_error(ProviderHTTPError("Bad Gateway", status_code=502))
    assert server == {"kind": "server", "status_code": 502, "retryable": True}

    transport = classify_provider_error(ProviderTransportError("connection reset by peer"))
    assert transport == {"kind": "connection", "status_code": None, "retryable": True}

    bad_req = classify_provider_error(ProviderResponseError("Malformed tool call JSON"))
    assert bad_req == {"kind": "bad_request", "status_code": None, "retryable": False}

    app_auth = classify_provider_error(AppError(403, "forbidden", "Workspace credential rejected"))
    assert app_auth == {"kind": "auth", "status_code": 403, "retryable": False}

    wrapped_net = ProviderHTTPError("upstream socket closed")
    wrapped_net.__cause__ = httpx.ReadError("reset")
    assert classify_provider_error(wrapped_net)["kind"] == "connection"


def test_provider_stream_error_payload_includes_kind_status_and_retryable() -> None:
    payload = _provider_stream_error_payload(
        ProviderHTTPError("HTTP 401 Unauthorized: invalid_api_key", status_code=401)
    )
    assert payload["code"] == "provider_http_error"
    assert payload["kind"] == "auth"
    assert payload["status_code"] == 401
    assert payload["retryable"] is False


def test_record_retry_attempt_trace_caps_history() -> None:
    trace: dict[str, object] = {}
    for index in range(15):
        _record_retry_attempt_trace(
            trace,
            attempt_no=index + 2,
            max_retries=3,
            error_category="upstream_http",
            backoff_ms=1000 * (index + 1),
            error_message=f"503 error #{index}",
        )
    attempts = trace["retry_attempts"]
    assert isinstance(attempts, list)
    assert len(attempts) == 12
    assert attempts[0]["attempt_no"] == 5
    assert attempts[-1]["attempt_no"] == 16
