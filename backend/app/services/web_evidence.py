"""Shared, provider-neutral contracts for discovery versus actually read pages."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


def public_source_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 8000:
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        _ = parsed.port
    except ValueError:
        return None
    return parsed.geturl()


def normalize_web_evidence(
    rows: list[Any], *, provider_id: str, provider_label: str,
    kind: str = "search", url_field: str = "url", title_field: str = "title",
    snippet_field: str = "snippet", content_field: str = "content",
    allowed_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Normalize explicit result rows; never infer full-page reads from snippets."""
    from app.providers.remote.search import domain_is_allowed

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        url = public_source_url(row.get(url_field))
        if not url or url in seen or (allowed_domains and not domain_is_allowed(url, set(allowed_domains))):
            continue
        seen.add(url)
        body = row.get(content_field)
        is_page = kind in {"fetch", "browser"} and isinstance(body, str) and bool(body.strip())
        snippet = row.get(snippet_field)
        result.append({
            "index": len(result) + 1,
            "url": url,
            "title": str(row.get(title_field) or url)[:1000],
            "snippet": str(snippet or "")[:4000],
            "content": body[:48000] if is_page else "",
            "evidence_kind": "page_text" if is_page else "search_snippet",
            "page_read": is_page,
            "provider_id": provider_id,
            "provider_label": provider_label,
            "source_type": "browser_page" if kind == "browser" else ("web_page" if is_page else "web_search"),
        })
    return result


def result_rows(value: Any, path: str = "results") -> list[Any]:
    """Read a bounded dot path in a declared MCP structured result."""
    current = value
    if path:
        for key in path.split(".")[:8]:
            if not isinstance(current, dict):
                return []
            current = current.get(key)
    if isinstance(current, list):
        return current[:100]
    return [current] if isinstance(current, dict) else []


def merge_citation_sources(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Stable URL indices across native, MCP and browser tool rounds."""
    merged: list[dict] = []
    positions: dict[str, int] = {}
    for row in [*existing, *incoming]:
        if not isinstance(row, dict):
            continue
        url = public_source_url(row.get("url"))
        if not url:
            continue
        if url in positions:
            position = positions[url]
            if row.get("page_read") is True:
                merged[position] = {**merged[position], **row, "index": position + 1}
            continue
        positions[url] = len(merged)
        merged.append({**row, "index": len(merged) + 1})
    return merged


def align_tool_sources(content: str, sources: list[dict], existing: list[dict]):
    import json

    merged = merge_citation_sources(existing, sources)
    by_url = {row["url"]: row for row in merged}
    aligned = [by_url[row["url"]] for row in sources if isinstance(row, dict) and row.get("url") in by_url]
    if not aligned:
        return content, aligned
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return content, aligned
    if isinstance(payload, dict):
        for row in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
            if isinstance(row, dict) and row.get("url") in by_url:
                row["index"] = by_url[row["url"]]["index"]
        if payload.get("url") in by_url:
            payload["index"] = by_url[payload["url"]]["index"]
        payload["citation_sources"] = [{key: row[key] for key in ("index", "url", "title", "page_read", "evidence_kind") if key in row} for row in aligned]
        payload["citation_notice"] = "Cite these host-assigned indices as （网页引用：N）. A search snippet does not establish that the page was read."
        content = json.dumps(payload, ensure_ascii=False)
    return content, aligned


def provider_label(provider: Any) -> str:
    """Use transport identity, not the model's self-reported brand."""
    if provider is None:
        return "未配置"
    provider_type = str(getattr(provider, "provider_type", "") or "")
    class_name = type(provider).__name__.casefold()
    if provider_type == "codex_chatgpt" or "codex" in class_name:
        return "OpenAI"
    if provider_type == "qwen" or "qwen" in class_name:
        return "百炼"
    if provider_type == "openai_responses":
        host = urlsplit(str(getattr(provider, "base_url", "") or "")).hostname
        return "OpenAI" if host == "api.openai.com" else "Responses 服务"
    for token, label in (("anysearch", "AnySearch"), ("tavily", "Tavily"),
                         ("searx", "SearXNG"), ("firecrawl", "Firecrawl"),
                         ("crawl4ai", "Crawl4AI"), ("ollama", "Ollama")):
        if token in provider_type.casefold() or token in class_name:
            return label
    return str(getattr(provider, "display_name", "") or provider_type or "网页服务")[:160]
