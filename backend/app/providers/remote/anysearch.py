from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from app.domain.schemas.research import SearchResult
from app.providers.remote.search import (
    SearchProviderError,
    SearchProviderResponseError,
    SearchProviderTimeout,
    dedupe_search_results,
    domain_is_allowed,
    normalize_domain,
)


_RESULT_HEADING = re.compile(r"(?m)^###\s+\d+\.\s+(?P<title>[^\r\n]+?)\s*$")
_URL_LINE = re.compile(
    r"(?im)^\s*[-*]\s*(?:\*\*)?URL(?:\*\*)?\s*:\s*(?P<value>\S+)"
)
_MARKDOWN_LINK = re.compile(r"^\[[^\]]*\]\((?P<url>https?://[^\s)]+)\)$")
ANYSEARCH_MCP_URL = "https://api.anysearch.com/mcp"


class AnySearchSearchProvider:
    """AnySearch's documented MCP-compatible JSON-RPC search adapter.

    The vendor exposes a tool endpoint, but LearnGraph keeps it behind the
    existing SearchProvider port.  The browser never receives the credential
    or communicates with AnySearch directly.  Advanced vendor tools such as
    ``extract`` intentionally remain in the FetchProvider boundary instead of
    bypassing its authorization and SSRF protections.
    """

    remote_capability = True
    provider_type = "anysearch"

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("AnySearch requires a base URL")
        if not api_key or not api_key.strip():
            raise ValueError("AnySearch requires an encrypted API key")
        self.provider_id = provider_id
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": uuid4().hex,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        try:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Anysearch-Client": "learngraph/0.1",
            }
            headers["Authorization"] = f"Bearer {self.api_key}"
            with httpx.Client(
                headers=headers,
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.post(self.base_url, json=payload)
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeout("AnySearch timed out") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError("AnySearch request failed") from exc
        if not response.is_success:
            raise SearchProviderError(f"AnySearch returned HTTP {response.status_code}")
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise SearchProviderResponseError("AnySearch returned non-JSON data") from exc
        if not isinstance(response_payload, dict):
            raise SearchProviderResponseError("AnySearch response must be an object")
        if response_payload.get("error") is not None:
            # Never surface an upstream error verbatim: it may contain request
            # metadata that should not cross the provider boundary.
            raise SearchProviderError("AnySearch rejected the tool call")
        result = response_payload.get("result")
        if not isinstance(result, dict):
            raise SearchProviderResponseError("AnySearch response has no tool result")
        return result

    @staticmethod
    def _permitted_domains(allowed_domains: set[str] | None) -> set[str]:
        return {
            domain
            for value in (allowed_domains or set())
            if (domain := normalize_domain(value))
        }

    @staticmethod
    def _clean_url(value: str) -> str | None:
        candidate = value.strip()
        markdown_link = _MARKDOWN_LINK.match(candidate)
        if markdown_link:
            candidate = markdown_link.group("url")
        candidate = candidate.rstrip(".,;\"'")
        return candidate if candidate.startswith(("https://", "http://")) else None

    @staticmethod
    def _snippet(block: str) -> str:
        lines = [
            line.strip()
            for line in block.splitlines()
            if not _URL_LINE.match(line)
        ]
        return "\n".join(line for line in lines if line).strip()[:8_000]

    @classmethod
    def _markdown_results(
        cls,
        text: str,
        *,
        max_results: int,
        permitted_domains: set[str],
        fetched_at: datetime,
    ) -> list[SearchResult]:
        matches = list(_RESULT_HEADING.finditer(text))
        results: list[SearchResult] = []
        for index, match in enumerate(matches):
            block_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            block = text[match.end() : block_end]
            url_match = _URL_LINE.search(block)
            if url_match is None:
                continue
            url = cls._clean_url(url_match.group("value"))
            if url is None or not domain_is_allowed(url, permitted_domains):
                continue
            title = match.group("title").strip().strip("#").strip()
            if not title:
                continue
            results.append(
                SearchResult(
                    title=title[:1_000],
                    url=url[:4_000],
                    snippet=cls._snippet(block),
                    source_type="anysearch_web_search",
                    fetched_at=fetched_at,
                )
            )
            if len(results) >= max_results:
                break
        return results

    @classmethod
    def _structured_results(
        cls,
        structured: object,
        *,
        max_results: int,
        permitted_domains: set[str],
        fetched_at: datetime,
    ) -> list[SearchResult] | None:
        """Accept future structured MCP output without assuming it exists today."""

        if not isinstance(structured, dict):
            return None
        raw_results = structured.get("results")
        if not isinstance(raw_results, list):
            return None
        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("name")
            raw_url = item.get("url") or item.get("link")
            if not isinstance(title, str) or not isinstance(raw_url, str):
                continue
            url = cls._clean_url(raw_url)
            if not title.strip() or url is None or not domain_is_allowed(url, permitted_domains):
                continue
            snippet = (
                item.get("snippet")
                or item.get("description")
                or item.get("content")
                or ""
            )
            results.append(
                SearchResult(
                    title=title.strip()[:1_000],
                    url=url[:4_000],
                    snippet=str(snippet).strip()[:8_000],
                    source_type="anysearch_web_search",
                    fetched_at=fetched_at,
                )
            )
            if len(results) >= max_results:
                break
        return results

    @classmethod
    def _result_text(cls, result: dict[str, Any]) -> str:
        content = result.get("content")
        if not isinstance(content, list):
            raise SearchProviderResponseError("AnySearch tool result has no content array")
        texts = [
            item.get("text")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if not texts:
            raise SearchProviderResponseError("AnySearch tool result has no text content")
        return "\n\n".join(text.strip() for text in texts if text.strip())

    def search(
        self,
        query: str,
        max_results: int,
        *,
        allowed_domains: set[str] | None = None,
    ) -> list[SearchResult]:
        # AnySearch documents a maximum of ten results.  The public
        # LearnGraph endpoint permits up to twenty for other Providers, so the
        # adapter sends the largest supported bounded request rather than an
        # invalid vendor payload.
        requested_results = min(max(1, max_results), 10)
        result = self._call_tool(
            "search",
            {"query": query, "max_results": requested_results},
        )
        permitted_domains = self._permitted_domains(allowed_domains)
        fetched_at = datetime.now(timezone.utc)

        structured_results = self._structured_results(
            result.get("structuredContent"),
            max_results=requested_results,
            permitted_domains=permitted_domains,
            fetched_at=fetched_at,
        )
        if structured_results is not None:
            return dedupe_search_results(structured_results)

        text = self._result_text(result)
        # A future AnySearch server can return a JSON document inside its text
        # content.  Parse that form only when it is valid JSON; ordinary
        # Markdown remains the documented response format.
        try:
            inline_structured = json.loads(text)
        except ValueError:
            inline_structured = None
        parsed_inline = self._structured_results(
            inline_structured,
            max_results=requested_results,
            permitted_domains=permitted_domains,
            fetched_at=fetched_at,
        )
        if parsed_inline is not None:
            return dedupe_search_results(parsed_inline)
        return dedupe_search_results(
            self._markdown_results(
                text,
                max_results=requested_results,
                permitted_domains=permitted_domains,
                fetched_at=fetched_at,
            )
        )

    def probe(self) -> dict[str, object]:
        results = self.search("LearnGraph provider capability check", 1)
        return {
            "capability": "search",
            "provider_type": self.provider_type,
            "protocol": "mcp_json_rpc",
            "tool": "search",
            "result_count": len(results),
        }
