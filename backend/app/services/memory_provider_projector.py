from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.memory_event_models import MemoryEvent, MemoryProjectionOutbox
from app.domain.models import MemoryProviderBinding, MemoryRecord, MemoryRevision, Workspace, utc_now
from app.providers.factory import memory_provider_for_workspace
from app.providers.local.memory import LocalWorkspaceMemoryProvider
from app.providers.ports.memory import CanonicalMemory

logger = logging.getLogger(__name__)


class MemoryProviderProjector:
    """Projects committed DB state outward; provider failures remain in the outbox."""

    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def handle(self, item: MemoryProjectionOutbox) -> None:
        if item.projection_kind not in {"markdown", "mem0"}:
            return
        workspace = self.db.get(Workspace, item.workspace_id)
        if workspace is None:
            raise LookupError("outbox workspace no longer exists")
        record = self.db.scalar(
            select(MemoryRecord).where(
                MemoryRecord.id == item.aggregate_id,
                MemoryRecord.workspace_id == workspace.id,
            )
        )
        event = self.db.scalar(
            select(MemoryEvent).where(MemoryEvent.event_id == item.event_id)
        )
        if event is None:
            raise LookupError("outbox source event no longer exists")
        actor_id = event.subject_user_id or workspace.owner_user_id
        provider = memory_provider_for_workspace(
            self.db,
            workspace,
            actor_id,
            self.settings,
        )
        if item.projection_kind == "markdown" and provider.provider_id != "local_workspace_markdown":
            provider = LocalWorkspaceMemoryProvider(self.settings.memory_root, workspace.id)
        if item.projection_kind == "mem0" and not provider.remote_capability:
            return
        if record is None or record.lifecycle_status in {
            "deleted",
            "retracted",
            "forgotten",
            "superseded",
        }:
            self._delete_projection(provider, workspace.id, item.aggregate_id)
            return
        revision = self.db.scalar(
            select(MemoryRevision)
            .where(
                MemoryRevision.workspace_id == workspace.id,
                MemoryRevision.memory_id == record.id,
                MemoryRevision.is_active.is_(True),
            )
            .order_by(MemoryRevision.revision.desc())
        )
        if revision is None or revision.content is None:
            self._delete_projection(provider, workspace.id, item.aggregate_id)
            return
        binding = self.db.scalar(
            select(MemoryProviderBinding)
            .where(
                MemoryProviderBinding.workspace_id == workspace.id,
                MemoryProviderBinding.memory_id == record.id,
                MemoryProviderBinding.provider_instance_id == provider.provider_id,
            )
            .order_by(MemoryProviderBinding.revision.desc())
        )
        result = provider.upsert(
            CanonicalMemory(
                memory_id=record.id,
                revision=record.revision,
                title=record.title,
                content=revision.content,
                content_hash=record.content_hash,
                namespace=record.namespace,
                session_id=record.session_id,
                record_kind=record.record_kind,
                zone=record.zone,
                state=record.state,
                source=record.source,
                source_ids=tuple(record.source_ids or []),
                origin_created_at=record.created_at,
                origin_updated_at=record.updated_at,
            ),
            provider_record_id=binding.provider_record_id if binding else None,
        )
        if binding is None:
            self.db.add(
                MemoryProviderBinding(
                    workspace_id=workspace.id,
                    provider_instance_id=provider.provider_id,
                    memory_id=record.id,
                    revision=record.revision,
                    provider_record_id=result.provider_record_id,
                    provider_entity_kind=result.provider_entity_kind,
                    provider_entity_value=result.provider_entity_value,
                    source_content_hash=record.content_hash,
                    target_readback_hash=result.target_readback_hash,
                    import_event_id=result.import_event_id,
                    binding_status="verified",
                    verified_at=utc_now(),
                )
            )
        else:
            binding.revision = record.revision
            binding.provider_record_id = result.provider_record_id
            binding.source_content_hash = record.content_hash
            binding.target_readback_hash = result.target_readback_hash
            binding.binding_status = "verified"
            binding.verified_at = utc_now()
        self.db.flush()

    def _delete_projection(self, provider, workspace_id: str, memory_id: str) -> None:
        bindings = self.db.scalars(
            select(MemoryProviderBinding).where(
                MemoryProviderBinding.workspace_id == workspace_id,
                MemoryProviderBinding.memory_id == memory_id,
                MemoryProviderBinding.provider_instance_id == provider.provider_id,
                MemoryProviderBinding.binding_status == "verified",
            )
        ).all()
        for binding in bindings:
            provider.delete(binding.provider_record_id)
            binding.binding_status = "deleted"
        self.db.flush()
