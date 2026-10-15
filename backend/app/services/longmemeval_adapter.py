"""LongMemEval-specific ingestion and retrieval provenance.

This module is deliberately separate from the product memory intent API.  A
benchmark turn is evidence first: it keeps role, source identifiers and the
historical observation time, while only user-authored evidence is eligible for
the normal profile projection.  Assistant text remains searchable evidence.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.memory_event_models import MemoryScopeContext, MemorySearchDocument
from app.domain.models import MemoryEvidence, User, Workspace
from app.services.memory_event_ingestor import event_cipher_from_settings
from app.services.memory_event_store import AppendEvent, MemoryEventStore
from app.services.memory_projector import MemoryProjector


BENCHMARK_NAME = "longmemeval"
BENCHMARK_EVENT_TYPE = "benchmark.longmemeval.session_turn_ingested"
MAX_CHUNK_CHARS = 3_500


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_session_date(value: str, timezone_name: str = "UTC") -> datetime:
    """Parse LongMemEval dates without consulting the current clock."""

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("session_date is required")
    # Dataset values contain a weekday in parentheses, e.g. 2023/05/20 (Sat) 02:21.
    cleaned = re.sub(r"\s*\([^)]*\)", "", raw).strip().replace("Z", "+00:00")
    parsed: datetime | None = None
    for candidate in (cleaned, cleaned.replace("/", "-")):
        try:
            parsed = datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if parsed is None:
        for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(cleaned, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(f"Unsupported session_date: {value}")
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name or "UTC"))
        except Exception:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def chunk_text(content: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split large turns while retaining every character and stable order."""

    if len(content) <= max_chars:
        return [content]
    chunks: list[str] = []
    start = 0
    while start < len(content):
        end = min(len(content), start + max_chars)
        if end < len(content):
            boundary = max(content.rfind("\n", start, end), content.rfind(" ", start, end))
            if boundary > start + max_chars // 2:
                end = boundary
        chunks.append(content[start:end])
        start = end
    return chunks


