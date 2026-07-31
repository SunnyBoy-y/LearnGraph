from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.memory_event_models import MemoryScopeContext
from app.domain.models import DocumentRevision, FileRecord, FileTextChunk, MemoryEvidence, utc_now


@dataclass(frozen=True, slots=True)
class RevisionActivationReport:
    activated_revision_id: str
    stale_revision_ids: tuple[str, ...]
    stale_chunk_count: int
    invalidated_memory_evidence: int


class MemoryFileInvalidationService:
    """Atomically switches the active revision and invalidates old derived evidence."""

    def __init__(self, db: Session) -> None:
        self.db = db

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
        self.db.flush()
        return RevisionActivationReport(revision.id, stale_ids, stale_chunks, invalidated)
