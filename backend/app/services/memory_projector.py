from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.domain.memory_event_models import MemoryEvent, MemorySearchDocument
from app.domain.memory_event_types import MemoryEventType
from app.domain.models import MemoryRecord, MemoryRevision


@dataclass(frozen=True, slots=True)
class ProjectionParity:
    legacy_count: int
    projection_count: int
    matching_hashes: int
    mismatches: list[str]


class MemoryProjector:
    VERSION = 1

    def __init__(self, db: Session) -> None:
        self.db = db

    def apply(self, event: MemoryEvent, payload: dict[str, Any]) -> None:
        if event.event_type.startswith("memory."):
            self._apply_memory(event, payload)

    def _apply_memory(self, event: MemoryEvent, payload: dict[str, Any]) -> None:
        memory_id = str(payload.get("memory_id") or "")
        if not memory_id:
            return
        if event.event_type in {
            MemoryEventType.MEMORY_SUPERSEDED,
            MemoryEventType.MEMORY_RETRACTED,
            MemoryEventType.MEMORY_DELETE_REQUESTED,
            MemoryEventType.MEMORY_FORGOTTEN,
        }:
            self._remove_search_document(memory_id)
            return
        title = str(payload.get("title") or "")
        content = str(payload.get("content") or "")
        status = str(payload.get("status") or "active")
        if status != "active":
            self._remove_search_document(memory_id)
            return
        existing = self.db.scalar(
            select(MemorySearchDocument).where(
                MemorySearchDocument.target_type == "memory",
                MemorySearchDocument.target_id == memory_id,
            )
        )
        digest = hashlib.sha256(f"{title}\0{content}".encode("utf-8")).hexdigest()
        if existing is None:
            existing = MemorySearchDocument(
                target_type="memory",
                target_id=memory_id,
                tenant_id=event.tenant_id,
                subject_user_id=event.subject_user_id,
                workspace_id=event.workspace_id,
                task_id=event.task_id,
                source_event_id=event.event_id,
                content_hash=digest,
            )
            self.db.add(existing)
        existing.target_version = event.stream_version
        existing.memory_layer = str(payload.get("memory_layer") or "L3")
        existing.memory_type = str(payload.get("record_kind") or "semantic_memory")
        existing.subject = title
        existing.slot_key = str(payload.get("canonical_key") or "")
        existing.content = content
        existing.keywords_text = " ".join(str(v) for v in payload.get("keywords", []))
        existing.entity_aliases_text = " ".join(
            str(v) for v in payload.get("entity_aliases", [])
        )
        existing.status = "active"
        existing.sensitivity = str(payload.get("sensitivity") or "normal")
        existing.importance = float(payload.get("importance") or 0.5)
        existing.confidence = float(payload.get("confidence") or 0.7)
        existing.source_event_id = event.event_id
        existing.content_hash = digest
        existing.projection_version = self.VERSION
        self.db.flush()
        self._upsert_fts(existing)

    def _remove_search_document(self, memory_id: str) -> None:
        documents = self.db.scalars(
            select(MemorySearchDocument).where(
                MemorySearchDocument.target_type == "memory",
                MemorySearchDocument.target_id == memory_id,
            )
        ).all()
        for document in documents:
            if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
                self.db.execute(
                    text("DELETE FROM memory_search_fts WHERE document_id = :id"),
                    {"id": document.id},
                )
            self.db.delete(document)
        self.db.flush()

    def _upsert_fts(self, document: MemorySearchDocument) -> None:
        if self.db.bind is None or self.db.bind.dialect.name != "sqlite":
            return
        self.db.execute(
            text("DELETE FROM memory_search_fts WHERE document_id = :id"),
            {"id": document.id},
        )
        self.db.execute(
            text(
                "INSERT INTO memory_search_fts("
                "document_id, tenant_id, workspace_id, subject, content, keywords, "
                "memory_type, entity_aliases) VALUES "
                "(:id, :tenant, :workspace, :subject, :content, :keywords, :memory_type, :aliases)"
            ),
            {
                "id": document.id,
                "tenant": document.tenant_id,
                "workspace": document.workspace_id or "",
                "subject": document.subject,
                "content": document.content,
                "keywords": document.keywords_text,
                "memory_type": document.memory_type,
                "aliases": document.entity_aliases_text,
            },
        )

    def rebuild_search_projection(self) -> int:
        self.db.execute(delete(MemorySearchDocument))
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("DELETE FROM memory_search_fts"))
        events = self.db.scalars(
            select(MemoryEvent).order_by(MemoryEvent.global_position)
        ).all()
        return len(events)

    def backfill_legacy(self, *, tenant_id: str, workspace_id: str) -> int:
        """Create snapshot search rows without pretending legacy history existed."""

        records = self.db.scalars(
            select(MemoryRecord).where(MemoryRecord.workspace_id == workspace_id)
        ).all()
        count = 0
        for record in records:
            if record.state != "active":
                continue
            revision = self.db.scalar(
                select(MemoryRevision)
                .where(
                    MemoryRevision.workspace_id == workspace_id,
                    MemoryRevision.memory_id == record.id,
                    MemoryRevision.is_active.is_(True),
                )
                .order_by(MemoryRevision.revision.desc())
            )
            if revision is None or revision.content is None:
                continue
            synthetic = MemoryEvent(
                global_position=0,
                event_id=f"legacy:{record.id}:{record.revision}",
                stream_id="legacy",
                stream_version=record.revision,
                event_type="legacy.memory_state_snapshotted",
                event_schema_version=1,
                producer="migration",
                actor_type="system",
                actor_id="legacy:unknown",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                subject_user_id=None,
                idempotency_key=f"legacy:{record.id}:{record.revision}",
                payload_hash=f"sha256:{record.content_hash}",
                metadata_json={"history_complete": False},
                occurred_at=record.updated_at or datetime.now(timezone.utc),
                ingested_at=datetime.now(timezone.utc),
            )
            self._apply_memory(
                synthetic,
                {
                    "memory_id": record.id,
                    "title": record.title,
                    "content": revision.content,
                    "record_kind": record.record_kind,
                    "canonical_key": record.canonical_key,
                    "importance": record.importance,
                    "confidence": record.confidence,
                    "status": "active",
                },
            )
            count += 1
        return count

    def parity_report(self, workspace_id: str) -> ProjectionParity:
        records = self.db.scalars(
            select(MemoryRecord).where(
                MemoryRecord.workspace_id == workspace_id,
                MemoryRecord.state == "active",
            )
        ).all()
        documents = self.db.scalars(
            select(MemorySearchDocument).where(
                MemorySearchDocument.workspace_id == workspace_id,
                MemorySearchDocument.target_type == "memory",
            )
        ).all()
        by_id = {document.target_id: document for document in documents}
        matching = 0
        mismatches: list[str] = []
        for record in records:
            document = by_id.get(record.id)
            if document is None:
                mismatches.append(record.id)
                continue
            revision = self.db.scalar(
                select(MemoryRevision)
                .where(
                    MemoryRevision.memory_id == record.id,
                    MemoryRevision.workspace_id == workspace_id,
                    MemoryRevision.is_active.is_(True),
                )
                .order_by(MemoryRevision.revision.desc())
            )
            if revision is None or revision.content is None:
                mismatches.append(record.id)
                continue
            digest = hashlib.sha256(
                f"{record.title}\0{revision.content}".encode("utf-8")
            ).hexdigest()
            if digest == document.content_hash:
                matching += 1
            else:
                mismatches.append(record.id)
        return ProjectionParity(len(records), len(documents), matching, mismatches)
