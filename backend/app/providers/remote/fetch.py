from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

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


def require_public_http_url(url: str, allowed_domains: set[str]) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeFetchURL("Only absolute HTTP(S) source URLs are allowed")
    if not domain_is_allowed(url, allowed_domains):
        raise UnsafeFetchURL("The source URL is outside the authorized domain set")
    hostname = parsed.hostname.casefold().strip(".")
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = [literal]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(info[4][0])
                for info in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            ]
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
        raise UnsafeFetchURL("Private, loopback, link-local, or reserved source addresses are blocked")
    return normalize_domain(hostname)


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
    ) -> None:
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
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
    """Firecrawl scrape adapter used only when explicitly selected as FetchProvider."""

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
    ) -> None:
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.max_content_chars = max_content_chars

    def fetch(self, url: str) -> FetchedDocument:
        try:
            with httpx.Client(
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    f"{self.base_url}/v1/scrape",
                    json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
                )
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
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise FetchProviderError("Fetch provider response has no data object")
        markdown = data.get("markdown")
        metadata = data.get("metadata") or {}
        if not isinstance(markdown, str) or not isinstance(metadata, dict):
            raise FetchProviderError("Fetch provider response lacks markdown content")
        if len(markdown) > self.max_content_chars:
            raise FetchProviderError("Fetch provider response exceeds the configured content limit")
        final_url = metadata.get("sourceURL") or metadata.get("url") or url
        if not isinstance(final_url, str):
            raise FetchProviderError("Fetch provider returned an invalid final URL")
        return FetchedDocument(
            source_url=url,
            final_url=final_url,
            title=str(metadata.get("title") or "")[:1_000],
            content=markdown,
            content_type="text/markdown",
            metadata={"bridge": "firecrawl", "status_code": metadata.get("statusCode")},
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

    def __init__(self, provider_id: str, reason: str) -> None:
        self.provider_id = provider_id
        self.reason = reason

    def fetch(self, url: str) -> FetchedDocument:
        del url
        raise FetchProviderError(self.reason)

    def probe(self) -> dict[str, object]:
        raise FetchProviderError(self.reason)
