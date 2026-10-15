from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.memory_event_models import (
    MemoryContextPackage,
    MemoryEvent,
    MemoryFeedback,
    MemoryPayloadKey,
    MemoryProjectionOutbox,
    MemoryRelation,
    MemoryScopeContext,
    MemorySearchDocument,
)
from app.domain.memory_event_types import MemoryEventType
from app.domain.models import MemoryEmbedding, MemoryRecord, MemoryRevision, utc_now
from app.services.memory_event_store import AppendEvent, MemoryEventStore


@dataclass(frozen=True, slots=True)
class ForgetReport:
    memory_id: str
    destroyed_keys: int
    deleted_search_documents: int
    deleted_embeddings: int
    deleted_relations: int
    invalidated_context_packages: int
    external_projection_jobs: int
    event_id: str


class MemoryCommandService:
    """Event-backed lifecycle commands layered over the compatible legacy view."""

    def __init__(self, db: Session, store: MemoryEventStore) -> None:
        self.db = db
        self.store = store

    def mirror_legacy_record(
        self,
        scope: MemoryScopeContext,
        record: MemoryRecord,
        *,
        content: str,
        operation: str,
        actor_id: str,
        idempotency_key: str | None = None,
    ) -> MemoryEvent:
        event_type = {
            "ADD": MemoryEventType.MEMORY_CREATED,
            "UPDATE": MemoryEventType.MEMORY_CORRECTED,
            "CONFIRM": MemoryEventType.MEMORY_CONFIRMED,
            "SUPERSEDE": MemoryEventType.MEMORY_SUPERSEDED,
            "RETRACT": MemoryEventType.MEMORY_RETRACTED,
            "DELETE": MemoryEventType.MEMORY_DELETE_REQUESTED,
            "RESTORE_DELETE": MemoryEventType.MEMORY_RESTORED,
        }.get(operation, MemoryEventType.MEMORY_CORRECTED)
        result = self.store.append(
            scope,
            aggregate_type="memory_atom",
            aggregate_id=record.id,
            expected_version=None,
            # The stream belongs to the *record's* owner, not to whoever happens
            # to write: a workspace-owned atom (subject NULL) must stay findable
            # for every principal that may update it, otherwise the next writer
            # (for example the system:memory-extraction sweep) collides with the
            # aggregate unique key instead of adopting the stream.
            stream_subject_user_id=record.subject_user_id,
            event=AppendEvent(
                event_type=event_type,
                payload=self._payload(record, content),
                idempotency_key=idempotency_key
                or f"legacy:{record.id}:{record.revision}:{operation}:{uuid4()}",
                producer="api",
                actor_id=actor_id,
                conversation_id=record.conversation_id or record.session_id,
                project_id=record.project_id,
                file_id=record.file_id,
                sensitivity=record.sensitivity,
                metadata={"legacy_revision": record.revision, "dual_write": True},
            ),
            outbox_kinds=("markdown", "mem0", "embedding", "profile", "index"),
        )
        record.head_event_id = result.event.event_id
        record.projection_version = result.event.stream_version
        return result.event

    def feedback(
        self,
        scope: MemoryScopeContext,
        memory_id: str,
        *,
        actor_id: str,
        feedback_type: str,
        payload: dict[str, Any],
        commit: bool = True,
    ) -> MemoryFeedback:
        record = self._require_record(scope, memory_id)
        result = self.store.append(
            scope,
            aggregate_type="memory_atom",
            aggregate_id=memory_id,
            expected_version=None,
            stream_subject_user_id=None if feedback_type == "project_only" else record.subject_user_id,
            event=AppendEvent(
                event_type=MemoryEventType.MEMORY_FEEDBACK_RECORDED,
                payload={"memory_id": memory_id, "feedback_type": feedback_type, "payload": payload},
                idempotency_key=f"feedback:{memory_id}:{actor_id}:{uuid4()}",
                actor_id=actor_id,
            ),
            outbox_kinds=("profile", "index"),
        )
        row = MemoryFeedback(
            workspace_id=scope.workspace_id,
            memory_id=memory_id,
            feedback_type=feedback_type,
            payload_json=payload,
            actor_id=actor_id,
            applied_event_id=result.event.event_id,
        )
        self.db.add(row)
        if feedback_type == "suppress_auto_recall":
            record.auto_recall_suppressed = True
        elif feedback_type == "deny_child":
            record.child_agent_denied = True
        elif feedback_type == "project_only":
            record.audience_type = "workspace"
            record.subject_user_id = None
            record.memory_layer = "L4"
        elif feedback_type in {"stale", "wrong"}:
            record.lifecycle_status = "disputed" if feedback_type == "wrong" else "stale"
        from app.services.memory_projector import MemoryProjector
        MemoryProjector(self.db).apply(result.event, {
            "memory_id": memory_id, "feedback_type": feedback_type,
        })
        record.head_event_id = result.event.event_id
        record.projection_version = result.event.stream_version
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return row

    def retract(
        self, scope: MemoryScopeContext, memory_id: str, *, actor_id: str, reason: str
    ) -> MemoryRecord:
        record = self._require_record(scope, memory_id)
        revision = self._active_revision(scope.workspace_id, memory_id)
        result = self.store.append(
            scope,
            aggregate_type="memory_atom",
            aggregate_id=memory_id,
            expected_version=None,
            event=AppendEvent(
                event_type=MemoryEventType.MEMORY_RETRACTED,
                payload={"memory_id": memory_id, "reason": reason},
                idempotency_key=f"retract:{memory_id}:{record.revision}:{uuid4()}",
                actor_id=actor_id,
            ),
            outbox_kinds=("markdown", "mem0", "embedding", "profile", "index"),
        )
        record.lifecycle_status = "retracted"
        record.ledger_status = "retracted"
        record.auto_recall_suppressed = True
        record.head_event_id = result.event.event_id
        if revision:
            revision.is_active = False
        self._clear_local_projections(memory_id)
        self.db.commit()
        return record

    def forget(
        self,
        scope: MemoryScopeContext,
        memory_id: str,
        *,
        actor_id: str,
        confirmation: str,
        reason: str,
        commit: bool = True,
    ) -> ForgetReport:
        record = self._require_record(scope, memory_id)
        if confirmation.strip() not in {record.title.strip(), "永久忘记", "FORGET"}:
            raise AppError(422, "forget_confirmation_invalid", "Forget confirmation does not match")
        stream = self.store.events.stream_for_aggregate(scope, "memory_atom", memory_id)
        destroyed_keys = (
            self.store.destroy_stream_payloads(
                scope, stream.id, actor_id=actor_id, reason=reason
            )
            if stream is not None
            else 0
        )
        result = self.store.append(
            scope,
            aggregate_type="memory_atom",
            aggregate_id=memory_id,
            expected_version=stream.current_version if stream is not None else 0,
            stream_subject_user_id=record.subject_user_id,
            event=AppendEvent(
                event_type=MemoryEventType.MEMORY_FORGOTTEN,
                payload={"target_hash": record.content_hash, "scope": "all_projections"},
                idempotency_key=f"forget:{memory_id}:{uuid4()}",
                actor_id=actor_id,
                sensitivity="normal",
                metadata={"content_excluded": True},
            ),
            outbox_kinds=("markdown", "mem0", "embedding", "profile", "index"),
        )
        # Destroy the just-written action payload key too; the envelope metadata
        # retains the non-sensitive audit action and target hash.
        action_key = self.db.get(MemoryPayloadKey, result.event.payload_key_id)
        if action_key is not None:
            action_key.wrapped_dek = None
            action_key.status = "destroyed"
            action_key.destroyed_at = utc_now()
            action_key.destroyed_by = actor_id
            action_key.reason = "forget_action_envelope_only"
            destroyed_keys += 1
        # Legacy records may not have a stream until the forget command.
        # Keep its tombstone so a delayed projection cannot resurrect content.
        if stream is None:
            self.store.destroy_stream_payloads(
                scope, result.event.stream_id, actor_id=actor_id, reason=reason
            )
        documents = self.db.scalars(
            select(MemorySearchDocument).where(
                MemorySearchDocument.tenant_id == scope.tenant_id,
                MemorySearchDocument.workspace_id == scope.workspace_id,
                MemorySearchDocument.target_type == "memory",
                MemorySearchDocument.target_id == memory_id,
            )
        ).all()
        deleted_documents = len(documents)
        for document in documents:
            if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
                self.db.execute(text("DELETE FROM memory_search_fts WHERE document_id = :id"), {"id": document.id})
            self.db.delete(document)
        deleted_embeddings = self.db.execute(
            delete(MemoryEmbedding).where(
                MemoryEmbedding.workspace_id == scope.workspace_id,
                MemoryEmbedding.memory_id == memory_id,
            )
        ).rowcount or 0
        deleted_relations = self.db.execute(
            delete(MemoryRelation).where(
                MemoryRelation.tenant_id == scope.tenant_id,
                (MemoryRelation.from_id == memory_id) | (MemoryRelation.to_id == memory_id),
            )
        ).rowcount or 0
        packages = self.db.scalars(
            select(MemoryContextPackage).where(
                MemoryContextPackage.workspace_id == scope.workspace_id
            )
        ).all()
        invalidated_packages = 0
        for package in packages:
            if memory_id in package.selected_ids_json or memory_id in package.candidate_ids_json:
                package.outcome_status = "invalidated_by_forget"
                package.selected_ids_json = [item for item in package.selected_ids_json if item != memory_id]
                package.candidate_ids_json = [item for item in package.candidate_ids_json if item != memory_id]
                invalidated_packages += 1
        from app.domain.models import MemoryJournalEntry, MemoryDeletionRecovery, MemoryProfileSnapshot, MemoryDraft
        now = utc_now()
        for journal in self.db.scalars(select(MemoryJournalEntry).where(
            MemoryJournalEntry.workspace_id == scope.workspace_id, MemoryJournalEntry.memory_id == memory_id
        )):
            journal.payload = {}
            journal.content_hash = ""
            journal.tombstone = True
            journal.recoverable_until = None
            journal.content_scrubbed_at = now
        for recovery in self.db.scalars(select(MemoryDeletionRecovery).where(
            MemoryDeletionRecovery.workspace_id == scope.workspace_id, MemoryDeletionRecovery.memory_id == memory_id
        )):
            from app.services.memory_vault import MemoryRecoveryVault
            from app.core.config import get_settings
            MemoryRecoveryVault(get_settings().memory_root, scope.workspace_id).destroy(memory_id)
            recovery.encrypted_payload = None
            recovery.recoverable_until = now
            recovery.destroyed_at = now
        for feedback in self.db.scalars(select(MemoryFeedback).where(
            MemoryFeedback.workspace_id == scope.workspace_id, MemoryFeedback.memory_id == memory_id
        )):
            feedback.payload_json = {}
        for draft in self.db.scalars(select(MemoryDraft).where(
            MemoryDraft.workspace_id == scope.workspace_id,
            (MemoryDraft.target_memory_id == memory_id) | (MemoryDraft.result_memory_id == memory_id),
        )):
            draft.title = "[forgotten]"
            draft.content = ""
            draft.structured_payload = {}
            draft.source_refs = []
            draft.status = "REJECTED"
            draft.rejection_reason = "source_forgotten"
        for snapshot in self.db.scalars(select(MemoryProfileSnapshot).where(
            MemoryProfileSnapshot.workspace_id == scope.workspace_id
        )):
            refs = set(snapshot.source_atom_ids or []) | {i for ids in (snapshot.claim_atom_map or {}).values() for i in ids}
            if memory_id in refs:
                snapshot.markdown = ""
                snapshot.structured_sections = []
                snapshot.source_atom_ids = []
                snapshot.claim_atom_map = {}
                snapshot.source_fingerprint = ""
                snapshot.status = "stale"
                snapshot.stale_reason = "source_forgotten"
        record.title = "[forgotten]"
        record.content_hash = ""
        record.relative_path = ""
        record.state = "destroyed"
        record.lifecycle_status = "forgotten"
        record.ledger_status = "forgotten"
        record.source_ids = []
        record.structured_payload = {}
        record.evidence_ids = []
        record.provider_binding_id = None
        record.auto_recall_suppressed = True
        record.content_destroyed_at = utc_now()
        record.head_event_id = result.event.event_id
        for revision in self.db.scalars(
            select(MemoryRevision).where(
                MemoryRevision.workspace_id == scope.workspace_id,
                MemoryRevision.memory_id == memory_id,
            )
        ).all():
            revision.title = "[forgotten]"
            revision.content = None
            revision.content_hash = ""
            revision.source_ids = []
            revision.is_active = False
        jobs = self.db.scalars(
            select(MemoryProjectionOutbox).where(
                MemoryProjectionOutbox.event_id == result.event.event_id
            )
        ).all()
        record.deleted_at = record.deleted_at or utc_now()
        record.recoverable_until = None
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        return ForgetReport(
            memory_id,
            destroyed_keys,
            deleted_documents,
            int(deleted_embeddings),
            int(deleted_relations),
            invalidated_packages,
            len(jobs),
            result.event.event_id,
        )

    def _require_record(self, scope: MemoryScopeContext, memory_id: str) -> MemoryRecord:
        record = self.db.scalar(
            select(MemoryRecord).where(
                MemoryRecord.id == memory_id,
                MemoryRecord.workspace_id == scope.workspace_id,
                MemoryRecord.tenant_id == scope.tenant_id,
                (MemoryRecord.subject_user_id == scope.principal_user_id)
                | (MemoryRecord.subject_user_id.is_(None)),
            )
        )
        if record is None:
            raise AppError(404, "memory_not_found", "Memory was not found")
        return record

    def _active_revision(self, workspace_id: str, memory_id: str) -> MemoryRevision | None:
        return self.db.scalar(
            select(MemoryRevision)
            .where(
                MemoryRevision.workspace_id == workspace_id,
                MemoryRevision.memory_id == memory_id,
                MemoryRevision.is_active.is_(True),
            )
            .order_by(MemoryRevision.revision.desc())
        )

    def _clear_local_projections(self, memory_id: str) -> None:
        documents = self.db.scalars(
            select(MemorySearchDocument).where(MemorySearchDocument.target_id == memory_id)
        ).all()
        for document in documents:
            if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
                self.db.execute(text("DELETE FROM memory_search_fts WHERE document_id = :id"), {"id": document.id})
            self.db.delete(document)
        self.db.execute(
            delete(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory_id)
        )

    @staticmethod
    def _payload(record: MemoryRecord, content: str) -> dict[str, Any]:
        return {
            "memory_id": record.id,
            "subject_user_id": record.subject_user_id,
            "state": record.state,
            "zone": record.zone,
            "atom_schema_version": record.atom_schema_version,
            "ledger_status": record.ledger_status,
            "temporal_status": record.temporal_status,
            "summary_eligibility": record.summary_eligibility,
            "supersedes_id": record.supersedes_id,
            "superseded_by_id": record.superseded_by_id,
            "auto_recall_suppressed": record.auto_recall_suppressed,
            "child_agent_denied": record.child_agent_denied,
            "valid_from": record.valid_from.isoformat() if record.valid_from else None,
            "valid_until": record.valid_until.isoformat() if record.valid_until else None,
            "title": record.title,
            "content": content,
            "revision": record.revision,
            "record_kind": record.record_kind,
            "canonical_key": record.canonical_key,
            "audience_type": record.audience_type,
            "memory_layer": record.memory_layer,
            "assertion_type": record.assertion_type,
            "sensitivity": record.sensitivity,
            "status": record.lifecycle_status,
            "importance": record.importance,
            "confidence": record.confidence,
            "goal_id": record.goal_id,
            "node_id": record.node_id,
            "conversation_id": record.conversation_id or record.session_id,
            "project_id": record.project_id,
            "file_id": record.file_id,
        }
