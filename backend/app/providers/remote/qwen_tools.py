from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from app.domain.schemas.research import SearchResult
from app.providers.remote.fetch import (
    FetchedDocument,
    FetchProviderError,
    FetchProviderTimeout,
)
from app.providers.remote.openai import merge_provider_request_headers
from app.providers.remote.search import (
    SearchProviderError,
    SearchProviderResponseError,
    SearchProviderTimeout,
    dedupe_search_results,
    domain_is_allowed,
    normalize_domain,
)


class QwenResponsesToolProvider:
    """Use an enabled Qwen model as a companion hosted-tool provider."""

    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        api_key: str,
        extra_headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 60.0,
        max_content_chars: int = 2_000_000,
    ) -> None:
        self.provider_id = provider_id
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.extra_headers = dict(extra_headers or {})
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.max_content_chars = max_content_chars
        self.last_usage: dict[str, int] = {}
        self.last_tool_usage: dict[str, int] = {}

    def _response(self, input_text: Any, tools: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            with httpx.Client(
                headers=merge_provider_request_headers(
                    api_key=self.api_key,
                    extra_headers=self.extra_headers,
                ),
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                request_body: dict[str, Any] = {
                    "model": self.model_id,
                    "input": input_text,
                    "tools": tools,
                }
                if (
                    self.model_id.casefold().startswith("qwen3-max")
                    and any(tool.get("type") == "web_extractor" for tool in tools)
                ):
                    request_body["enable_thinking"] = True
                response = client.post(
                    f"{self.base_url}/responses",
                    json=request_body,
                )
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeout("Qwen hosted tool timed out") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError("Qwen hosted tool request failed") from exc
        if not response.is_success:
            raise SearchProviderError(
                f"Qwen hosted tool returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SearchProviderResponseError(
                "Qwen hosted tool returned non-JSON data"
            ) from exc
        if not isinstance(payload, dict):
            raise SearchProviderResponseError(
                "Qwen hosted tool response must be an object"
            )
        usage = payload.get("usage")
        self.last_usage = {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        } if isinstance(usage, dict) else {}
        raw_tools = usage.get("x_tools") if isinstance(usage, dict) else None
        self.last_tool_usage = {}
        if isinstance(raw_tools, dict):
            for name, value in raw_tools.items():
                if isinstance(value, dict):
                    self.last_tool_usage[str(name)] = int(value.get("count") or 0)
        return payload

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str):
            return direct
        chunks: list[str] = []
        for item in payload.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks)

    @staticmethod
    def _web_sources(payload: dict[str, Any]) -> list[dict[str, str]]:
        sources: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in payload.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "web_search_call":
                continue
            action = item.get("action")
            raw_sources = action.get("sources") if isinstance(action, dict) else None
            if not isinstance(raw_sources, list):
                continue
            for raw in raw_sources:
                if not isinstance(raw, dict):
                    continue
                url = raw.get("url")
                if (
                    not isinstance(url, str)
                    or not url.startswith(("http://", "https://"))
                    or url in seen
                ):
                    continue
                seen.add(url)
                sources.append(
                    {
                        "url": url,
                        "title": str(raw.get("title") or url)[:1_000],
                    }
                )
        return sources

    def search(
        self,
        query: str,
        max_results: int,
        *,
        allowed_domains: set[str] | None = None,
    ) -> list[SearchResult]:
        payload = self._response(query, [{"type": "web_search"}])
        permitted_domains = {
            domain
            for value in (allowed_domains or set())
            if (domain := normalize_domain(value))
        }
        answer = self._output_text(payload)
        now = datetime.now(timezone.utc)
        results: list[SearchResult] = []
        for source in self._web_sources(payload):
            if not domain_is_allowed(source["url"], permitted_domains):
                continue
            results.append(
                SearchResult(
                    title=source["title"],
                    url=source["url"][:4_000],
                    snippet=answer[:8_000],
                    source_type="qwen_hosted_web_search",
                    fetched_at=now,
                )
            )
            if len(results) >= max_results:
                break
        return dedupe_search_results(results)

    def fetch(self, url: str) -> FetchedDocument:
        try:
            payload = self._response(
                f"完整提取并返回此网页的主要内容，保留标题与正文：{url}",
                [{"type": "web_search"}, {"type": "web_extractor"}],
            )
        except SearchProviderTimeout as exc:
            raise FetchProviderTimeout("Qwen web extractor timed out") from exc
        except SearchProviderError as exc:
            raise FetchProviderError("Qwen web extractor failed") from exc
        content = self._output_text(payload).strip()
        title = ""
        for item in payload.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "web_extractor_call":
                continue
            output = item.get("output")
            if isinstance(output, str) and output.strip():
                # Some responses expose the extracted page as a JSON string.
                try:
                    decoded = json.loads(output)
                except ValueError:
                    decoded = None
                if isinstance(decoded, dict):
                    content = str(
                        decoded.get("content")
                        or decoded.get("markdown")
                        or content
                    ).strip()
                    title = str(decoded.get("title") or "")[:1_000]
                elif not content:
                    content = output.strip()
        if not content:
            raise FetchProviderError("Qwen web extractor returned empty content")
        if len(content) > self.max_content_chars:
            raise FetchProviderError(
                "Qwen web extractor response exceeds the configured content limit"
            )
        return FetchedDocument(
            source_url=url,
            final_url=url,
            title=title,
            content=content,
            content_type="text/markdown",
            metadata={
                "bridge": "qwen_responses_web_extractor",
                "model_id": self.model_id,
            },
        )

    def image_search(
        self,
        query: str,
        *,
        image_url: str | None = None,
    ) -> list[dict[str, str]]:
        """Expose both Qwen Responses image-search tools for orchestrators."""

        tool_type = "image_search" if image_url else "web_search_image"
        if image_url:
            input_value: Any = [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": query},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }
            ]
        else:
            input_value = query
        payload = self._response(input_value, [{"type": tool_type}])
        expected_type = f"{tool_type}_call"
        for item in payload.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != expected_type:
                continue
            output = item.get("output")
            if not isinstance(output, str):
                continue
            try:
                decoded = json.loads(output)
            except ValueError as exc:
                raise SearchProviderResponseError(
                    "Qwen image search returned invalid JSON"
                ) from exc
            if not isinstance(decoded, list):
                continue
            return [
                {
                    "title": str(raw.get("title") or "")[:1_000],
                    "url": str(raw.get("url") or "")[:4_000],
                }
                for raw in decoded
                if isinstance(raw, dict)
                and str(raw.get("url") or "").startswith(("http://", "https://"))
            ]
        return []

    def probe(self) -> dict[str, object]:
        results = self.search("LearnGraph provider capability check", 1)
        return {
            "capability": "qwen_responses_tools",
            "model_id": self.model_id,
            "result_count": len(results),
        }
