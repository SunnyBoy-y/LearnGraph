from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings
from app.domain.memory_event_models import MemoryScopeContext
from app.domain.schemas.memory_v2 import MemoryEventAppendRequest
from app.services.memory_crypto import MemoryPayloadCipher, development_event_secret
from app.services.memory_event_store import AppendEvent, AppendResult, MemoryEventStore
from app.services.memory_sensitive_filter import SensitiveDataFilter


@dataclass(frozen=True, slots=True)
class EventActor:
    actor_type: Literal["user", "system", "agent", "device"]
    actor_id: str


def event_cipher_from_settings(settings: Settings) -> MemoryPayloadCipher:
    secret = settings.memory_event_master_key or settings.master_key
    if not secret:
        if settings.env.casefold() not in {"development", "dev", "test", "local"}:
            raise RuntimeError("LEARNGRAPH_MEMORY_EVENT_MASTER_KEY is required")
        secret = development_event_secret()
    return MemoryPayloadCipher(secret, settings.master_key_version)


class MemoryEventIngestor:
    def __init__(self, store: MemoryEventStore) -> None:
        self.store = store
        self.sensitive_filter = SensitiveDataFilter()

    def ingest(
        self,
        scope: MemoryScopeContext,
        actor: EventActor,
        request: MemoryEventAppendRequest,
        *,
        trusted_producer: bool = False,
    ) -> AppendResult:
        if request.producer in {"scheduler", "migration"} and not trusted_producer:
            raise PermissionError("event producer is restricted")
        self.sensitive_filter.require_safe(request.payload)
        return self.store.append(
            scope,
            aggregate_type=request.aggregate_type,
            aggregate_id=request.aggregate_id,
            expected_version=request.expected_stream_version,
            event=AppendEvent(
                event_type=request.event_type,
                payload=request.payload,
                idempotency_key=request.idempotency_key,
                producer=request.producer,
                event_schema_version=request.event_schema_version,
                actor_type=actor.actor_type,
                actor_id=actor.actor_id,
                correlation_id=request.correlation_id,
                causation_id=request.causation_id,
                occurred_at=request.occurred_at,
                sensitivity=request.sensitivity,
                metadata=request.metadata,
                conversation_id=request.conversation_id,
                project_id=request.project_id,
                file_id=request.file_id,
                knowledge_node_id=request.knowledge_node_id,
            ),
            outbox_kinds=self._outbox_kinds(request.event_type),
        )

    @staticmethod
    def _outbox_kinds(event_type: str) -> tuple[str, ...]:
        if event_type.startswith("memory."):
            return ("markdown", "mem0", "embedding", "profile", "index")
        if event_type.startswith("artifact."):
            return ("index", "embedding")
        if event_type.startswith("episode."):
            return ("index",)
        return ()
