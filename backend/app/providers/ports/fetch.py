from __future__ import annotations

from typing import Protocol


class FetchedDocument(Protocol):
    source_url: str
    final_url: str
    title: str
    content: str
    content_type: str
    metadata: dict


class FetchProviderPort(Protocol):
    provider_id: str
    remote_capability: bool

    def fetch(self, url: str) -> FetchedDocument: ...

    def probe(self) -> dict[str, object]: ...
