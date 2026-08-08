from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.domain.memory_event_models import (
    MemoryEvent,
    MemoryPayloadKey,
    MemoryProjectionCheckpoint,
    MemorySearchDocument,
    MemoryStream,
    utc_now,
)
from app.domain.memory_event_types import MemoryEventType
from app.domain.models import MemoryRecord, MemoryRevision
from app.services.memory_crypto import EventPayloadUnavailable, MemoryPayloadCipher
from app.services.memory_upcasters import EventUpcasterRegistry, upcasters

logger = logging.getLogger(__name__)

# Events that fully replace the active search document content.
_CONTENT_UPSERT_EVENTS = frozenset(
    {
        MemoryEventType.MEMORY_CREATED,
        MemoryEventType.MEMORY_CORRECTED,
        MemoryEventType.MEMORY_CONFIRMED,
        MemoryEventType.MEMORY_RESTORED,
    }
)
# Events that only clear the document from search (no content required).
_REMOVE_EVENTS = frozenset(
    {
        MemoryEventType.MEMORY_SUPERSEDED,
        MemoryEventType.MEMORY_RETRACTED,
        MemoryEventType.MEMORY_DELETE_REQUESTED,
        MemoryEventType.MEMORY_FORGOTTEN,
    }
)
# Events that patch scope/sensitivity without requiring a full content rewrite.
_PATCH_EVENTS = frozenset(
    {
        MemoryEventType.MEMORY_SCOPE_CHANGED,
        MemoryEventType.MEMORY_SENSITIVITY_CHANGED,
        MemoryEventType.MEMORY_AUTO_RECALL_SUPPRESSED,
    }
)

SEARCH_PROJECTOR_NAME = "memory_search_v1"
FTS_CAPABILITY_PROJECTOR_NAME = "memory_search_fts_capability"


@dataclass(frozen=True, slots=True)
class ProjectionParity:
    legacy_count: int
    projection_count: int
    matching_hashes: int
    mismatches: list[str]


@dataclass(frozen=True, slots=True)
class SearchRebuildReport:
    event_count: int
    applied_count: int
    skipped_forgotten: int
    skipped_non_memory: int
    skipped_unavailable: int
    document_count: int
    fts_row_count: int
    content_fingerprint: str
    fts_capability: str


def normalize_bm25_score(raw_bm25: float) -> float:
    """Map SQLite FTS5 bm25() to a [0, 1) relevance score.

    FTS5 bm25 is lower (more negative) for better matches. The logistic map
    keeps ordering and bounds the fusion component used by the hybrid retriever.
    """

    try:
        value = float(raw_bm25)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    # raw=-12 → ~1.0, raw=0 → 0.5, raw=+6 → ~0.002
    return 1.0 / (1.0 + math.exp(value))


def probe_memory_search_fts_capability(db: Session) -> str:
    """Return ``trigram``, ``unicode``, or ``unavailable`` for the live FTS table."""

    if db.bind is None or db.bind.dialect.name != "sqlite":
        return "unavailable"
    try:
        row = db.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'memory_search_fts'"
            )
        ).first()
    except Exception:
        return "unavailable"
    if row is None or not row[0]:
        return "unavailable"
    sql = str(row[0]).casefold()
    if "tokenize" in sql and "trigram" in sql:
        return "trigram"
    return "unicode"


def ensure_memory_search_fts(db: Session) -> str:
    """Create the FTS projection if missing and record its tokenizer capability."""

    if db.bind is None or db.bind.dialect.name != "sqlite":
        return "unavailable"
    capability = probe_memory_search_fts_capability(db)
    if capability != "unavailable":
        _record_fts_capability(db, capability)
        return capability
    try:
        db.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search_fts USING fts5("
                "document_id UNINDEXED, tenant_id UNINDEXED, workspace_id UNINDEXED, "
                "subject, content, keywords, memory_type, entity_aliases, "
                "tokenize='trigram')"
            )
        )
        capability = "trigram"
    except Exception:
        db.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search_fts USING fts5("
                "document_id UNINDEXED, tenant_id UNINDEXED, workspace_id UNINDEXED, "
                "subject, content, keywords, memory_type, entity_aliases)"
            )
        )
        capability = "unicode"
    _record_fts_capability(db, capability)
    return capability


def _record_fts_capability(db: Session, capability: str) -> None:
    row = db.get(MemoryProjectionCheckpoint, FTS_CAPABILITY_PROJECTOR_NAME)
    if row is None:
        row = MemoryProjectionCheckpoint(
            projector_name=FTS_CAPABILITY_PROJECTOR_NAME,
            projection_version=1,
            last_global_position=0,
            last_error=capability,
            updated_at=utc_now(),
        )
        db.add(row)
    else:
        row.last_error = capability
        row.updated_at = utc_now()
    db.flush()


