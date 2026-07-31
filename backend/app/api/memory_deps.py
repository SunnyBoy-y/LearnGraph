from __future__ import annotations

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.memory_event_models import MemoryScopeContext
from app.services.memory_crypto import MemoryPayloadCipher
from app.services.memory_event_ingestor import event_cipher_from_settings
from app.services.memory_event_store import MemoryEventStore


def memory_scope(context: CurrentWorkspace, *, task_id: str | None = None, conversation_id: str | None = None, node_ids: tuple[str, ...] = (), agent_id: str = "main_agent", allowed_sensitivity: frozenset[str] | None = None) -> MemoryScopeContext:
    return MemoryScopeContext(
        tenant_id=context.principal.tenant_id,
        principal_user_id=context.principal.user_id,
        workspace_id=context.workspace.id,
        task_id=task_id,
        conversation_id=conversation_id,
        node_ids=node_ids,
        agent_id=agent_id,
        allowed_sensitivity=allowed_sensitivity
        or frozenset({"public", "normal", "private"}),
    )


def event_store(db: DB, settings: AppSettings) -> MemoryEventStore:
    return MemoryEventStore(db, event_cipher_from_settings(settings))
