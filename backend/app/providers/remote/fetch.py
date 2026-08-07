from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from requests import exceptions as requests_exceptions

from app.providers.remote.search import domain_is_allowed, normalize_domain


class FetchProviderError(RuntimeError):
    pass


class FetchProviderTimeout(FetchProviderError):
    pass


class UnsafeFetchURL(FetchProviderError):
    pass


@dataclass(frozen=True)
class FetchedDocument:
    source_url: str
    final_url: str
    title: str
    content: str
    content_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _resolve_public_host(hostname: str) -> str:
    """Resolve every address for a host and reject unsafe SSRF destinations."""
    normalized = hostname.casefold().strip(".")
    try:
        addresses = [ipaddress.ip_address(normalized)]
    except ValueError:
        try:
            # getaddrinfo returns both A and AAAA results when available.  Every
            # resolved address must be public; accepting one public answer while
            # another is private creates a DNS-rebinding/round-robin SSRF path.
            addresses = list(
                {
                    ipaddress.ip_address(info[4][0])
                    for info in socket.getaddrinfo(
                        normalized,
                        None,
                        family=socket.AF_UNSPEC,
                        type=socket.SOCK_STREAM,
                    )
                }
            )
        except OSError as exc:
            raise UnsafeFetchURL("The source host could not be resolved safely") from exc
    if not addresses or any(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        for address in addresses
    ):
        raise UnsafeFetchURL(
            "Private, loopback, link-local, metadata, multicast, or reserved source addresses are blocked"
        )
    return normalize_domain(normalized)


