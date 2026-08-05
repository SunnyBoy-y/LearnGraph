from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

from app.providers.remote.openai import merge_provider_request_headers


class ProviderBalanceError(RuntimeError):
    """A safe, provider-neutral balance retrieval failure."""


@dataclass(frozen=True, slots=True)
class BalanceInfo:
    currency: str
    total_balance: str
    granted_balance: str | None = None
    topped_up_balance: str | None = None


@dataclass(frozen=True, slots=True)
class BalanceReport:
    vendor: str
    vendor_label: str
    is_available: bool
    infos: list[BalanceInfo] = field(default_factory=list)
    notice: str | None = None


def _https_host(base_url: str | None) -> str:
    if not base_url:
        return ""
    try:
        parsed = urlsplit(base_url.strip())
    except ValueError:
        return ""
    if parsed.scheme.casefold() != "https" or parsed.username or parsed.password:
        return ""
    return (parsed.hostname or "").casefold()


# Official platforms that are verified (2026-07) to NOT expose an
# API-key-based balance endpoint.  Attempting the relay-station billing
# convention against them would only send the saved key to extra routes
# and produce misleading 404/401 errors, so they get an explicit refusal.
_NO_BALANCE_NOTICES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("api.openai.com",),
        "OpenAI 官方不提供 API Key 余额查询（旧 dashboard 账单接口已停用，"
        "成本需组织 Admin Key 走 Costs API）。请在 platform.openai.com 控制台查看。",
    ),
    (
        ("dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"),
        "阿里云百炼不提供 API Key 余额查询；账户余额需阿里云 AccessKey 调用 "
        "BSS OpenAPI（QueryAccountBalance）或在费用中心查看。",
    ),
    (
        ("api.minimaxi.com", "api.minimax.io"),
        "MiniMax 未提供 API Key 余额查询接口（余额不足时推理接口返回错误码 1008）。"
        "请在 MiniMax 开放平台控制台查看余额。",
    ),
    (
        ("api.xiaomimimo.com",),
        "小米 MiMo 未提供余额查询接口，请在 platform.xiaomimimo.com 控制台查看余额。",
    ),
    (
        ("generativelanguage.googleapis.com",),
        "Gemini API 不提供余额/配额查询接口，请在 Google AI Studio 的 "
        "Usage / Billing 页面查看。",
    ),
    (
        ("api.anthropic.com",),
        "Anthropic 不提供余额查询接口（Admin API 仅报告用量与成本），"
        "请在 platform.claude.com 控制台查看。",
    ),
)


def official_no_balance_notice(base_url: str | None) -> str | None:
    host = _https_host(base_url)
    if not host:
        return None
    for hosts, notice in _NO_BALANCE_NOTICES:
        if host in hosts:
            return notice
    if host.endswith(".maas.aliyuncs.com"):
        return _NO_BALANCE_NOTICES[1][1]
    return None


def supports_gateway_billing(base_url: str | None) -> bool:
    """The relay-station billing convention only ever runs over https."""

    return bool(_https_host(base_url))


def detect_balance_vendor(base_url: str | None) -> str | None:
    """Map an official origin to its balance implementation, if one exists."""

    host = _https_host(base_url)
    if host in {"api.moonshot.cn", "api.moonshot.ai"}:
        return "moonshot"
    if host in {"api.siliconflow.cn", "api.siliconflow.com"}:
        return "siliconflow"
    if host == "openrouter.ai":
        return "openrouter"
    return None


