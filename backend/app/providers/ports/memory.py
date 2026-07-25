from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class CanonicalMemory:
    memory_id: str
    revision: int
    title: str
    content: str
    content_hash: str
    namespace: str
    session_id: str | None
    record_kind: str
    zone: str
    state: str
    source: str
    source_ids: tuple[str, ...]
    origin_created_at: datetime
    origin_updated_at: datetime
    policy_version: str = "1.0.0"
    policy_sha256: str = "builtin"


@dataclass(frozen=True, slots=True)
class ProviderBindingResult:
    provider_record_id: str
    provider_entity_kind: str
    provider_entity_value: str
    target_readback_hash: str
    import_event_id: str | None = None
    relative_path: str = ""


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider_id: str
    available: bool
    status: str
    remote_capability: bool
    details: dict[str, Any] = field(default_factory=dict)


class MemoryProviderPort(Protocol):
    provider_id: str
    available: bool
    remote_capability: bool

    def health(self) -> ProviderHealth: ...

    def upsert(
        self,
        memory: CanonicalMemory,
        *,
        provider_record_id: str | None = None,
    ) -> ProviderBindingResult: ...

    def delete(self, provider_record_id: str) -> None: ...

