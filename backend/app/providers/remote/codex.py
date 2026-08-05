"""Codex "Sign in with ChatGPT" direct-login support.

LearnGraph normally speaks to documented vendor APIs with a workspace API key.
Codex direct login is different: the credential is a rotating OAuth token set
issued to the Codex CLI client, and inference is billed against the user's
ChatGPT plan through ``chatgpt.com/backend-api/codex`` rather than API credits.

These endpoints are undocumented and have changed shape between Codex
releases, so every field read here is treated as optional-with-fallback and no
response is trusted beyond the values we explicitly validate.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

import httpx

CODEX_ISSUER = "https://auth.openai.com"
CODEX_TOKEN_URL = f"{CODEX_ISSUER}/oauth/token"
CODEX_DEVICE_CODE_URL = f"{CODEX_ISSUER}/api/accounts/deviceauth/usercode"
CODEX_DEVICE_TOKEN_URL = f"{CODEX_ISSUER}/api/accounts/deviceauth/token"
CODEX_DEVICE_VERIFICATION_URL = f"{CODEX_ISSUER}/codex/device"
CODEX_DEVICE_REDIRECT_URI = f"{CODEX_ISSUER}/deviceauth/callback"
# The Codex CLI's public OAuth client. Tokens minted for it are only accepted
# by the ChatGPT Codex backend, never by api.openai.com.
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_ORIGINATOR = "codex_cli_rs"
CODEX_USER_AGENT = f"{CODEX_ORIGINATOR}/0.104.0 (LearnGraph; codex direct login)"
_AUTH_CLAIM = "https://api.openai.com/auth"
# Codex refreshes when the access token is within five minutes of expiry.
_REFRESH_WINDOW_SECONDS = 300

# The ChatGPT Codex backend does not expose a public GET /models endpoint, so
# discovery falls back to this reviewed ChatGPT-account catalog. Several API /
# docs slugs (including the Power default ``gpt-5.6-sol`` and older 5.2 ids)
# are rejected by the ChatGPT-auth path with HTTP 400. Users can still type an
# unlisted model id manually if their plan later unlocks it.
#
# Verified against the free ChatGPT Codex backend with stream=true (2026-07-29):
# terra / luna / gpt-5.5 / gpt-5.4-mini succeed; sol / pro / nano / 5.2–5.3 fail.
CODEX_KNOWN_MODELS: tuple[str, ...] = (
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4-mini",
    # Plan-gated / Power flagship: listed for paid accounts that can select it,
    # but not used as the default because free ChatGPT accounts reject it.
    "gpt-5.6-sol",
    # ChatGPT-auth slugs reviewed for the catalog (2026-08-05).
    "codex-auto-review",
    "gpt-5.3-codex-spark",
    "gpt-5.4",
    "gpt-5.6",
    "gpt-image-2",
)
CODEX_DEFAULT_MODEL = "gpt-5.6-terra"
# Models that OpenAI still documents for Codex but the ChatGPT-auth backend
# currently rejects. Kept for migration of older provider rows / UI cleanup.
CODEX_UNSUPPORTED_CHATGPT_MODELS: frozenset[str] = frozenset(
    {
        "gpt-5.5-pro",
        "gpt-5.4-nano",
        "gpt-5.4-pro",
        "gpt-5.3-codex",
        "gpt-5.3-codex-xhigh",
        "gpt-5.3-chat-latest",
        "gpt-5.2",
        "gpt-5.2-codex",
        "gpt-5.2-pro",
        "gpt-5.2-chat-latest",
        "codex-mini-latest",
    }
)
# Free ChatGPT accounts accept only a subset of the documented Codex catalog.
# When the selected slug is known-unsupported on free, remap to a working
# default instead of surfacing a hard 400 from the backend.
CODEX_FREE_SUPPORTED_MODELS: frozenset[str] = frozenset(
    {
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4-mini",
    }
)


def resolve_codex_model_for_plan(model_id: str, plan_type: str | None) -> str:
    """Return a ChatGPT-auth model slug that the caller's plan can use.

    Paid plans keep the requested id (including ``gpt-5.6-sol``). Free plans
    fall back to :data:`CODEX_DEFAULT_MODEL` when the selection is outside the
    free-supported subset.
    """

    requested = (model_id or "").strip() or CODEX_DEFAULT_MODEL
    plan = (plan_type or "").strip().casefold()
    if plan and plan != "free":
        return requested
    if requested in CODEX_FREE_SUPPORTED_MODELS:
        return requested
    return CODEX_DEFAULT_MODEL


class CodexAuthError(RuntimeError):
    """A safe, credential-free Codex authentication failure."""


class CodexAuthExpired(CodexAuthError):
    """The stored token set is terminally invalid; the user must sign in again."""


@dataclass(frozen=True, slots=True)
class CodexCredentials:
    access_token: str
    refresh_token: str
    id_token: str | None = None
    account_id: str | None = None
    plan_type: str | None = None

    def to_secret(self) -> str:
        """Serialize in Codex's own ``auth.json`` shape for round-tripping."""

        return json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "id_token": self.id_token,
                    "account_id": self.account_id,
                },
                "last_refresh": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )


