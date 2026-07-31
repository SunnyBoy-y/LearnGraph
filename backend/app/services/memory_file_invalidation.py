from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.memory_event_models import MemoryScopeContext
from app.domain.memory_event_types import MemoryEventType
from app.domain.models import DocumentRevision, FileRecord, FileTextChunk, MemoryEvidence, utc_now
from app.services.memory_event_store import AppendEvent, MemoryEventStore


@dataclass(frozen=True, slots=True)
class RevisionActivationReport:
    activated_revision_id: str
    stale_revision_ids: tuple[str, ...]
    stale_chunk_count: int
    invalidated_memory_evidence: int


class MemoryFileInvalidationService:
    """Atomically switches the active revision and invalidates old derived evidence.

    ``store`` is optional: when provided, activation/invalidation also appends
    ``artifact.revision_activated`` / ``artifact.revision_invalidated`` events in
    the same transaction. Callers inside a larger UoW must not ``commit()`` here;
    both paths only ``flush()`` so the caller's outer transaction stays intact.
    """

    def __init__(self, db: Session, store: MemoryEventStore | None = None) -> None:
        self.db = db
        self.store = store

    def activate_revision(
        self,
        scope: MemoryScopeContext,
        *,
        file_id: str,
        revision_id: str,
        actor_id: str,
    ) -> RevisionActivationReport:
        file = self.db.scalar(
            select(FileRecord).where(
                FileRecord.id == file_id, FileRecord.workspace_id == scope.workspace_id
            )
        )
        revision = self.db.scalar(
            select(DocumentRevision).where(
                DocumentRevision.id == revision_id,
                DocumentRevision.file_id == file_id,
                DocumentRevision.workspace_id == scope.workspace_id,
            )
        )
        if file is None or revision is None:
            raise LookupError("file revision not found in scope")
        old_revisions = self.db.scalars(
            select(DocumentRevision).where(
                DocumentRevision.file_id == file_id,
                DocumentRevision.workspace_id == scope.workspace_id,
                DocumentRevision.id != revision_id,
                DocumentRevision.lifecycle_status == "active",
            )
        ).all()
        stale_ids = tuple(item.id for item in old_revisions)
        stale_chunks = 0
        for old in old_revisions:
            old.lifecycle_status = "stale"
            old.index_status = "stale"
            chunks = self.db.scalars(
                select(FileTextChunk).where(FileTextChunk.document_revision_id == old.id)
            ).all()
            for chunk in chunks:
                chunk.lifecycle_status = "stale"
                stale_chunks += 1
                if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
                    self.db.execute(
                        text("DELETE FROM document_chunks_fts WHERE chunk_id = :id"),
                        {"id": chunk.id},
                    )
        revision.lifecycle_status = "active"
        revision.activated_at = utc_now()
        revision.index_status = "ready"
        revision.supersedes_revision_id = file.active_revision_id
        file.active_revision_id = revision.id
        file.logical_version += 1
        file.updated_by = actor_id
        file.lifecycle_status = "active"
        evidence_rows = self.db.scalars(
            select(MemoryEvidence).where(
                MemoryEvidence.workspace_id == scope.workspace_id,
                MemoryEvidence.file_id == file_id,
                MemoryEvidence.deleted_at.is_(None),
            )
        ).all()
        invalidated = 0
        stale_set = set(stale_ids)
        for evidence in evidence_rows:
            refs = list(evidence.derived_from or [])
            if any(str(ref.get("source_version_id") or "") in stale_set for ref in refs):
                evidence.deleted_at = utc_now()
                evidence.eligibility_reason = "source_revision_stale"
                invalidated += 1
        if self.store is not None:
            expected = self._artifact_stream_version(scope, file_id)
            self.store.append(
                scope,
                aggregate_type="artifact",
                aggregate_id=file_id,
                expected_version=expected,
                event=AppendEvent(
                    event_type=MemoryEventType.ARTIFACT_REVISION_ACTIVATED,
                    payload={
                        "file_id": file_id,
                        "revision_id": revision_id,
                        "supersedes_revision_id": revision.supersedes_revision_id,
                        "stale_revision_ids": list(stale_ids),
                        "invalidated_memory_evidence": invalidated,
                        "actor_id": actor_id,
                    },
                    idempotency_key=f"rev_act:{file_id}:{revision_id}",
                    actor_id=actor_id,
                    file_id=file_id,
                ),
                outbox_kinds=("index",),
            )
        self.db.flush()
        return RevisionActivationReport(revision.id, stale_ids, stale_chunks, invalidated)

    def invalidate_revision(
        self,
        scope: MemoryScopeContext,
        *,
        file_id: str,
        revision_id: str,
        actor_id: str,
        reason: str = "explicit",
    ) -> None:
        """Mark a revision stale and publish ``artifact.revision_invalidated``.

        Unlike activation this does not switch the active revision; it is used
        for explicit invalidation (e.g. delete of a specific old revision).
        Idempotent: re-running on an already-stale revision is a no-op apart
        from the deduplicated event.
        """
        revision = self.db.scalar(
            select(DocumentRevision).where(
                DocumentRevision.id == revision_id,
                DocumentRevision.file_id == file_id,
                DocumentRevision.workspace_id == scope.workspace_id,
            )
        )
        if revision is None:
            raise LookupError("file revision not found in scope")
        if revision.lifecycle_status == "active":
            revision.lifecycle_status = "stale"
            revision.index_status = "stale"
        chunks = self.db.scalars(
            select(FileTextChunk).where(FileTextChunk.document_revision_id == revision.id)
        ).all()
        for chunk in chunks:
            if chunk.lifecycle_status == "active":
                chunk.lifecycle_status = "stale"
            if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
                self.db.execute(
                    text("DELETE FROM document_chunks_fts WHERE chunk_id = :id"),
                    {"id": chunk.id},
                )
        if self.store is not None:
            expected = self._artifact_stream_version(scope, file_id)
            self.store.append(
                scope,
                aggregate_type="artifact",
                aggregate_id=file_id,
                expected_version=expected,
                event=AppendEvent(
                    event_type=MemoryEventType.ARTIFACT_REVISION_INVALIDATED,
                    payload={
                        "file_id": file_id,
                        "revision_id": revision_id,
                        "reason": reason,
                        "actor_id": actor_id,
                    },
                    idempotency_key=f"rev_inv:{file_id}:{revision_id}:{reason}",
                    actor_id=actor_id,
                    file_id=file_id,
                ),
                outbox_kinds=("index",),
            )
        self.db.flush()

    def get_active_revision(self, file_id: str) -> DocumentRevision | None:
        return self.db.scalar(
            select(DocumentRevision).where(
                DocumentRevision.file_id == file_id,
                DocumentRevision.lifecycle_status == "active",
            )
        )

    def _artifact_stream_version(self, scope: MemoryScopeContext, file_id: str) -> int:
        """Current CAS version of the artifact event stream, 0 if it does not exist yet."""
        if self.store is None:
            return 0
        stream = self.store.events.stream_for_aggregate(scope, "artifact", file_id)
        return stream.current_version if stream is not None else 0