def _parse_public_http_url(url: str, *, label: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeFetchURL(f"Only absolute HTTP(S) {label} URLs are allowed")
    # A bridge URL with userinfo can hide an unexpected authority from logs and
    # configuration review; credentials belong in ProviderSecret, never URLs.
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise UnsafeFetchURL(f"{label.capitalize()} URLs must not contain userinfo")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeFetchURL(f"{label.capitalize()} URL has an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise UnsafeFetchURL(f"{label.capitalize()} URL has an invalid port")
    return normalize_domain(parsed.hostname), parsed.geturl()


def validate_bridge_url(url: str, *, allow_private: bool = False) -> str:
    """Validate an admin-configured provider bridge URL before connecting to it.

    Unlike document fetch targets, bridge URLs do not have a caller-provided
    domain allowlist. They still must be HTTP(S), credential-free, and have a
    valid port; unless ``allow_private`` is set for a trusted host network they
    must also resolve exclusively to public addresses.
    """
    hostname, normalized_url = _parse_public_http_url(url, label="provider bridge")
    if not allow_private:
        _resolve_public_host(hostname)
    return normalized_url.rstrip("/")


def require_public_http_url(url: str, allowed_domains: set[str]) -> str:
    hostname, _ = _parse_public_http_url(url, label="source")
    if not domain_is_allowed(url, allowed_domains):
        raise UnsafeFetchURL("The source URL is outside the authorized domain set")
    return _resolve_public_host(hostname)


class Crawl4AIHTTPFetchProvider:
    """Adapter for a self-hosted Crawl4AI HTTP bridge.

    The bridge contract is intentionally narrow: ``POST /crawl`` receives a
    JSON object with ``url`` and returns ``final_url``, ``title``, and either
    ``markdown`` or ``content``.  LearnGraph validates the requested and final
    URL before persisting the result, so a bridge cannot turn into an SSRF path.
    """

    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
        max_content_chars: int = 2_000_000,
        allow_private_bridge_urls: bool = False,
    ) -> None:
        self.provider_id = provider_id
        self.base_url = validate_bridge_url(base_url, allow_private=allow_private_bridge_urls)
        self.api_key = api_key
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.max_content_chars = max_content_chars

    def fetch(self, url: str) -> FetchedDocument:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(
                headers=headers,
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.post(f"{self.base_url}/crawl", json={"url": url})
        except httpx.TimeoutException as exc:
            raise FetchProviderTimeout("Fetch provider timed out") from exc
        except httpx.HTTPError as exc:
            raise FetchProviderError("Fetch provider request failed") from exc
        if not response.is_success:
            raise FetchProviderError(f"Fetch provider returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise FetchProviderError("Fetch provider returned non-JSON data") from exc
        if not isinstance(payload, dict):
            raise FetchProviderError("Fetch provider response must be an object")
        final_url = payload.get("final_url") or payload.get("url") or url
        content = payload.get("markdown") or payload.get("content")
        if not isinstance(final_url, str) or not isinstance(content, str):
            raise FetchProviderError("Fetch provider response lacks final_url or content")
        if len(content) > self.max_content_chars:
            raise FetchProviderError("Fetch provider response exceeds the configured content limit")
        title = payload.get("title")
        content_type = payload.get("content_type") or "text/markdown"
        return FetchedDocument(
            source_url=url,
            final_url=final_url,
            title=str(title or "")[:1_000],
            content=content,
            content_type=str(content_type)[:160],
            metadata={"bridge": "crawl4ai_http"},
        )

    def probe(self) -> dict[str, object]:
        document = self.fetch("https://example.com/")
        return {
            "capability": "fetch",
            "provider_type": "crawl4ai_http",
            "content_chars": len(document.content),
            "final_url": document.final_url,
        }


class FirecrawlFetchProvider:
    """Firecrawl SDK adapter used only when selected as the FetchProvider."""

    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
        max_content_chars: int = 2_000_000,
        allow_private_bridge_urls: bool = False,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.base_url = validate_bridge_url(base_url, allow_private=allow_private_bridge_urls)
        self.api_key = api_key
        # ``firecrawl-py`` owns its HTTP client and cannot accept an httpx
        # transport. Keep this test seam so tests never need a real key or a
        # billable outbound request.
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.max_content_chars = max_content_chars
        self.client_factory = client_factory

    @property
    def available(self) -> bool:
        return True

    def _client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory(
                api_key=self.api_key,
                api_url=self.base_url,
                timeout=self.timeout_seconds,
            )
        try:
            from firecrawl import Firecrawl
        except ImportError as exc:
            raise FetchProviderError("Firecrawl SDK is not installed") from exc
        return Firecrawl(
            api_key=self.api_key,
            api_url=self.base_url,
            timeout=self.timeout_seconds,
        )

    @staticmethod
    def _field(document: Any, name: str, default: Any = None) -> Any:
        if isinstance(document, dict):
            return document.get(name, default)
        return getattr(document, name, default)

    def fetch(self, url: str) -> FetchedDocument:
        try:
            # Use the documented v2 SDK entry point; its client normalizes the
            # Firecrawl response into snake_case fields before returning it.
            document = self._client().scrape(url, formats=["markdown"])
        except (httpx.TimeoutException, requests_exceptions.Timeout) as exc:
            raise FetchProviderTimeout("Firecrawl scrape timed out") from exc
        except TimeoutError as exc:
            raise FetchProviderTimeout("Firecrawl scrape timed out") from exc
        except Exception as exc:
            raise FetchProviderError("Firecrawl scrape request failed") from exc

        markdown = self._field(document, "markdown")
        metadata = self._field(document, "metadata") or {}
        if not isinstance(markdown, str) or not markdown.strip():
            raise FetchProviderError("Firecrawl scrape returned no markdown content")
        if not isinstance(metadata, dict) and not hasattr(metadata, "source_url"):
            raise FetchProviderError("Firecrawl scrape returned invalid metadata")
        if len(markdown) > self.max_content_chars:
            raise FetchProviderError("Firecrawl scrape exceeds the configured content limit")

        final_url = self._field(metadata, "source_url") or self._field(metadata, "url") or url
        if not isinstance(final_url, str):
            raise FetchProviderError("Firecrawl scrape returned an invalid final URL")
        status_code = self._field(metadata, "status_code")
        title = self._field(metadata, "title")
        content_type = self._field(metadata, "content_type") or "text/markdown"
        return FetchedDocument(
            source_url=url,
            final_url=final_url,
            title=str(title or "")[:1_000],
            content=markdown,
            content_type=str(content_type)[:160],
            metadata={"bridge": "firecrawl", "status_code": status_code},
        )

    def probe(self) -> dict[str, object]:
        document = self.fetch("https://example.com/")
        return {
            "capability": "fetch",
            "provider_type": "firecrawl_fetch",
            "content_chars": len(document.content),
            "final_url": document.final_url,
        }


class UnavailableFetchProvider:
    remote_capability = True
    available = False

    def __init__(self, provider_id: str, reason: str) -> None:
        self.provider_id = provider_id
        self.reason = reason

    def fetch(self, url: str) -> FetchedDocument:
        del url
        raise FetchProviderError(self.reason)

    def probe(self) -> dict[str, object]:
        raise FetchProviderError(self.reason)
