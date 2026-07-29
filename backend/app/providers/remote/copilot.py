from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.providers.remote.openai import (
    OpenAICompatibleChatProvider,
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTimeoutError,
)

GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_BASE_URL = "https://api.githubcopilot.com"
GITHUB_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"

COPILOT_HEADERS = {
    "copilot-integration-id": "vscode-chat",
    "editor-version": "vscode/1.96.2",
    "editor-plugin-version": "copilot-chat/0.26.7",
    "openai-intent": "conversation-panel",
    "user-agent": "GitHubCopilotChat/0.26.7",
    "x-github-api-version": "2022-11-28",
}
_CREDENTIAL_HEADERS = {
    "authorization",
    "x-api-key",
    "api-key",
    "proxy-authorization",
}
_DEVICE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,200}$")
_CLAUDE_DASH_VERSION_RE = re.compile(
    r"^(claude-(?:haiku|sonnet|opus)-\d+)-(\d+)(?:\[1m\])?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CopilotDeviceLogin:
    device_auth_id: str
    user_code: str
    verification_url: str
    interval_seconds: int


@dataclass(frozen=True, slots=True)
class CopilotCredentials:
    github_token: str


@dataclass(frozen=True, slots=True)
class CopilotToken:
    value: str
    expires_at: float


def _json_object(response: httpx.Response, *, operation: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderResponseError(f"{operation} returned non-JSON data") from exc
    if not isinstance(payload, dict):
        raise ProviderResponseError(f"{operation} returned an invalid JSON object")
    return payload


def _request_error(operation: str, exc: httpx.HTTPError) -> ProviderTimeoutError | ProviderResponseError:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError(f"{operation} timed out")
    return ProviderResponseError(f"{operation} request failed")


def start_copilot_device_login(
    *,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 15.0,
) -> CopilotDeviceLogin:
    try:
        with httpx.Client(transport=transport, timeout=timeout_seconds) as client:
            response = client.post(
                GITHUB_DEVICE_CODE_URL,
                data={"client_id": GITHUB_COPILOT_CLIENT_ID, "scope": "read:user"},
                headers={"Accept": "application/json", "User-Agent": COPILOT_HEADERS["user-agent"]},
            )
    except httpx.HTTPError as exc:
        raise _request_error("GitHub device login", exc) from exc
    if not response.is_success:
        raise ProviderHTTPError(
            f"GitHub device login returned HTTP {response.status_code}",
            status_code=response.status_code,
        )
    payload = _json_object(response, operation="GitHub device login")
    device_code = str(payload.get("device_code") or "").strip()
    user_code = str(payload.get("user_code") or "").strip()
    if not _DEVICE_CODE_RE.fullmatch(device_code) or not user_code:
        raise ProviderResponseError("GitHub device login response is missing device codes")
    try:
        interval = int(payload.get("interval") or 5)
    except (TypeError, ValueError):
        interval = 5
    return CopilotDeviceLogin(
        device_auth_id=device_code,
        user_code=user_code,
        verification_url=str(
            payload.get("verification_uri") or "https://github.com/login/device"
        ),
        interval_seconds=max(1, min(interval, 30)),
    )


def poll_copilot_device_login(
    *,
    device_auth_id: str,
    user_code: str,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 15.0,
) -> CopilotCredentials | None:
    device_code = device_auth_id.strip()
    if not _DEVICE_CODE_RE.fullmatch(device_code) or not user_code.strip():
        raise ProviderHTTPError("Unknown or expired GitHub device login", status_code=400)
    try:
        with httpx.Client(transport=transport, timeout=timeout_seconds) as client:
            response = client.post(
                GITHUB_OAUTH_TOKEN_URL,
                data={
                    "client_id": GITHUB_COPILOT_CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json", "User-Agent": COPILOT_HEADERS["user-agent"]},
            )
    except httpx.HTTPError as exc:
        raise _request_error("GitHub device login polling", exc) from exc
    if not response.is_success:
        raise ProviderHTTPError(
            f"GitHub device login polling returned HTTP {response.status_code}",
            status_code=response.status_code,
        )
    payload = _json_object(response, operation="GitHub device login polling")
    error = str(payload.get("error") or "").strip()
    if error in {"authorization_pending", "slow_down"}:
        return None
    if error:
        raise ProviderHTTPError(
            str(payload.get("error_description") or "GitHub authorization failed"),
            status_code=400,
        )
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise ProviderResponseError("GitHub authorization response has no access token")
    return CopilotCredentials(github_token=token)


def _copilot_headers(
    token: str,
    extra_headers: dict[str, str] | None = None,
    *,
    accept: str = "application/json",
) -> dict[str, str]:
    protected = {*_CREDENTIAL_HEADERS, *(key.casefold() for key in COPILOT_HEADERS)}
    headers = {
        str(key).strip(): str(value).strip()
        for key, value in (extra_headers or {}).items()
        if str(key).strip()
        and str(value).strip()
        and str(key).strip().casefold() not in protected
    }
    headers.update(COPILOT_HEADERS)
    headers["Accept"] = accept
    headers["Authorization"] = f"Bearer {token}"
    return headers


def exchange_github_token_for_copilot_token(
    github_token: str,
    *,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 15.0,
) -> CopilotToken:
    try:
        with httpx.Client(transport=transport, timeout=timeout_seconds) as client:
            response = client.get(
                GITHUB_COPILOT_TOKEN_URL,
                headers={
                    "Authorization": f"token {github_token}",
                    "Accept": "application/json",
                    "editor-version": COPILOT_HEADERS["editor-version"],
                    "editor-plugin-version": COPILOT_HEADERS["editor-plugin-version"],
                    "user-agent": COPILOT_HEADERS["user-agent"],
                    "x-github-api-version": COPILOT_HEADERS["x-github-api-version"],
                },
            )
    except httpx.HTTPError as exc:
        raise _request_error("Copilot token exchange", exc) from exc
    if not response.is_success:
        raise ProviderHTTPError(
            f"Copilot token exchange returned HTTP {response.status_code}",
            status_code=response.status_code,
        )
    payload = _json_object(response, operation="Copilot token exchange")
    token = str(payload.get("token") or "").strip()
    if not token:
        raise ProviderResponseError("Copilot token exchange response has no token")
    try:
        expires_at = float(payload.get("expires_at"))
    except (TypeError, ValueError):
        expires_at = time.time() + 1_500
    return CopilotToken(value=token, expires_at=expires_at)


def discover_copilot_models(
    *,
    base_url: str = COPILOT_BASE_URL,
    github_token: str,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 15.0,
    extra_headers: dict[str, str] | None = None,
) -> list[str]:
    token = exchange_github_token_for_copilot_token(
        github_token,
        transport=transport,
        timeout_seconds=timeout_seconds,
    )
    try:
        with httpx.Client(
            transport=transport,
            timeout=timeout_seconds,
            headers=_copilot_headers(token.value, extra_headers),
        ) as client:
            response = client.get(f"{base_url.rstrip('/')}/models")
    except httpx.HTTPError as exc:
        raise _request_error("Copilot model discovery", exc) from exc
    if not response.is_success:
        raise ProviderHTTPError(
            f"Copilot model discovery returned HTTP {response.status_code}",
            status_code=response.status_code,
        )
    payload = _json_object(response, operation="Copilot model discovery")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ProviderResponseError("Copilot model discovery response has no data array")
    model_ids = [
        str(item.get("id") or "").strip()
        for item in data
        if isinstance(item, dict) and item.get("model_picker_enabled") is True
    ]
    return sorted({model_id for model_id in model_ids if model_id})


def normalize_copilot_model_id(model_id: str) -> str:
    candidate = model_id.strip()
    match = _CLAUDE_DASH_VERSION_RE.fullmatch(candidate)
    if not match:
        return candidate
    return f"{match.group(1)}.{match.group(2)}"


class GitHubCopilotChatProvider(OpenAICompatibleChatProvider):
    """OpenAI Chat adapter that refreshes Copilot's short-lived bearer token."""

    def __init__(self, *, api_key: str, **kwargs: Any) -> None:
        kwargs["model_id"] = normalize_copilot_model_id(str(kwargs["model_id"]))
        super().__init__(api_key=api_key, **kwargs)
        self.github_token = api_key
        self._copilot_token = CopilotToken(value="", expires_at=0.0)

    def _valid_copilot_token(self) -> str:
        if self._copilot_token.value and time.time() < self._copilot_token.expires_at - 60:
            return self._copilot_token.value
        self._copilot_token = exchange_github_token_for_copilot_token(
            self.github_token,
            transport=self.transport,
            timeout_seconds=min(self.timeout_seconds, 15.0),
        )
        return self._copilot_token.value

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers=_copilot_headers(
                self._valid_copilot_token(),
                self.extra_headers,
                accept="text/event-stream",
            ),
            timeout=self.timeout_seconds,
            transport=self.transport,
        )