def _get_json(
    url: str,
    *,
    api_key: str,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None,
    extra_headers: dict[str, str] | None,
    params: dict[str, str] | None = None,
) -> dict:
    try:
        with httpx.Client(
            headers=merge_provider_request_headers(
                api_key=api_key,
                extra_headers=extra_headers,
            ),
            timeout=timeout_seconds,
            transport=transport,
        ) as client:
            response = client.get(url, params=params)
    except httpx.TimeoutException as exc:
        raise ProviderBalanceError("Balance request timed out") from exc
    except httpx.HTTPError as exc:
        raise ProviderBalanceError("Balance request could not be sent") from exc
    if not response.is_success:
        raise ProviderBalanceError(
            f"Balance request failed with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderBalanceError("Balance response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ProviderBalanceError("Balance response had an invalid schema")
    return payload


def _as_amount(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ProviderBalanceError(f"Balance response had an invalid {label}")
    try:
        return float(value)
    except ValueError as exc:
        raise ProviderBalanceError(f"Balance response had an invalid {label}") from exc


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def _v1_root(base_url: str) -> str:
    """Return the ``…/v1`` API root regardless of how the base URL was saved."""

    root = base_url.strip().rstrip("/")
    return root if root.endswith("/v1") else f"{root}/v1"


def fetch_moonshot_balance(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float = 15.0,
    transport: httpx.BaseTransport | None = None,
    extra_headers: dict[str, str] | None = None,
) -> BalanceReport:
    """Moonshot/Kimi official balance: GET /v1/users/me/balance."""

    payload = _get_json(
        f"{_v1_root(base_url)}/users/me/balance",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        transport=transport,
        extra_headers=extra_headers,
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ProviderBalanceError("Balance response had no data object")
    available = _as_amount(data.get("available_balance"), label="available_balance")
    voucher = _as_amount(data.get("voucher_balance"), label="voucher_balance")
    cash = _as_amount(data.get("cash_balance"), label="cash_balance")
    currency = "CNY" if _https_host(base_url) == "api.moonshot.cn" else "USD"
    return BalanceReport(
        vendor="moonshot",
        vendor_label="Moonshot / Kimi",
        is_available=available > 0,
        infos=[
            BalanceInfo(
                currency=currency,
                total_balance=_fmt(available),
                granted_balance=_fmt(voucher),
                topped_up_balance=_fmt(cash),
            )
        ],
        # cash_balance may be negative (arrears); the sum explains that state.
        notice="可用余额 = 现金余额 + 代金券余额；现金余额可能为负（欠费）。",
    )


def fetch_siliconflow_balance(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float = 15.0,
    transport: httpx.BaseTransport | None = None,
    extra_headers: dict[str, str] | None = None,
) -> BalanceReport:
    """SiliconFlow official balance: GET /v1/user/info."""

    payload = _get_json(
        f"{_v1_root(base_url)}/user/info",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        transport=transport,
        extra_headers=extra_headers,
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ProviderBalanceError("Balance response had no data object")
    total = _as_amount(data.get("totalBalance"), label="totalBalance")
    gift = _as_amount(data.get("balance"), label="balance")
    charged = _as_amount(data.get("chargeBalance"), label="chargeBalance")
    currency = "CNY" if _https_host(base_url) == "api.siliconflow.cn" else "USD"
    return BalanceReport(
        vendor="siliconflow",
        vendor_label="SiliconFlow",
        is_available=total > 0,
        infos=[
            BalanceInfo(
                currency=currency,
                total_balance=_fmt(total),
                granted_balance=_fmt(gift),
                topped_up_balance=_fmt(charged),
            )
        ],
    )


def fetch_openrouter_balance(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float = 15.0,
    transport: httpx.BaseTransport | None = None,
    extra_headers: dict[str, str] | None = None,
) -> BalanceReport:
    """OpenRouter credits: GET https://openrouter.ai/api/v1/credits."""

    del base_url  # Only the verified official origin may receive the key.
    payload = _get_json(
        "https://openrouter.ai/api/v1/credits",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        transport=transport,
        extra_headers=extra_headers,
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ProviderBalanceError("Balance response had no data object")
    credits = _as_amount(data.get("total_credits"), label="total_credits")
    usage = _as_amount(data.get("total_usage"), label="total_usage")
    remaining = credits - usage
    return BalanceReport(
        vendor="openrouter",
        vendor_label="OpenRouter",
        is_available=remaining > 0,
        infos=[
            BalanceInfo(
                currency="USD",
                total_balance=_fmt(remaining),
                granted_balance=None,
                topped_up_balance=_fmt(credits),
            )
        ],
        notice=f"累计充值 ${_fmt(credits)}，已消耗 ${_fmt(usage)}。",
    )


def fetch_gateway_billing_balance(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float = 15.0,
    transport: httpx.BaseTransport | None = None,
    extra_headers: dict[str, str] | None = None,
    today: date | None = None,
) -> BalanceReport:
    """one-api / new-api relay-station convention.

    GET /v1/dashboard/billing/subscription exposes the token quota as
    ``hard_limit_usd``; GET /v1/dashboard/billing/usage reports consumption in
    hundredths of a dollar.  Only stations implement these routes — official
    vendor origins are filtered out before this call.
    """

    root = base_url.strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    subscription = _get_json(
        f"{root}/v1/dashboard/billing/subscription",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        transport=transport,
        extra_headers=extra_headers,
    )
    hard_limit = _as_amount(
        subscription.get("hard_limit_usd"), label="hard_limit_usd"
    )
    anchor = today or date.today()
    usage_payload = _get_json(
        f"{root}/v1/dashboard/billing/usage",
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        transport=transport,
        extra_headers=extra_headers,
        params={
            # one-api's own checker window: usage since ~99 days ago.
            "start_date": (anchor - timedelta(days=99)).isoformat(),
            "end_date": (anchor + timedelta(days=1)).isoformat(),
        },
    )
    used = _as_amount(usage_payload.get("total_usage"), label="total_usage") / 100.0
    remaining = hard_limit - used
    return BalanceReport(
        vendor="gateway",
        vendor_label="OpenAI 兼容中转站",
        is_available=remaining > 0,
        infos=[
            BalanceInfo(
                currency="USD",
                total_balance=_fmt(remaining),
                granted_balance=None,
                topped_up_balance=_fmt(hard_limit),
            )
        ],
        notice=(
            f"按 one-api/new-api 账单惯例估算：站点配额 ${_fmt(hard_limit)}，"
            f"近 100 天已用 ${_fmt(used)}。数值口径以站点面板为准。"
        ),
    )


def _rfc3986(value: object) -> str:
    """RFC 3986 percent-encoding used by Aliyun RPC signatures.

    Unreserved characters ``A-Z a-z 0-9 - . _ ~`` stay literal; everything
    else becomes ``%XX`` with uppercase hex digits and spaces become ``%20``
    (never ``+``).
    """
    return quote(str(value), safe="-_.~")


def _bss_signed_url(
    *,
    access_key_id: str,
    access_key_secret: str,
    now: datetime | None = None,
) -> str:
    """Build a signed Aliyun BSS ``QueryAccountBalance`` RPC URL.

    The string-to-sign is ``GET&%2F&<canonicalized-query>`` and the HMAC key
    is ``<AccessKeySecret>&``.  The signature is appended as the final query
    parameter so a transport test can assert both the RPC action and that a
    signature was produced without ever holding the secret.
    """
    params = {
        "Action": "QueryAccountBalance",
        "Format": "JSON",
        "Version": "2017-12-14",
        "AccessKeyId": access_key_id,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": uuid4().hex,
        "Timestamp": (now or datetime.now(UTC)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    canonical = "&".join(
        f"{_rfc3986(key)}={_rfc3986(value)}"
        for key, value in sorted(params.items())
    )
    string_to_sign = "GET&%2F&" + _rfc3986(canonical)
    signature = base64.b64encode(
        hmac.new(
            (access_key_secret + "&").encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("ascii")
    return f"https://business.aliyuncs.com/?{canonical}&Signature={_rfc3986(signature)}"


def _optional_amount(value: object) -> float | None:
    if value is None:
        return None
    try:
        return _as_amount(value, label="amount")
    except ProviderBalanceError:
        return None


def fetch_dashscope_balance(
    *,
    access_key_id: str,
    access_key_secret: str,
    timeout_seconds: float = 15.0,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> BalanceReport:
    """Query the Aliyun account balance via the BSS ``QueryAccountBalance`` RPC.

    DashScope API keys cannot read account balance; the account balance is
    exposed through the Aliyun BSS OpenAPI using an AccessKey pair.  The
    request is signed per the RPC signature protocol (HMAC-SHA1) and the
    AccessKey secret never leaves the server.
    """
    url = _bss_signed_url(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        now=now,
    )
    try:
        with httpx.Client(
            timeout=timeout_seconds, transport=transport
        ) as client:
            response = client.get(url)
    except httpx.TimeoutException as exc:
        raise ProviderBalanceError("Balance request timed out") from exc
    except httpx.HTTPError as exc:
        raise ProviderBalanceError("Balance request could not be sent") from exc
    if not response.is_success:
        raise ProviderBalanceError(
            f"Balance request failed with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderBalanceError("Balance response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ProviderBalanceError("Balance response had an invalid schema")
    code = str(payload.get("Code") or payload.get("code") or "").strip()
    if code not in {"200", "OK"}:
        message = str(
            payload.get("Message")
            or payload.get("message")
            or code
            or "unknown error"
        )
        raise ProviderBalanceError(f"Aliyun BSS returned: {message}")
    data = payload.get("Data")
    if not isinstance(data, dict):
        raise ProviderBalanceError("Balance response had no Data object")
    available = _as_amount(data.get("AvailableAmount"), label="AvailableAmount")
    raw_currency = str(data.get("Currency") or "CNY").strip().upper()
    currency = "USD" if raw_currency == "USD" else "CNY"
    cash = _optional_amount(data.get("CashAmount"))
    granted = _optional_amount(data.get("CreditAmount"))
    frozen = _optional_amount(data.get("FreezeAmount"))
    return BalanceReport(
        vendor="dashscope",
        vendor_label="阿里云百炼（账户余额）",
        is_available=available > 0,
        infos=[
            BalanceInfo(
                currency=currency,
                total_balance=_fmt(available),
                granted_balance=_fmt(granted) if granted is not None else None,
                topped_up_balance=_fmt(cash) if cash is not None else None,
            )
        ],
        notice=(
            f"账户可用余额 {_fmt(available)} {currency}"
            + (f"（冻结 {_fmt(frozen)}）" if frozen else "")
            + "，来自 BSS QueryAccountBalance。"
        ),
    )
