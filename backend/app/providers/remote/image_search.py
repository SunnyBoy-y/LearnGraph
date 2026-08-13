from __future__ import annotations

from typing import Any

import httpx

from app.providers.remote.search import (
    SearchProviderError,
    SearchProviderResponseError,
    SearchProviderTimeout,
)


class ImageSearchProvider:
    """轻量 REST 文搜图 lane：Tavily include_images / Openverse / Pexels / Pixabay。

    与 qwen_image_search（Responses API 双模式）不同，这些供应商只支持
    文搜图（text）：文本描述 → 公网图片 URL。传入 ``image_url`` 时抛出
    ``SearchProviderError``，由 Agent 运行时转成明确的错误提示。

    由 ``image_search_provider_for_workspace`` 在未配置 qwen 专用通道时作为
    回退 lane 使用；每个实例对应一个已启用的 Provider 配置行。
    """

    remote_capability = True
    supports_reverse_image = False

    _SUPPORTED_TYPES = frozenset(
        {
            "tavily_image_search",
            "openverse_image_search",
            "pexels_image_search",
            "pixabay_image_search",
        }
    )

    def __init__(
        self,
        *,
        provider_id: str,
        provider_type: str,
        base_url: str,
        api_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
        max_results: int = 12,
    ) -> None:
        if provider_type not in self._SUPPORTED_TYPES:
            raise ValueError("Unsupported image search provider type")
        self.provider_id = provider_id
        self.provider_type = provider_type
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.extra_headers = dict(extra_headers or {})
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.max_results = max(1, min(int(max_results), 20))

    # ------------------------------------------------------------------
    # Public lane entry point (duck-typed by agent_runtime._search_images)
    # ------------------------------------------------------------------

    def image_search(
        self,
        query: str,
        *,
        image_url: str | None = None,
    ) -> list[dict[str, str]]:
        if image_url is not None:
            raise SearchProviderError(
                "该文搜图供应商不支持图搜图（image_url）；"
                "请改用支持双模式的 qwen_image_search 或后续接入的图搜图供应商"
            )
        if self.provider_type == "tavily_image_search":
            return self._search_tavily(query)
        if self.provider_type == "openverse_image_search":
            return self._search_openverse(query)
        if self.provider_type == "pexels_image_search":
            return self._search_pexels(query)
        return self._search_pixabay(query)

    def probe(self) -> dict[str, object]:
        try:
            results = self.image_search("LearnGraph provider capability check")
            count = len(results)
        except SearchProviderError:
            count = 0
        return {
            "capability": "image_search",
            "provider_type": self.provider_type,
            "result_count": count,
        }

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    def _headers(self, **overrides: str) -> dict[str, str]:
        """Compose outbound headers; custom headers cannot smuggle credentials."""
        headers: dict[str, str] = {"Accept": "application/json"}
        for raw_key, raw_value in self.extra_headers.items():
            key = str(raw_key).strip()
            value = str(raw_value).strip()
            if not key or not value:
                continue
            if key.casefold() in {"authorization", "x-api-key", "api-key"}:
                continue
            headers[key] = value
        headers.update(overrides)
        return headers

    def _get_json(self, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        try:
            with httpx.Client(
                headers=headers,
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=True,
            ) as client:
                response = client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeout("Image search provider timed out") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError("Image search provider request failed") from exc
        if not response.is_success:
            raise SearchProviderError(
                f"Image search provider returned HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SearchProviderResponseError(
                "Image search provider returned non-JSON data"
            ) from exc

    def _post_json(self, url: str, *, json: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
        try:
            with httpx.Client(
                headers=headers,
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.post(url, json=json)
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeout("Image search provider timed out") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError("Image search provider request failed") from exc
        if not response.is_success:
            raise SearchProviderError(
                f"Image search provider returned HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SearchProviderResponseError(
                "Image search provider returned non-JSON data"
            ) from exc

    @staticmethod
    def _image_item(*, title: Any, url: Any, source_url: Any = None, thumbnail_url: Any = None) -> dict[str, str]:
        item: dict[str, str] = {
            "title": str(title or "")[:1_000],
            "url": str(url or "")[:4_000],
        }
        if source_url:
            item["source_url"] = str(source_url)[:4_000]
        if thumbnail_url:
            item["thumbnail_url"] = str(thumbnail_url)[:4_000]
        return item

    # ------------------------------------------------------------------
    # Vendors
    # ------------------------------------------------------------------

    def _search_tavily(self, query: str) -> list[dict[str, str]]:
        if not self.api_key:
            raise SearchProviderError("Tavily image search requires an API key")
        payload = self._post_json(
            f"{self.base_url}/search",
            headers=self._headers(Authorization=f"Bearer {self.api_key}"),
            json={
                "query": query,
                "include_images": True,
                "include_image_descriptions": True,
                "max_results": self.max_results,
                "search_depth": "basic",
            },
        )
        if not isinstance(payload, dict):
            raise SearchProviderResponseError("Tavily image search response must be an object")
        raw_images = payload.get("images")
        if not isinstance(raw_images, list):
            return []
        results: list[dict[str, str]] = []
        for raw in raw_images:
            if not isinstance(raw, dict):
                continue
            url = raw.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            results.append(
                self._image_item(
                    title=raw.get("description") or raw.get("title") or "Tavily 图片",
                    url=url,
                    source_url=raw.get("url"),
                )
            )
            if len(results) >= self.max_results:
                break
        return results

    def _search_openverse(self, query: str) -> list[dict[str, str]]:
        payload = self._get_json(
            f"{self.base_url}/v1/images/",
            headers=self._headers(),
            params={"q": query, "page_size": min(self.max_results, 20)},
        )
        if not isinstance(payload, dict):
            raise SearchProviderResponseError("Openverse image search response must be an object")
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            return []
        results: list[dict[str, str]] = []
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            url = raw.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            results.append(
                self._image_item(
                    title=raw.get("title") or "Openverse 图片",
                    url=url,
                    source_url=raw.get("foreign_landing_url"),
                    thumbnail_url=raw.get("thumbnail"),
                )
            )
            if len(results) >= self.max_results:
                break
        return results

    def _search_pexels(self, query: str) -> list[dict[str, str]]:
        if not self.api_key:
            raise SearchProviderError("Pexels image search requires an API key")
        payload = self._get_json(
            f"{self.base_url}/v1/search",
            headers=self._headers(Authorization=self.api_key),
            params={"query": query, "per_page": min(self.max_results, 20)},
        )
        if not isinstance(payload, dict):
            raise SearchProviderResponseError("Pexels image search response must be an object")
        raw_photos = payload.get("photos")
        if not isinstance(raw_photos, list):
            return []
        results: list[dict[str, str]] = []
        for raw in raw_photos:
            if not isinstance(raw, dict):
                continue
            src = raw.get("src")
            image_url = (
                (src.get("large2x") if isinstance(src, dict) else None)
                or (src.get("original") if isinstance(src, dict) else None)
                or raw.get("url")
            )
            if not isinstance(image_url, str) or not image_url.startswith(("http://", "https://")):
                continue
            results.append(
                self._image_item(
                    title=raw.get("alt") or "Pexels 图片",
                    url=image_url,
                    source_url=raw.get("url"),
                    thumbnail_url=(src.get("medium") if isinstance(src, dict) else None),
                )
            )
            if len(results) >= self.max_results:
                break
        return results

    def _search_pixabay(self, query: str) -> list[dict[str, str]]:
        if not self.api_key:
            raise SearchProviderError("Pixabay image search requires an API key")
        payload = self._get_json(
            f"{self.base_url}/",
            headers=self._headers(),
            params={
                "key": self.api_key,
                "q": query,
                "image_type": "photo",
                "per_page": min(self.max_results, 20),
                "safesearch": "true",
                "min_width": 200,
            },
        )
        if not isinstance(payload, dict):
            raise SearchProviderResponseError("Pixabay image search response must be an object")
        raw_hits = payload.get("hits")
        if not isinstance(raw_hits, list):
            return []
        results: list[dict[str, str]] = []
        for raw in raw_hits:
            if not isinstance(raw, dict):
                continue
            image_url = raw.get("largeImageURL") or raw.get("webformatURL") or raw.get("imageURL")
            if not isinstance(image_url, str) or not image_url.startswith(("http://", "https://")):
                continue
            results.append(
                self._image_item(
                    title=raw.get("tags") or "Pixabay 图片",
                    url=image_url,
                    source_url=raw.get("pageURL"),
                    thumbnail_url=raw.get("webformatURL"),
                )
            )
            if len(results) >= self.max_results:
                break
        return results