def decode_jwt_claims(token: str | None) -> dict[str, Any]:
    """Decode a JWT payload without verifying its signature.

    Codex itself never verifies these tokens locally — the claims are only used
    to address the right ChatGPT account and to know when to refresh.  The
    token's authority still rests entirely with the upstream server.
    """

    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        claims = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _auth_claims(token: str | None) -> dict[str, Any]:
    namespace = decode_jwt_claims(token).get(_AUTH_CLAIM)
    return namespace if isinstance(namespace, dict) else {}


def codex_account_id(*tokens: str | None) -> str | None:
    for token in tokens:
        value = _auth_claims(token).get("chatgpt_account_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def codex_plan_type(*tokens: str | None) -> str | None:
    for token in tokens:
        value = _auth_claims(token).get("chatgpt_plan_type")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def access_token_expires_at(access_token: str | None) -> int | None:
    value = decode_jwt_claims(access_token).get("exp")
    return int(value) if isinstance(value, (int, float)) else None


def parse_codex_credentials(secret: str) -> CodexCredentials:
    """Accept a full ``auth.json``, its ``tokens`` object, or a refresh token."""

    text = (secret or "").strip()
    if not text:
        raise CodexAuthError("Codex 凭据为空")
    if not text.startswith("{"):
        # A bare refresh token still bootstraps: the first refresh mints the
        # access token and reveals the account id.
        return CodexCredentials(access_token="", refresh_token=text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodexAuthError("Codex 凭据不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise CodexAuthError("Codex 凭据格式不正确")
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else payload
    access_token = str(tokens.get("access_token") or "").strip()
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    id_token = str(tokens.get("id_token") or "").strip() or None
    account_id = str(tokens.get("account_id") or "").strip() or None
    if not refresh_token and not access_token:
        raise CodexAuthError("Codex 凭据缺少 access_token / refresh_token")
    return CodexCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
        account_id=account_id or codex_account_id(id_token, access_token),
        plan_type=codex_plan_type(id_token, access_token),
    )


def _client(
    *,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None,
) -> httpx.Client:
    # chatgpt.com sits behind Cloudflare and sets clearance cookies; a shared
    # jar per call keeps redirect/challenge handling working.
    return httpx.Client(
        headers={
            "originator": CODEX_ORIGINATOR,
            "User-Agent": CODEX_USER_AGENT,
        },
        timeout=timeout_seconds,
        transport=transport,
        follow_redirects=False,
    )


def refresh_codex_credentials(
    credentials: CodexCredentials,
    *,
    timeout_seconds: float = 20.0,
    transport: httpx.BaseTransport | None = None,
) -> CodexCredentials:
    """Exchange the rotating refresh token for a new Codex token set."""

    if not credentials.refresh_token:
        raise CodexAuthExpired("Codex 凭据没有 refresh_token，需要重新登录")
    try:
        with _client(timeout_seconds=timeout_seconds, transport=transport) as client:
            response = client.post(
                CODEX_TOKEN_URL,
                json={
                    "client_id": CODEX_CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": credentials.refresh_token,
                },
            )
    except httpx.TimeoutException as exc:
        raise CodexAuthError("Codex 令牌刷新超时") from exc
    except httpx.HTTPError as exc:
        raise CodexAuthError("Codex 令牌刷新请求发送失败") from exc

    if not response.is_success:
        code = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                code = str(body.get("error") or body.get("code") or "")
        except (json.JSONDecodeError, ValueError):
            code = ""
        if response.status_code == 401 or code in {
            "refresh_token_expired",
            "refresh_token_reused",
            "refresh_token_invalidated",
        }:
            raise CodexAuthExpired("Codex 登录已失效，请重新直登授权")
        raise CodexAuthError(f"Codex 令牌刷新失败（HTTP {response.status_code}）")

    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise CodexAuthError("Codex 令牌刷新响应不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise CodexAuthError("Codex 令牌刷新响应格式不正确")
    access_token = str(payload.get("access_token") or "").strip()
    id_token = str(payload.get("id_token") or "").strip() or credentials.id_token
    # Refresh tokens rotate; keeping the old one after a successful refresh
    # would invalidate the session on the next call.
    refresh_token = (
        str(payload.get("refresh_token") or "").strip() or credentials.refresh_token
    )
    if not access_token:
        raise CodexAuthError("Codex 令牌刷新响应缺少 access_token")
    return CodexCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
        account_id=credentials.account_id or codex_account_id(id_token, access_token),
        plan_type=codex_plan_type(id_token, access_token) or credentials.plan_type,
    )


def ensure_fresh_codex_credentials(
    credentials: CodexCredentials,
    *,
    now: float | None = None,
    timeout_seconds: float = 20.0,
    transport: httpx.BaseTransport | None = None,
) -> tuple[CodexCredentials, bool]:
    """Return usable credentials plus whether they must be persisted again."""

    current = time.time() if now is None else now
    expires_at = access_token_expires_at(credentials.access_token)
    needs_refresh = (
        not credentials.access_token
        or expires_at is None
        or expires_at <= current + _REFRESH_WINDOW_SECONDS
    )
    if not needs_refresh:
        if credentials.account_id:
            return credentials, False
        resolved = codex_account_id(credentials.id_token, credentials.access_token)
        if not resolved:
            raise CodexAuthError("Codex 凭据缺少 chatgpt_account_id")
        return replace(credentials, account_id=resolved), True
    refreshed = refresh_codex_credentials(
        credentials,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )
    if not refreshed.account_id:
        raise CodexAuthError("Codex 凭据缺少 chatgpt_account_id")
    return refreshed, True


def codex_request_headers(
    credentials: CodexCredentials,
    *,
    accept: str = "text/event-stream",
    session_id: str | None = None,
) -> dict[str, str]:
    """Build the header set the Codex CLI sends to the ChatGPT backend."""

    if not credentials.access_token or not credentials.account_id:
        raise CodexAuthError("Codex 凭据不完整，无法构造请求头")
    session = session_id or str(uuid.uuid4())
    return {
        "Accept": accept,
        "Authorization": f"Bearer {credentials.access_token}",
        "ChatGPT-Account-ID": credentials.account_id,
        "originator": CODEX_ORIGINATOR,
        "User-Agent": CODEX_USER_AGENT,
        "session-id": session,
        "thread-id": session,
        "x-client-request-id": session,
    }


@dataclass(frozen=True, slots=True)
class CodexUsageWindow:
    label: str
    used_percent: float
    window_minutes: int | None
    resets_at: datetime | None


def _window_label(window_minutes: int | None, fallback: str) -> str:
    if window_minutes is None:
        return fallback
    # Codex labels a window by matching its length within ±5%.
    for minutes, label in (
        (300, "5 小时额度"),
        (1440, "每日额度"),
        (10080, "每周额度"),
        (43200, "每月额度"),
        (525600, "年度额度"),
    ):
        if abs(window_minutes - minutes) <= minutes * 0.05:
            return label
    return fallback


def _parse_usage_window(raw: object, *, fallback: str) -> CodexUsageWindow | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_percent")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    seconds = raw.get("limit_window_seconds")
    window_minutes = (
        -(-int(seconds) // 60)
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0
        else None
    )
    reset_at = raw.get("reset_at")
    resets_at = (
        datetime.fromtimestamp(int(reset_at), tz=timezone.utc)
        if isinstance(reset_at, (int, float))
        and not isinstance(reset_at, bool)
        and reset_at > 0
        else None
    )
    return CodexUsageWindow(
        label=_window_label(window_minutes, fallback),
        used_percent=float(used),
        window_minutes=window_minutes,
        resets_at=resets_at,
    )


@dataclass(frozen=True, slots=True)
class CodexUsage:
    plan_type: str | None
    windows: list[CodexUsageWindow]
    credits_balance: str | None
    credits_unlimited: bool
    has_credits: bool | None
    limit_reached: bool


def fetch_codex_usage(
    credentials: CodexCredentials,
    *,
    timeout_seconds: float = 20.0,
    transport: httpx.BaseTransport | None = None,
) -> CodexUsage:
    """Read the 5h/weekly rolling limits Codex shows in ``/status``."""

    headers = codex_request_headers(credentials, accept="application/json")
    try:
        with _client(timeout_seconds=timeout_seconds, transport=transport) as client:
            response = client.get(CODEX_USAGE_URL, headers=headers)
    except httpx.TimeoutException as exc:
        raise CodexAuthError("Codex 用量查询超时") from exc
    except httpx.HTTPError as exc:
        raise CodexAuthError("Codex 用量查询请求发送失败") from exc
    if response.status_code in {401, 403}:
        raise CodexAuthExpired("Codex 登录已失效，请重新直登授权")
    if not response.is_success:
        raise CodexAuthError(f"Codex 用量查询失败（HTTP {response.status_code}）")
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise CodexAuthError("Codex 用量响应不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise CodexAuthError("Codex 用量响应格式不正确")

    rate_limit = payload.get("rate_limit")
    rate_limit = rate_limit if isinstance(rate_limit, dict) else {}
    windows = [
        window
        for window in (
            _parse_usage_window(rate_limit.get("primary_window"), fallback="主额度窗口"),
            _parse_usage_window(rate_limit.get("secondary_window"), fallback="次额度窗口"),
        )
        if window is not None
    ]
    credits = payload.get("credits")
    credits = credits if isinstance(credits, dict) else {}
    balance = credits.get("balance")
    plan_type = payload.get("plan_type")
    return CodexUsage(
        plan_type=str(plan_type) if isinstance(plan_type, str) else credentials.plan_type,
        windows=windows,
        credits_balance=str(balance) if isinstance(balance, (str, int, float)) else None,
        credits_unlimited=credits.get("unlimited") is True,
        has_credits=(
            credits.get("has_credits") if isinstance(credits.get("has_credits"), bool) else None
        ),
        limit_reached=rate_limit.get("limit_reached") is True,
    )


@dataclass(frozen=True, slots=True)
class CodexDeviceLogin:
    device_auth_id: str
    user_code: str
    verification_url: str
    interval_seconds: int


def start_codex_device_login(
    *,
    timeout_seconds: float = 20.0,
    transport: httpx.BaseTransport | None = None,
) -> CodexDeviceLogin:
    """Begin the headless device-code flow (the only server-side login path).

    The loopback flow Codex CLI uses binds ``localhost:1455`` on the user's own
    machine, which a hosted backend cannot do; the device code is the
    documented headless alternative in the same CLI.
    """

    try:
        with _client(timeout_seconds=timeout_seconds, transport=transport) as client:
            response = client.post(
                CODEX_DEVICE_CODE_URL,
                json={"client_id": CODEX_CLIENT_ID},
            )
    except httpx.TimeoutException as exc:
        raise CodexAuthError("Codex 直登请求超时") from exc
    except httpx.HTTPError as exc:
        raise CodexAuthError("Codex 直登请求发送失败") from exc
    if not response.is_success:
        raise CodexAuthError(f"Codex 直登请求失败（HTTP {response.status_code}）")
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise CodexAuthError("Codex 直登响应不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise CodexAuthError("Codex 直登响应格式不正确")
    device_auth_id = str(payload.get("device_auth_id") or "").strip()
    user_code = str(payload.get("user_code") or payload.get("usercode") or "").strip()
    if not device_auth_id or not user_code:
        raise CodexAuthError("Codex 直登响应缺少设备码")
    raw_interval = payload.get("interval")
    try:
        interval = int(str(raw_interval)) if raw_interval is not None else 5
    except ValueError:
        interval = 5
    return CodexDeviceLogin(
        device_auth_id=device_auth_id,
        user_code=user_code,
        verification_url=CODEX_DEVICE_VERIFICATION_URL,
        interval_seconds=max(1, min(interval, 30)),
    )


def poll_codex_device_login(
    *,
    device_auth_id: str,
    user_code: str,
    timeout_seconds: float = 20.0,
    transport: httpx.BaseTransport | None = None,
) -> CodexCredentials | None:
    """Return credentials once the user approves, or ``None`` while pending."""

    try:
        with _client(timeout_seconds=timeout_seconds, transport=transport) as client:
            response = client.post(
                CODEX_DEVICE_TOKEN_URL,
                json={"device_auth_id": device_auth_id, "user_code": user_code},
            )
            # 403/404 is the documented "not approved yet" signal.
            if response.status_code in {403, 404}:
                return None
            if not response.is_success:
                raise CodexAuthError(f"Codex 直登轮询失败（HTTP {response.status_code}）")
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise CodexAuthError("Codex 直登轮询响应不是合法 JSON") from exc
            if not isinstance(payload, dict):
                raise CodexAuthError("Codex 直登轮询响应格式不正确")
            code = str(payload.get("authorization_code") or "").strip()
            verifier = str(payload.get("code_verifier") or "").strip()
            if not code or not verifier:
                return None
            token_response = client.post(
                CODEX_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": CODEX_DEVICE_REDIRECT_URI,
                    "client_id": CODEX_CLIENT_ID,
                    "code_verifier": verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.TimeoutException as exc:
        raise CodexAuthError("Codex 直登轮询超时") from exc
    except httpx.HTTPError as exc:
        raise CodexAuthError("Codex 直登轮询请求发送失败") from exc
    if not token_response.is_success:
        raise CodexAuthError(
            f"Codex 直登令牌交换失败（HTTP {token_response.status_code}）"
        )
    try:
        tokens = token_response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise CodexAuthError("Codex 直登令牌响应不是合法 JSON") from exc
    if not isinstance(tokens, dict):
        raise CodexAuthError("Codex 直登令牌响应格式不正确")
    access_token = str(tokens.get("access_token") or "").strip()
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    id_token = str(tokens.get("id_token") or "").strip() or None
    if not access_token or not refresh_token:
        raise CodexAuthError("Codex 直登令牌响应缺少令牌")
    account_id = codex_account_id(id_token, access_token)
    if not account_id:
        raise CodexAuthError("Codex 直登令牌缺少 chatgpt_account_id")
    return CodexCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
        account_id=account_id,
        plan_type=codex_plan_type(id_token, access_token),
    )
