"""Explicit OpenAlex/arXiv adapters, not a replacement for hosted web search.

References: https://help.openalex.org/api/ and
https://info.arxiv.org/help/api/user-manual.html . Neither provider silently
falls back to another provider. Remote text and PDF links remain untrusted.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import threading
import time
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.core.errors import AppError
from app.domain.schemas.papers import Paper, PaperReference, PaperSearchRequest, PaperSearchResponse
from app.providers.mcp_network import PinnedMCPTransport

OPENALEX_BASE = "https://api.openalex.org"
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.-]+/\d{7})(?:v[1-9]\d*)?", re.I)
_ARXIV_LOCK = threading.Lock()
_ARXIV_LAST_REQUEST = 0.0
ATOM = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def public_https(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 4000:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password and parsed.port in {None, 443}:
            return value
    except ValueError:
        pass
    return None


def normalized_paper_id(provider: str, value: str) -> str:
    candidate = value.strip()
    if provider == "openalex" and re.fullmatch(r"W[1-9]\d{0,18}", candidate):
        return candidate
    if provider == "arxiv" and ARXIV_ID.fullmatch(candidate):
        return candidate
    raise AppError(422, "invalid_paper_id", "请使用论文检索返回的 OpenAlex 或 arXiv 标识符")


def _abstract(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    words: dict[int, str] = {}
    for word, positions in list(index.items())[:10000]:
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        for position in positions[:1000]:
            if isinstance(position, int) and not isinstance(position, bool) and 0 <= position < 10000:
                words[position] = word[:500]
    return " ".join(words[p] for p in sorted(words))[:20000]


def openalex_paper(row: dict[str, Any]) -> Paper:
    identifier = str(row.get("id") or "").rsplit("/", 1)[-1]
    normalized_paper_id("openalex", identifier)
    abstract = _abstract(row.get("abstract_inverted_index"))
    oa = row.get("open_access") or {}
    raw_locations = row.get("locations") or []
    if not isinstance(oa, dict) or not isinstance(raw_locations, list):
        raise ValueError("invalid open-access metadata")
    locations = [row.get("best_oa_location"), *raw_locations[:500]]
    pdf = next((url for loc in locations if isinstance(loc, dict) and loc.get("is_oa") is True
                if (url := public_https(loc.get("pdf_url")))), None)
    primary = row.get("primary_location") or {}
    if not isinstance(primary, dict):
        raise ValueError("invalid primary location")
    source = primary.get("source") or {}
    authorships = row.get("authorships") or []
    if not isinstance(source, dict) or not isinstance(authorships, list):
        raise ValueError("invalid source or authorships")
    authors = [str(item["author"].get("display_name") or "")[:300]
               for item in authorships[:200] if isinstance(item, dict) and isinstance(item.get("author"), dict)]
    doi = str(row.get("doi") or "")
    doi = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    if not re.fullmatch(r"10\.\d{4,9}/[^\s{}]+", doi):
        doi = ""
    return Paper(provider="openalex", paper_id=identifier, title=str(row.get("title") or row.get("display_name") or identifier)[:1000],
                 authors=[a for a in authors if a], year=row.get("publication_year"), published_at=row.get("publication_date"),
                 venue=str(source.get("display_name") or "")[:500], doi=doi or None,
                 url=f"https://openalex.org/{identifier}", abstract=abstract, pdf_url=pdf,
                 open_access=oa.get("is_oa") is True, publication_type=str(row.get("type") or "")[:100],
                 cited_by_count=row.get("cited_by_count"), fetched_at=datetime.now(timezone.utc),
                 evidence_kind="abstract" if abstract else "metadata")


def arxiv_papers(data: bytes) -> list[Paper]:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise AppError(502, "paper_provider_response_invalid", "arXiv 返回了无效的 XML")
    root = ElementTree.fromstring(data)
    results = []
    for entry in root.findall("a:entry", ATOM)[:100]:
        raw_id = entry.findtext("a:id", "", ATOM)
        if "api/errors" in raw_id:
            raise AppError(502, "paper_provider_rejected", "arXiv 无法处理此检索请求")
        identifier = raw_id.split("/abs/", 1)[-1]
        normalized_paper_id("arxiv", identifier)
        published = entry.findtext("a:published", "", ATOM)
        abstract = " ".join(entry.findtext("a:summary", "", ATOM).split())[:20000]
        doi = entry.findtext("x:doi", "", ATOM).strip() or None
        results.append(Paper(provider="arxiv", paper_id=identifier,
            title=" ".join(entry.findtext("a:title", identifier, ATOM).split())[:1000],
            authors=[a.findtext("a:name", "", ATOM)[:300] for a in entry.findall("a:author", ATOM)[:200]],
            year=int(published[:4]) if published[:4].isdigit() else None, published_at=published or None,
            venue=entry.findtext("x:journal_ref", "", ATOM)[:500], doi=doi,
            url=f"https://arxiv.org/abs/{identifier}", abstract=abstract,
            pdf_url=f"https://arxiv.org/pdf/{identifier}", open_access=True, publication_type="preprint",
            fetched_at=datetime.now(timezone.utc), evidence_kind="abstract" if abstract else "metadata"))
    return results


class PaperSearchProvider:
    """Each request chooses one named source; no AnySearch dependency."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def _get(self, url: str, params: dict[str, Any]) -> bytes:
        global _ARXIV_LAST_REQUEST
        if url.startswith(ARXIV_API) and self.transport is None:
            # arXiv asks callers to leave three seconds between API requests.
            with _ARXIV_LOCK:
                delay = 3.0 - (time.monotonic() - _ARXIV_LAST_REQUEST)
                if delay > 0:
                    time.sleep(delay)
                _ARXIV_LAST_REQUEST = time.monotonic()
        try:
            transport = self.transport or PinnedMCPTransport(urlsplit(url).hostname)
            with httpx.Client(transport=transport, timeout=httpx.Timeout(20, connect=6),
                              follow_redirects=False, trust_env=False,
                              headers={"User-Agent": "LearnGraph-ScholarlySearch/1.0", "Accept": "application/json, application/atom+xml"}) as client:
                with client.stream("GET", url, params=params) as response:
                    if response.status_code == 429:
                        raise AppError(429, "paper_provider_rate_limited", "论文服务请求过多，请稍后重试")
                    if response.status_code in {401, 403}:
                        raise AppError(503, "paper_provider_access_denied", "论文服务拒绝访问；OpenAlex 可通过 OPENALEX_API_KEY 配置密钥")
                    if response.status_code == 404:
                        raise AppError(404, "paper_not_found", "未找到该论文")
                    if not response.is_success:
                        raise AppError(502, "paper_provider_failed", "论文服务暂时不可用", {"status": response.status_code})
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 4_000_000:
                            raise AppError(502, "paper_provider_response_invalid", "论文服务返回内容超过大小限制")
                    return bytes(data)
        except httpx.TimeoutException as exc:
            raise AppError(504, "paper_provider_timeout", "论文服务超时；未切换到其他搜索通道") from exc
        except httpx.HTTPError as exc:
            raise AppError(502, "paper_provider_failed", "论文服务暂时不可用；未切换到其他搜索通道") from exc

    def _openalex(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        import json
        key = os.environ.get("OPENALEX_API_KEY", "").strip()
        if key:
            params = {**params, "api_key": key}
        try:
            value = json.loads(self._get(f"{OPENALEX_BASE}/{path}", params))
            if not isinstance(value, dict):
                raise ValueError("not an object")
            return value
        except (ValueError, TypeError) as exc:
            raise AppError(502, "paper_provider_response_invalid", "OpenAlex 返回了无效数据") from exc

    def search(self, request: PaperSearchRequest) -> PaperSearchResponse:
        try:
            if request.provider == "openalex":
                filters = []
                if request.year_from or request.year_to:
                    filters.append(f"publication_year:{request.year_from or 1500}-{request.year_to or 2200}")
                if request.open_access_only:
                    filters.append("is_oa:true")
                if request.author:
                    authors = self._openalex("authors", {"search": request.author, "per_page": 25}).get("results")
                    if not isinstance(authors, list):
                        raise ValueError("authors results missing")
                    ids = [str(a.get("id") or "").rsplit("/", 1)[-1] for a in authors if isinstance(a, dict)]
                    ids = [a for a in ids if re.fullmatch(r"A\d+", a)]
                    if not ids:
                        return PaperSearchResponse(query=request.query, provider="openalex", results=[])
                    filters.append("authorships.author.id:" + "|".join(ids))
                params: dict[str, Any] = {"search": request.query, "per_page": request.max_results}
                if filters:
                    params["filter"] = ",".join(filters)
                if request.sort == "newest":
                    params["sort"] = "publication_date:desc"
                rows = self._openalex("works", params).get("results")
                if not isinstance(rows, list):
                    raise ValueError("works results missing")
                papers = [openalex_paper(row) for row in rows[:request.max_results] if isinstance(row, dict)]
            else:
                # Quote user text rather than allowing it to alter boolean filters.
                terms = re.findall(r"[\w-]+", request.query, re.UNICODE)[:60]
                if not terms:
                    raise AppError(422, "invalid_paper_query", "请提供有效的论文关键词")
                query = " AND ".join(f'all:"{term}"' for term in terms)
                if request.author:
                    author = " ".join(re.findall(r"[\w-]+", request.author, re.UNICODE))
                    if not author:
                        raise AppError(422, "invalid_paper_author", "请提供有效的作者姓名")
                    query += f' AND au:"{author}"'
                if request.year_from or request.year_to:
                    query += f" AND submittedDate:[{request.year_from or 1500}01010000 TO {request.year_to or 2200}12312359]"
                papers = arxiv_papers(self._get(ARXIV_API, {"search_query": query, "start": 0,
                    "max_results": request.max_results, "sortBy": "submittedDate" if request.sort == "newest" else "relevance", "sortOrder": "descending"}))
            seen = set()
            unique = []
            for paper in papers:
                key = (paper.doi or f"{paper.provider}:{paper.paper_id}").casefold()
                if key not in seen:
                    unique.append(paper)
                    seen.add(key)
            return PaperSearchResponse(query=request.query, provider=request.provider, results=unique)
        except (ValueError, TypeError, KeyError, ElementTree.ParseError) as exc:
            raise AppError(502, "paper_provider_response_invalid", "论文服务返回了无效数据") from exc

    def resolve(self, reference: PaperReference) -> Paper:
        identifier = normalized_paper_id(reference.provider, reference.paper_id)
        try:
            if reference.provider == "openalex":
                paper = openalex_paper(self._openalex(f"works/{identifier}", {}))
            else:
                rows = arxiv_papers(self._get(ARXIV_API, {"id_list": identifier, "max_results": 1}))
                if not rows:
                    raise AppError(404, "paper_not_found", "未找到该论文")
                paper = rows[0]
            # arXiv can resolve an unversioned id to its current version.
            if re.sub(r"v\d+$", "", paper.paper_id) != re.sub(r"v\d+$", "", identifier):
                raise ValueError("provider returned another paper")
            return paper
        except (ValueError, TypeError, KeyError, ElementTree.ParseError) as exc:
            raise AppError(502, "paper_provider_response_invalid", "论文服务返回了无效数据") from exc
