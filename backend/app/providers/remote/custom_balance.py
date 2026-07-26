from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit

import httpx


class CustomBalanceQueryError(RuntimeError):
    """A safe failure while executing a user-configured balance request."""


_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
# Hop-by-hop / transport headers always come from the HTTP client itself.
_BLOCKED_HEADER_NAMES = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
}
_MAX_RESPONSE_CHARS = 262_144

BASE_URL_PLACEHOLDER = "{{baseUrl}}"
API_KEY_PLACEHOLDER = "{{apiKey}}"


def substitute_balance_template(
    text: str,
    *,
    base_url: str,
    api_key: str,
    variables: dict[str, str] | None = None,
) -> str:
    """Fill in the cc-switch style ``{{baseUrl}}`` / ``{{apiKey}}`` variables,
    plus any user-defined extras (e.g. ``{{accessToken}}`` for NewAPI)."""

    result = text.replace(BASE_URL_PLACEHOLDER, base_url).replace(
        API_KEY_PLACEHOLDER, api_key
    )
    for name, value in (variables or {}).items():
        result = result.replace("{{" + name + "}}", str(value))
    return result


def _is_private_host(host: str) -> bool:
    lowered = host.casefold().strip("[]")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(
        ".localhost"
    ):
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def validate_custom_balance_url(url: str) -> None:
    """The saved key may only travel to https origins, or plain http on the
    user's own machine / LAN (self-hosted relay stations such as one-api on
    ``http://localhost``)."""

    try:
        parsed = urlsplit(url.strip())
    except ValueError as exc:
        raise CustomBalanceQueryError("查询 URL 无法解析") from exc
    scheme = parsed.scheme.casefold()
    host = parsed.hostname or ""
    if not host:
        raise CustomBalanceQueryError("查询 URL 缺少主机名")
    if parsed.username or parsed.password:
        raise CustomBalanceQueryError("查询 URL 不允许携带内嵌凭据")
    if scheme == "https":
        return
    if scheme == "http" and _is_private_host(host):
        return
    raise CustomBalanceQueryError(
        "查询 URL 仅支持 https，或本机 / 局域网地址上的 http"
    )


def _scrub_secret(text: str, api_key: str) -> str:
    if api_key and api_key in text:
        return text.replace(api_key, "***")
    return text


def execute_custom_balance_request(
    *,
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    base_url: str,
    api_key: str,
    variables: dict[str, str] | None = None,
    timeout_seconds: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Run one user-configured balance request and return the raw response.

    Template variables are substituted server-side so the plaintext key never
    reaches the browser; any echo of the key in the response is scrubbed for
    the same reason. The extractor script itself runs client-side.
    """

    method_name = (method or "GET").strip().upper()
    if method_name not in _ALLOWED_METHODS:
        raise CustomBalanceQueryError("查询请求方法不受支持")
    clean_base_url = base_url.strip().rstrip("/")
    final_url = substitute_balance_template(
        url.strip(), base_url=clean_base_url, api_key=api_key, variables=variables
    )
    validate_custom_balance_url(final_url)
    request_headers: dict[str, str] = {}
    for name, value in (headers or {}).items():
        header_name = str(name).strip()
        if not header_name or header_name.casefold() in _BLOCKED_HEADER_NAMES:
            continue
        request_headers[header_name] = substitute_balance_template(
            str(value).strip(),
            base_url=clean_base_url,
            api_key=api_key,
            variables=variables,
        )
    request_body = (
        substitute_balance_template(
            body, base_url=clean_base_url, api_key=api_key, variables=variables
        )
        if body
        else None
    )
    try:
        with httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
        ) as client:
            response = client.request(
                method_name,
                final_url,
                headers=request_headers,
                content=(
                    request_body.encode("utf-8")
                    if request_body is not None
                    else None
                ),
            )
    except httpx.TimeoutException as exc:
        raise CustomBalanceQueryError("余额查询请求超时") from exc
    except httpx.HTTPError as exc:
        raise CustomBalanceQueryError("余额查询请求发送失败") from exc
    text = _scrub_secret(response.text[:_MAX_RESPONSE_CHARS], api_key)
    payload: object | None = None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        payload = None
    return {
        "status_code": response.status_code,
        "ok": response.is_success,
        "payload": payload,
        "text": None if payload is not None else text,
    }