class LongMemEvalAdapter:
    def __init__(self, db: Session, settings: Settings, workspace: Workspace, user: User) -> None:
        self.db = db
        self.settings = settings
        self.workspace = workspace
        self.user = user
        self.scope = MemoryScopeContext(
            tenant_id=user.tenant_id,
            principal_user_id=user.id,
            workspace_id=workspace.id,
            agent_id="longmemeval",
        )
        self.store = MemoryEventStore(db, event_cipher_from_settings(settings))

    def ingest_session(
        self,
        *,
        question_id: str,
        session_id: str,
        session_index: int = 0,
        session_date: str,
        timezone_name: str,
        ingestion_mode: str = "evidence",
        turns: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        if not question_id or not session_id:
            raise ValueError("question_id and session_id are required")
        if session_index < 0:
            raise ValueError("session_index must be non-negative")
        if ingestion_mode not in {"evidence", "product_memory"}:
            raise ValueError("ingestion_mode must be evidence or product_memory")
        observed_at = parse_session_date(session_date, timezone_name)
        turn_results: list[dict[str, Any]] = []
        for turn_index, turn in enumerate(turns):
            role = str(turn.get("role") or "").strip().casefold()
            content = str(turn.get("content") or "")
            if role not in {"user", "assistant"}:
                raise ValueError(f"turn {turn_index} has unsupported role: {role}")
            if not content:
                continue
            chunks = chunk_text(content)
            chunk_results: list[dict[str, Any]] = []
            for chunk_index, chunk in enumerate(chunks):
                external_source = {
                    "benchmark": BENCHMARK_NAME,
                    "question_id": question_id,
                    "session_id": session_id,
                    "external_session_id": session_id,
                    "session_index": session_index,
                    "external_session_key": f"{session_id}#{session_index}",
                    "session_date": session_date,
                    "original_time_expression": session_date,
                    "observed_at": observed_at.isoformat(),
                    "timezone_name": timezone_name or "UTC",
                    "turn_index": turn_index,
                    "role": role,
                    "chunk_index": chunk_index,
                    "total_chunks": len(chunks),
                }
                stable = (
                    f"{question_id}\0{session_index}\0{session_id}"
                    f"\0{turn_index}\0{chunk_index}"
                )
                aggregate_id = _hash(stable)[:64]
                idempotency_key = f"longmemeval:{_hash(stable)}"
                payload = {
                    "benchmark": BENCHMARK_NAME,
                    "external_source": external_source,
                    "content_hash": _hash(chunk),
                }
                event_result = self.store.append(
                    self.scope,
                    aggregate_type="episode",
                    aggregate_id=aggregate_id,
                    expected_version=None,
                    event=AppendEvent(
                        event_type=BENCHMARK_EVENT_TYPE,
                        payload=payload,
                        idempotency_key=idempotency_key,
                        producer="api",
                        actor_type="system",
                        actor_id="longmemeval",
                        occurred_at=observed_at,
                        metadata={"external_source": external_source},
                    ),
                )
                source_id = f"longmemeval:{_hash(stable)}"
                evidence = self.db.scalar(
                    select(MemoryEvidence).where(
                        MemoryEvidence.workspace_id == self.workspace.id,
                        MemoryEvidence.source_kind == f"benchmark_{role}_turn",
                        MemoryEvidence.source_id == source_id,
                    )
                )
                if evidence is None:
                    evidence = MemoryEvidence(
                        workspace_id=self.workspace.id,
                        source_kind=f"benchmark_{role}_turn",
                        source_id=source_id,
                        authorship=role,
                        observed_at=observed_at,
                        content_hash=_hash(chunk),
                        # Keep the original turn text here. This is evidence,
                        # not a normalized user profile statement.
                        excerpt=chunk,
                        derived_from=[external_source],
                        profile_eligible=role == "user",
                        eligibility_reason=(
                            "benchmark_user_evidence" if role == "user" else "assistant_benchmark_evidence"
                        ),
                    )
                    self.db.add(evidence)
                    self.db.flush()
                document = self.db.scalar(
                    select(MemorySearchDocument).where(
                        MemorySearchDocument.target_type == "benchmark_evidence",
                        MemorySearchDocument.target_id == evidence.id,
                    )
                )
                if document is None:
                    document = MemorySearchDocument(
                        target_type="benchmark_evidence",
                        target_id=evidence.id,
                        target_version=1,
                        memory_layer="L4",
                        memory_type="benchmark_evidence",
                        subject=f"{role} turn {turn_index}",
                        # slot_key is an internal label, not the provenance
                        # source of truth. Keep it bounded even when the API
                        # accepts the maximum-length external identifiers.
                        slot_key=f"longmemeval:{_hash(stable)[:64]}",
                        content=chunk,
                        keywords_text=f"{question_id} {session_id} {role}",
                        entity_aliases_text=session_id,
                        tenant_id=self.user.tenant_id,
                        subject_user_id=self.user.id,
                        workspace_id=self.workspace.id,
                        status="active",
                        sensitivity="normal",
                        valid_from=observed_at,
                        importance=0.6,
                        confidence=1.0,
                        source_event_id=event_result.event.event_id,
                        content_hash=_hash(chunk),
                        metadata_json={"external_source": external_source},
                    )
                    self.db.add(document)
                    self.db.flush()
                    # Keep benchmark evidence on the same FTS/BM25 projection
                    # used by normal memory retrieval.
                    MemoryProjector(self.db)._upsert_fts(document)
                profile_result: dict[str, Any] | None = None
                if ingestion_mode == "product_memory" and role == "user":
                    from app.domain.schemas.management import MemoryProfileIntentRequest
                    from app.services.memory_profile import MemoryProfileService

                    result = MemoryProfileService(
                        self.db, self.workspace, self.user.id, self.settings
                    ).apply_intent(
                        MemoryProfileIntentRequest(
                            text=chunk,
                            timezone_name=timezone_name or "UTC",
                            observed_at=observed_at,
                        )
                    )
                    profile_result = result.model_dump(mode="json")
                chunk_results.append(
                    {
                        "evidence_id": evidence.id,
                        "event_id": event_result.event.event_id,
                        "chunk_index": chunk_index,
                        "total_chunks": len(chunks),
                        "external_source": external_source,
                        "product_memory": profile_result,
                    }
                )
            turn_results.append({"turn_index": turn_index, "role": role, "chunks": chunk_results})
        self.db.commit()
        return {
            "benchmark": BENCHMARK_NAME,
            "question_id": question_id,
            "session_id": session_id,
            "session_index": session_index,
            "session_date": session_date,
            "observed_at": observed_at.isoformat(),
            "timezone_name": timezone_name or "UTC",
            "ingestion_mode": ingestion_mode,
            "turns": turn_results,
        }


__all__ = ["BENCHMARK_NAME", "LongMemEvalAdapter", "chunk_text", "parse_session_date"]
