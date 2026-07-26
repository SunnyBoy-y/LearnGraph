"""Vendor-neutral normalization for Deep Research results.

Every vendor returns a report plus citations in its own shape.  LearnGraph
persists a single ``evidence_pack`` contract instead, so the research service,
the审阅 UI and the publish path never learn vendor specifics.  Nothing here
performs network I/O — adapters hand raw provider payloads in and get the
canonical pack out.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

# A claim carries the sentence plus the sources supporting it.  Deep-research
# vendors rarely emit claim-level structure, so a report is segmented into
# paragraphs and each paragraph keeps the citations found inside it.
_MAX_CLAIMS = 200
_MAX_SOURCES = 200
_MAX_CLAIM_CHARS = 4_000


def normalize_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url:
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    return url[:4_000]


def source_entry(
    *,
    url: Any,
    title: Any = None,
    snippet: Any = None,
    source_type: str = "web_search",
) -> dict[str, Any] | None:
    normalized = normalize_url(url)
    if normalized is None:
        return None
    host = urlsplit(normalized).hostname or ""
    return {
        "url": normalized,
        "title": (str(title).strip()[:1_000] if isinstance(title, str) and title.strip() else host),
        "snippet": (str(snippet).strip()[:8_000] if isinstance(snippet, str) else ""),
        "source_type": source_type,
        "domain": host.casefold(),
    }


def dedupe_sources(entries: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not entry:
            continue
        url = entry["url"]
        existing = seen.get(url)
        if existing is None:
            seen[url] = entry
            continue
        # Keep the richest variant: a later citation may carry the title or
        # snippet an earlier bare URL lacked.  A title that is only the host
        # is this module's own placeholder, so a real title still wins.
        placeholder = existing.get("title") == existing.get("domain")
        if (placeholder or not existing.get("title")) and entry.get("title") != entry.get(
            "domain"
        ):
            existing["title"] = entry["title"]
        if not existing.get("snippet") and entry.get("snippet"):
            existing["snippet"] = entry["snippet"]
    return list(seen.values())[:_MAX_SOURCES]


def claims_from_report(report: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Segment a cited report into paragraph-level claims.

    Vendors return prose, not claim objects.  Splitting on blank lines keeps
    each claim reviewable next to the URLs it actually cites, without inventing
    structure the provider never asserted.
    """

    by_url = {item["url"]: item for item in sources}
    claims: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", report or ""):
        text = block.strip()
        if not text:
            continue
        cited = [url for url in by_url if url in text]
        claims.append(
            {
                "statement": text[:_MAX_CLAIM_CHARS],
                "source_urls": cited,
                # Provider prose is evidence to review, not a verified fact.
                "confidence": "provider_reported",
            }
        )
        if len(claims) >= _MAX_CLAIMS:
            break
    return claims


def filter_sources_by_domain(
    sources: list[dict[str, Any]],
    allowed_domains: list[str],
) -> list[dict[str, Any]]:
    """Drop sources outside an explicitly approved domain allow-list."""

    permitted = {
        domain.strip().casefold().removeprefix("www.")
        for domain in allowed_domains
        if isinstance(domain, str) and domain.strip()
    }
    if not permitted:
        return sources
    kept: list[dict[str, Any]] = []
    for item in sources:
        host = str(item.get("domain") or "").removeprefix("www.")
        if any(host == domain or host.endswith(f".{domain}") for domain in permitted):
            kept.append(item)
    return kept


def evidence_pack(
    *,
    report: str,
    sources: list[dict[str, Any]],
    model_or_agent_version: str | None = None,
    artifact_ref: str | None = None,
    allowed_domains: list[str] | None = None,
    coverage_gaps: list[Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical pack the research service persists."""

    scoped = filter_sources_by_domain(sources, allowed_domains or [])
    dropped = len(sources) - len(scoped)
    gaps = list(coverage_gaps or [])
    if dropped > 0:
        gaps.append(
            {
                "kind": "out_of_scope_sources_removed",
                "detail": f"{dropped} 条引用不在允许域名范围内，已从证据包移除。",
            }
        )
    return {
        "claims": claims_from_report(report, scoped),
        "sources": scoped,
        "candidate_concepts": [],
        "candidate_relations": [],
        "learning_resources": [],
        "coverage_gaps": gaps,
        "conflicts": [],
        "report_markdown": (report or "")[:200_000],
        "provider_raw_artifact_ref": artifact_ref,
        "model_or_agent_version": model_or_agent_version,
    }
