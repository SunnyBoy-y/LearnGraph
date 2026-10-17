from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1_000)
    max_results: int = Field(default=5, ge=1, le=20)
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    search_route: Literal["external", "local", "auto"] = "auto"


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    source_type: str
    fetched_at: datetime
    # Optional provider-supplied thumbnail. Untrusted display reference only —
    # the backend never proxies/downloads it.
    image_url: str | None = None


class SearchResponse(BaseModel):
    provider_id: str
    remote_capability: bool
    query: str
    results: list[SearchResult]
    notice: str