class MemoryProjector:
    VERSION = 1

    def __init__(
        self,
        db: Session,
        *,
        cipher: MemoryPayloadCipher | None = None,
        upcaster_registry: EventUpcasterRegistry = upcasters,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.upcasters = upcaster_registry

    def apply(self, event: MemoryEvent, payload: dict[str, Any]) -> None:
        if not event.event_type.startswith("memory."):
            return
        self._apply_memory(event, payload, aggregate_id=None)

    def _apply_memory(
        self,
        event: MemoryEvent,
        payload: dict[str, Any] | None,
        *,
        aggregate_id: str | None,
    ) -> None:
        memory_id = str((payload or {}).get("memory_id") or aggregate_id or "")
        if not memory_id:
            return

        if event.event_type in _REMOVE_EVENTS:
            self._remove_search_document(memory_id)
            return

        if event.event_type in _PATCH_EVENTS:
            self._patch_search_document(event, payload or {}, memory_id)
            return

        if event.event_type not in _CONTENT_UPSERT_EVENTS and not (
            payload and (payload.get("title") or payload.get("content"))
        ):
            # Feedback and other non-content memory.* events must not invent empty docs.
            return

        if payload is None:
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
        existing.memory_layer = str(payload.get("memory_layer") or existing.memory_layer or "L3")
        existing.zone = str(payload.get("zone") or existing.zone or "recent")
        existing.memory_type = str(
            payload.get("record_kind") or existing.memory_type or "semantic_memory"
        )
        existing.subject = title
        existing.slot_key = str(payload.get("canonical_key") or existing.slot_key or "")
        existing.content = content
        existing.keywords_text = " ".join(str(v) for v in payload.get("keywords", []) or [])
        existing.entity_aliases_text = " ".join(
            str(v) for v in payload.get("entity_aliases", []) or []
        )
        existing.status = "active"
        existing.sensitivity = str(payload.get("sensitivity") or event.sensitivity or "normal")
        existing.importance = float(payload.get("importance") or existing.importance or 0.5)
        existing.confidence = float(payload.get("confidence") or existing.confidence or 0.7)
        existing.tenant_id = event.tenant_id
        existing.subject_user_id = event.subject_user_id
        existing.workspace_id = event.workspace_id
        existing.task_id = event.task_id
        existing.project_id = event.project_id or existing.project_id
        existing.conversation_id = event.conversation_id or existing.conversation_id
        existing.file_id = event.file_id or existing.file_id
        existing.knowledge_node_id = event.knowledge_node_id or existing.knowledge_node_id
        existing.source_event_id = event.event_id
        existing.content_hash = digest
        existing.projection_version = self.VERSION
        self.db.flush()
        self._upsert_fts(existing)

    def _patch_search_document(
        self, event: MemoryEvent, payload: dict[str, Any], memory_id: str
    ) -> None:
        existing = self.db.scalar(
            select(MemorySearchDocument).where(
                MemorySearchDocument.target_type == "memory",
                MemorySearchDocument.target_id == memory_id,
            )
        )
        if existing is None:
            return
        if event.event_type == MemoryEventType.MEMORY_AUTO_RECALL_SUPPRESSED:
            existing.status = "suppressed"
            self._delete_fts_row(existing.id)
            self.db.flush()
            return
        if event.event_type == MemoryEventType.MEMORY_SENSITIVITY_CHANGED:
            existing.sensitivity = str(
                payload.get("sensitivity") or event.sensitivity or existing.sensitivity
            )
        if event.event_type == MemoryEventType.MEMORY_SCOPE_CHANGED:
            if "workspace_id" in payload:
                existing.workspace_id = payload.get("workspace_id")
            if "task_id" in payload:
                existing.task_id = payload.get("task_id")
            if "subject_user_id" in payload:
                existing.subject_user_id = payload.get("subject_user_id")
            if "memory_layer" in payload:
                existing.memory_layer = str(payload.get("memory_layer") or existing.memory_layer)
            if "audience_type" in payload and payload.get("audience_type") == "workspace":
                existing.subject_user_id = None
                existing.memory_layer = str(payload.get("memory_layer") or "L4")
        existing.source_event_id = event.event_id
        existing.target_version = event.stream_version
        existing.projection_version = self.VERSION
        self.db.flush()
        if existing.status == "active":
            self._upsert_fts(existing)

    def _remove_search_document(self, memory_id: str) -> None:
        documents = self.db.scalars(
            select(MemorySearchDocument).where(
                MemorySearchDocument.target_type == "memory",
                MemorySearchDocument.target_id == memory_id,
            )
        ).all()
        for document in documents:
            self._delete_fts_row(document.id)
            self.db.delete(document)
        self.db.flush()

    def _delete_fts_row(self, document_id: str) -> None:
        if self.db.bind is None or self.db.bind.dialect.name != "sqlite":
            return
        self.db.execute(
            text("DELETE FROM memory_search_fts WHERE document_id = :id"),
            {"id": document_id},
        )

    def _upsert_fts(self, document: MemorySearchDocument) -> None:
        if self.db.bind is None or self.db.bind.dialect.name != "sqlite":
            return
        ensure_memory_search_fts(self.db)
        self._delete_fts_row(document.id)
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

    def rebuild_search_projection(
        self,
        *,
        read_payload: Callable[[MemoryEvent], dict[str, Any] | None] | None = None,
        after_position: int = 0,
    ) -> SearchRebuildReport:
        """Delete search documents/FTS and fully replay Event Store by global_position.

        The entire rebuild is intended to run inside the caller's transaction: on
        failure the caller rolls back both structured rows and FTS writes.
        """

        fts_capability = ensure_memory_search_fts(self.db)
        self.db.execute(delete(MemorySearchDocument))
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("DELETE FROM memory_search_fts"))
        self.db.flush()

        events: Sequence[MemoryEvent] = self.db.scalars(
            select(MemoryEvent)
            .where(MemoryEvent.global_position > after_position)
            .order_by(MemoryEvent.global_position)
        ).all()
        streams = {
            stream.id: stream
            for stream in self.db.scalars(select(MemoryStream)).all()
        }
        reader = read_payload or self._default_payload_reader

        applied = 0
        skipped_forgotten = 0
        skipped_non_memory = 0
        skipped_unavailable = 0
        last_position = after_position

        for event in events:
            last_position = event.global_position
            if not event.event_type.startswith("memory."):
                skipped_non_memory += 1
                continue

            stream = streams.get(event.stream_id)
            aggregate_id = stream.aggregate_id if stream is not None else None

            if event.event_type in _REMOVE_EVENTS:
                # Forgotten payloads may be irrecoverable; aggregate_id is enough.
                self._apply_memory(event, None, aggregate_id=aggregate_id)
                applied += 1
                if event.event_type == MemoryEventType.MEMORY_FORGOTTEN:
                    skipped_forgotten += 1
                continue

            try:
                payload = reader(event)
            except EventPayloadUnavailable:
                skipped_unavailable += 1
                continue
            if payload is None:
                # Key destroyed or redacted — treat as non-searchable history gap.
                if event.event_type in _CONTENT_UPSERT_EVENTS and aggregate_id:
                    self._remove_search_document(aggregate_id)
                skipped_unavailable += 1
                continue

            self._apply_memory(event, payload, aggregate_id=aggregate_id)
            applied += 1

        documents = self.db.scalars(
            select(MemorySearchDocument).order_by(
                MemorySearchDocument.target_type,
                MemorySearchDocument.target_id,
            )
        ).all()
        fts_row_count = 0
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            try:
                fts_row_count = int(
                    self.db.execute(text("SELECT count(*) FROM memory_search_fts")).scalar() or 0
                )
            except Exception:
                fts_row_count = 0

        fingerprint_source = [
            {
                "target_type": doc.target_type,
                "target_id": doc.target_id,
                "content_hash": doc.content_hash,
                "status": doc.status,
                "sensitivity": doc.sensitivity,
                "workspace_id": doc.workspace_id,
                "tenant_id": doc.tenant_id,
            }
            for doc in documents
        ]
        content_fingerprint = hashlib.sha256(
            json.dumps(fingerprint_source, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        checkpoint = self.db.get(MemoryProjectionCheckpoint, SEARCH_PROJECTOR_NAME)
        if checkpoint is None:
            checkpoint = MemoryProjectionCheckpoint(
                projector_name=SEARCH_PROJECTOR_NAME,
                projection_version=self.VERSION,
                last_global_position=last_position,
                last_error="",
                updated_at=utc_now(),
            )
            self.db.add(checkpoint)
        else:
            checkpoint.projection_version = self.VERSION
            checkpoint.last_global_position = last_position
            checkpoint.last_error = ""
            checkpoint.updated_at = utc_now()
        self.db.flush()

        return SearchRebuildReport(
            event_count=len(events),
            applied_count=applied,
            skipped_forgotten=skipped_forgotten,
            skipped_non_memory=skipped_non_memory,
            skipped_unavailable=skipped_unavailable,
            document_count=len(documents),
            fts_row_count=fts_row_count,
            content_fingerprint=content_fingerprint,
            fts_capability=fts_capability,
        )

    def _default_payload_reader(self, event: MemoryEvent) -> dict[str, Any] | None:
        if self.cipher is None:
            raise RuntimeError(
                "MemoryProjector.rebuild_search_projection requires a cipher "
                "or an explicit read_payload callback"
            )
        if event.payload_key_id is None or event.payload_ciphertext is None:
            return None
        key = self.db.get(MemoryPayloadKey, event.payload_key_id)
        if key is None or key.status == "destroyed" or key.wrapped_dek is None:
            return None
        payload = self.cipher.decrypt(event.payload_ciphertext, key.wrapped_dek)
        _version, payload = self.upcasters.upcast(
            event.event_type, event.event_schema_version, payload
        )
        return payload

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
            # Snapshot marker only — not a real domain event. content-bearing
            # payload still drives the upsert path via title/content presence.
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
                subject_user_id=record.subject_user_id,
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
                    "memory_layer": record.memory_layer,
                    "sensitivity": record.sensitivity,
                },
                aggregate_id=record.id,
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

    def search_document_count(self) -> int:
        return int(self.db.scalar(select(func.count()).select_from(MemorySearchDocument)) or 0)
