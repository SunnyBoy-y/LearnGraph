from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app.domain.schemas.research import SearchResult


class SearchProviderError(RuntimeError):
    """A configured search provider failed; callers must not fall back silently."""


class SearchProviderTimeout(SearchProviderError):
    pass


class SearchProviderResponseError(SearchProviderError):
    pass


def normalize_domain(value: str) -> str:
    candidate = value.strip().casefold()
    if "://" in candidate:
        candidate = urlparse(candidate).hostname or ""
    return candidate.strip(".")


def domain_is_allowed(url: str, allowed_domains: set[str] | None) -> bool:
    if not allowed_domains:
        return True
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.casefold().strip(".")
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed_domains)


def _safe_image_url(value: Any) -> str | None:
    """Return a bounded public http(s) URL for a vendor thumbnail, else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return candidate[:2_000]


class SearXNGSearchProvider:
    """SearXNG JSON search adapter for an explicitly configured instance."""

    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 15.0,
        allow_private_bridge_urls: bool = False,
    ) -> None:
        self.provider_id = provider_id
        # Local import avoids a module cycle: fetch.py reuses domain_is_allowed
        # from this module while this constructor validates bridge egress.
        from app.providers.remote.fetch import validate_bridge_url

        self.base_url = validate_bridge_url(base_url, allow_private=allow_private_bridge_urls)
        self.api_key = api_key
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def search(
        self,
        query: str,
        max_results: int,
        *,
        allowed_domains: set[str] | None = None,
    ) -> list[SearchResult]:
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
                response = client.get(
                    f"{self.base_url}/search",
                    params={"q": query, "format": "json"},
                )
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeout("Search provider timed out") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError("Search provider request failed") from exc
        if not response.is_success:
            raise SearchProviderError(f"Search provider returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SearchProviderResponseError("Search provider returned non-JSON data") from exc
        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise SearchProviderResponseError("Search provider response has no results array")

        permitted_domains = {
            domain
            for value in (allowed_domains or set())
            if (domain := normalize_domain(value))
        }
        now = datetime.now(timezone.utc)
        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            url = item.get("url")
            snippet = item.get("content") or item.get("snippet") or ""
            if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
                continue
            if not domain_is_allowed(url, permitted_domains):
                continue
            results.append(
                SearchResult(
                    title=title.strip()[:1_000],
                    url=url[:4_000],
                    snippet=str(snippet).strip()[:8_000],
                    source_type="web_search",
                    fetched_at=now,
                    image_url=_safe_image_url(
                        item.get("img_src")
                        or item.get("thumbnail_src")
                        or item.get("image_url")
                    ),
                )
            )
            if len(results) >= max_results:
                break
        return results

    def probe(self) -> dict[str, object]:
        results = self.search("LearnGraph provider capability check", 1)
        return {
            "capability": "search",
            "provider_type": "searxng",
            "result_count": len(results),
        }


class CloudSearchProvider:
    """Real HTTP adapters for the explicitly selected cloud search boundary."""

    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        provider_type: str,
        base_url: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 15.0,
        allow_private_bridge_urls: bool = False,
    ) -> None:
        if provider_type not in {"tavily", "exa", "brave_search", "firecrawl_search"}:
            raise ValueError("Unsupported cloud search provider type")
        self.provider_id = provider_id
        self.provider_type = provider_type
        # Local import avoids a module cycle; see SearXNGSearchProvider above.
        from app.providers.remote.fetch import validate_bridge_url

        self.base_url = validate_bridge_url(base_url, allow_private=allow_private_bridge_urls)
        self.api_key = api_key
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def _request(self, query: str, max_results: int) -> object:
        headers = {"Accept": "application/json"}
        method = "POST"
        url = f"{self.base_url}/search"
        params: dict[str, Any] | None = None
        body: dict[str, Any] | None = {"query": query}
        if self.provider_type == "tavily":
            headers["Authorization"] = f"Bearer {self.api_key}"
            body.update({"max_results": max_results, "include_answer": False})
        elif self.provider_type == "exa":
            headers["x-api-key"] = self.api_key
            body.update({"numResults": max_results, "contents": {"text": {"maxCharacters": 8_000}}})
        elif self.provider_type == "brave_search":
            method = "GET"
            url = f"{self.base_url}/res/v1/web/search"
            headers["X-Subscription-Token"] = self.api_key
            params = {"q": query, "count": max_results}
            body = None
        else:
            url = f"{self.base_url}/v1/search"
            headers["Authorization"] = f"Bearer {self.api_key}"
            body.update({"limit": max_results})
        try:
            with httpx.Client(
                headers=headers,
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.request(method, url, params=params, json=body)
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeout("Search provider timed out") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError("Search provider request failed") from exc
        if not response.is_success:
            raise SearchProviderError(f"Search provider returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SearchProviderResponseError("Search provider returned non-JSON data") from exc

    def search(
        self,
        query: str,
        max_results: int,
        *,
        allowed_domains: set[str] | None = None,
    ) -> list[SearchResult]:
        payload = self._request(query, max_results)
        if not isinstance(payload, dict):
            raise SearchProviderResponseError("Search provider response must be an object")
        if self.provider_type == "brave_search":
            web = payload.get("web")
            raw_results = web.get("results") if isinstance(web, dict) else None
        elif self.provider_type == "firecrawl_search":
            raw_results = payload.get("data")
            if isinstance(raw_results, dict):
                raw_results = raw_results.get("web") or raw_results.get("results")
        else:
            raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise SearchProviderResponseError("Search provider response has no results array")
        permitted_domains = {
            domain
            for value in (allowed_domains or set())
            if (domain := normalize_domain(value))
        }
        now = datetime.now(timezone.utc)
        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            title = item.get("title") or item.get("name")
            snippet = (
                item.get("content")
                or item.get("text")
                or item.get("description")
                or item.get("snippet")
                or ""
            )
            if not isinstance(url, str) or not isinstance(title, str) or not title.strip():
                continue
            if not domain_is_allowed(url, permitted_domains):
                continue
            results.append(
                SearchResult(
                    title=title.strip()[:1_000],
                    url=url[:4_000],
                    snippet=str(snippet).strip()[:8_000],
                    source_type=f"{self.provider_type}_web_search",
                    fetched_at=now,
                    image_url=_safe_image_url(
                        item.get("image_url")
                        or (
                            item.get("thumbnail", {}).get("src")
                            if isinstance(item.get("thumbnail"), dict)
                            else None
                        )
                        or (
                            (item.get("meta") or {}).get("image")
                            if isinstance(item.get("meta"), dict)
                            else None
                        )
                    ),
                )
            )
            if len(results) >= max_results:
                break
        return results

    def probe(self) -> dict[str, object]:
        results = self.search("LearnGraph provider capability check", 1)
        return {
            "capability": "search",
            "provider_type": self.provider_type,
            "result_count": len(results),
        }


class UnavailableSearchProvider:
    """Keeps a broken explicit configuration visible instead of returning fixtures."""

    remote_capability = True
    available = False

    def __init__(self, provider_id: str, reason: str) -> None:
        self.provider_id = provider_id
        self.reason = reason

    def search(
        self,
        query: str,
        max_results: int,
        *,
        allowed_domains: set[str] | None = None,
    ) -> list[SearchResult]:
        del query, max_results, allowed_domains
        raise SearchProviderError(self.reason)

    def probe(self) -> dict[str, object]:
        raise SearchProviderError(self.reason)
