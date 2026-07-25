from __future__ import annotations

from typing import Protocol

from app.domain.schemas.research import SearchResult


class SearchProviderPort(Protocol):
    provider_id: str
    remote_capability: bool

    def search(
        self,
        query: str,
        max_results: int,
        *,
        allowed_domains: set[str] | None = None,
    ) -> list[SearchResult]: ...

    def probe(self) -> dict[str, object]: ...
