"""Optional memory enhancement pipelines: semantic recall and auto extraction.

Two independently configured capabilities on top of the base memory system:

- **Semantic recall (embedding plugin).** The default recall pipeline is the
  heuristic scope/strength scoring in ``MemoryService`` and never requires an
  embedding model. When the workspace configures an OpenAI-compatible
  embedding endpoint (for example Qwen ``text-embedding-v4`` via DashScope
  compatible mode), recall additionally blends cosine similarity between the
  current user message and each candidate memory. Removing the configuration
  restores the exact no-embedding behaviour.

- **Auto extraction (background "dreaming").** A scheduler sweep reviews
  sessions that have gone quiet, asks a *separately configured* model to
  extract durable memories from the new turns, and routes every proposal
  through the existing MemoryDraft flow — CREATE proposals may auto-commit
  under the standing confidence gate, UPDATE proposals always wait for human
  review.

Both configurations live in one WorkspaceSetting row (``memory.enhancement``)
so they survive provider changes and export/import.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func as sql_func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import commit_with_locked_retry
from app.core.errors import AppError
from app.domain.memory_event_models import MemorySearchDocument
from app.domain.memory_types import MEMORY_TYPE_REGISTRY, get_memory_type
from app.domain.models import (
    ChatSession,
    ContextSummary,
    MemoryDraft,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryExtractionState,
    MemoryRecord,
    MemoryRevision,
    Message,
    Workspace,
    WorkspaceSetting,
    utc_now,
)
from app.domain.schemas.management import MemoryDraftCreateRequest
from app.providers.factory import (
    embedding_provider_for_workspace,
    memory_provider_for_workspace,
    model_provider_for_workspace,
)
from app.services.billing import BillingService
from app.services.memory_plan import (
    PLAN_STATES,
    canonical_plan_key,
    ensure_plan_canonical_key,
    normalize_plan_status,
    plan_change_text,
    resolve_target_for_operation,
)
from app.services.temporal_normalizer import TemporalNormalizer
from app.services.token_estimate import estimate_tokens

logger = logging.getLogger(__name__)

MEMORY_ENHANCEMENT_KEY = "memory.enhancement"
MEMORY_POLICY_KEY = "memory.shared_policy"

# Types the background extractor may propose. Deliberately excludes
# goal_constraint (authoritative user intent) and ai_observation (reserved for
# concept-branch close proposals).
EXTRACTABLE_MEMORY_TYPES = (
    "semantic_memory",
    "learning_preference",
    "misconception",
    "strategy_effectiveness",
    "decision",
    "event_summary",
)

_QUERY_CHAR_CAP = 2_000
_MEMORY_TEXT_CHAR_CAP = 2_000
_BACKFILL_PER_CALL = 24
_REINDEX_CAP = 500
_REINDEX_TOTAL_CAP = 10_000
_TRANSCRIPT_MESSAGE_CAP = 80
_TRANSCRIPT_CHARS_PER_MESSAGE = 1_200
# This is a safety ceiling, not a target. The extractor is told to return only
# durable, high-value atoms, so a small conversation may still yield none.
_EXTRACTION_MAX_PROPOSALS = 24
_EXISTING_MEMORY_CONTEXT_CAP = 30


def _parse_extraction_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def default_enhancement_config() -> dict[str, Any]:
    return {
        "extraction": {
            # Default ON: whether extraction runs is gated only by the
            # workspace "memory" master switch (workspace_enabled). The
            # provider/model fall back to the workspace default model.
            "enabled": True,
            "provider_id": "",
            "model_id": "",
            "follow_conversation": False,
            "auto_commit": True,
        },
        "embedding": {
            "enabled": False,
            "provider_id": "",
            "model_id": "",
            "semantic_weight": 0.8,
        },
        # Background rolling summaries of long sessions (ContextSummary
        # kind='model'). Empty provider/model falls back to the extraction
        # model, so enabling the toggle alone is enough once extraction is set.
        "summarization": {
            "enabled": False,
            "provider_id": "",
            "model_id": "",
            "follow_conversation": False,
        },
    }


def _normalize_config(raw: Any) -> dict[str, Any]:
    config = default_enhancement_config()
    if not isinstance(raw, dict):
        return config
    for section in tuple(config):
        stored = raw.get(section)
        if not isinstance(stored, dict):
            continue
        target = config[section]
        for key, current in list(target.items()):
            value = stored.get(key)
            if isinstance(current, bool):
                if isinstance(value, bool):
                    target[key] = value
            elif isinstance(current, float):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    target[key] = min(2.0, max(0.0, float(value)))
            elif isinstance(current, str):
                if isinstance(value, str):
                    target[key] = value.strip()
    return config


def load_enhancement_config(db: Session, workspace_id: str) -> dict[str, Any]:
    setting = db.scalar(
        select(WorkspaceSetting).where(
            WorkspaceSetting.workspace_id == workspace_id,
            WorkspaceSetting.key == MEMORY_ENHANCEMENT_KEY,
        )
    )
    return _normalize_config(setting.value if setting is not None else None)


def save_enhancement_config(
    db: Session, workspace_id: str, updates: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Persist enhancement settings; return ``(config, cache_invalidated)``.

    ``cache_invalidated`` is non-None only when the embedding provider/model
    changed *and* the previous model still had cached vectors — so the caller
    can surface "reindex required + old cache to prune" without a second
    round trip. Old-model vector rows are projections, not fact sources: they
    are safe to prune once the switch is confirmed.
    """

    setting = db.scalar(
        select(WorkspaceSetting).where(
            WorkspaceSetting.workspace_id == workspace_id,
            WorkspaceSetting.key == MEMORY_ENHANCEMENT_KEY,
        )
    )
    current = _normalize_config(setting.value if setting is not None else None)
    previous_embedding = {
        "provider_id": current["embedding"]["provider_id"],
        "model_id": current["embedding"]["model_id"],
    }
    for section in tuple(current):
        patch = updates.get(section)
        if isinstance(patch, dict):
            merged = dict(current[section])
            merged.update({key: value for key, value in patch.items() if value is not None})
            current[section] = merged
    normalized = _normalize_config(current)
    if setting is None:
        db.add(
            WorkspaceSetting(
                workspace_id=workspace_id,
                key=MEMORY_ENHANCEMENT_KEY,
                value=normalized,
            )
        )
    else:
        setting.value = normalized
    db.commit()

    cache_invalidated: dict[str, Any] | None = None
    embedding = normalized["embedding"]
    previous_key = _model_key(
        previous_embedding["provider_id"], previous_embedding["model_id"]
    )
    current_key = _model_key(embedding["provider_id"], embedding["model_id"])
    if previous_key != current_key:
        previous_count = (
            db.scalar(
                select(sql_func.count(MemoryEmbedding.id)).where(
                    MemoryEmbedding.workspace_id == workspace_id,
                    MemoryEmbedding.model_key == previous_key,
                )
            )
            or 0
        )
        if previous_count:
            cache_invalidated = {
                "previous_model_key": previous_key,
                "previous_indexed_count": previous_count,
                "current_model_key": current_key,
            }
    return normalized, cache_invalidated


def _workspace_memory_enabled(db: Session, workspace_id: str) -> bool:
    setting = db.scalar(
        select(WorkspaceSetting).where(
            WorkspaceSetting.workspace_id == workspace_id,
            WorkspaceSetting.key == MEMORY_POLICY_KEY,
        )
    )
    if setting is None or not isinstance(setting.value, dict):
        return False
    return bool(
        setting.value.get("workspace_enabled")
        and setting.value.get("workspace_learning_enabled", True)
    )


# ---------------------------------------------------------------------------
# Semantic recall (embedding plugin)
# ---------------------------------------------------------------------------


def _model_key(provider_id: str, model_id: str) -> str:
    return f"{provider_id}:{model_id}"


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


def _memory_texts(
    db: Session, workspace_id: str, records: list[MemoryRecord]
) -> dict[str, str]:
    """Current revision content per memory id (title-only when scrubbed)."""

    ids = [record.id for record in records]
    revisions: dict[tuple[str, int], str | None] = {}
    if ids:
        rows = db.scalars(
            select(MemoryRevision).where(
                MemoryRevision.workspace_id == workspace_id,
                MemoryRevision.memory_id.in_(ids),
            )
        ).all()
        for row in rows:
            revisions[(row.memory_id, row.revision)] = row.content
    texts: dict[str, str] = {}
    for record in records:
        content = revisions.get((record.id, record.revision)) or ""
        texts[record.id] = f"{record.title}\n{content}"[:_MEMORY_TEXT_CHAR_CAP]
    return texts


def _embedding_rows(
    db: Session, workspace_id: str, model_key: str, memory_ids: list[str]
) -> dict[str, MemoryEmbedding]:
    if not memory_ids:
        return {}
    rows = db.scalars(
        select(MemoryEmbedding).where(
            MemoryEmbedding.workspace_id == workspace_id,
            MemoryEmbedding.model_key == model_key,
            MemoryEmbedding.memory_id.in_(memory_ids),
        )
    ).all()
    return {row.memory_id: row for row in rows}


def _upsert_embeddings(
    db: Session,
    workspace_id: str,
    model_key: str,
    records: list[MemoryRecord],
    vectors: list[list[float]],
    existing: dict[str, MemoryEmbedding],
) -> None:
    for record, vector in zip(records, vectors):
        row = existing.get(record.id)
        if row is None:
            row = MemoryEmbedding(
                workspace_id=workspace_id,
                memory_id=record.id,
                model_key=model_key,
            )
            db.add(row)
            existing[record.id] = row
        row.content_hash = record.content_hash
        row.dim = len(vector)
        row.vector = vector


def semantic_boosts_for_records(
    db: Session,
    workspace_id: str,
    settings: Settings,
    query_text: str,
    records: list[MemoryRecord],
    config: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Weighted similarity boost per memory id; empty dict = no enhancement.

    Never raises: any provider failure degrades recall back to the heuristic
    pipeline instead of failing the chat turn.
    """

    query = (query_text or "").strip()
    if not query or not records:
        return {}
    try:
        config = config or load_enhancement_config(db, workspace_id)
        embedding_cfg = config["embedding"]
        if not embedding_cfg["enabled"]:
            return {}
        provider = embedding_provider_for_workspace(
            db,
            workspace_id,
            settings,
            provider_id=embedding_cfg["provider_id"],
            model_id=embedding_cfg["model_id"],
        )
        if provider is None:
            return {}
        model_key = _model_key(provider.provider_id, provider.model_id)
        existing = _embedding_rows(db, workspace_id, model_key, [r.id for r in records])
        fresh: dict[str, list[float]] = {}
        stale: list[MemoryRecord] = []
        for record in records:
            row = existing.get(record.id)
            if row is not None and row.vector and row.content_hash == record.content_hash:
                fresh[record.id] = list(row.vector)
            else:
                stale.append(record)
        stale = stale[:_BACKFILL_PER_CALL]
        if not fresh and not stale:
            return {}
        query_snippet = query[:_QUERY_CHAR_CAP]
        stale_texts = _memory_texts(db, workspace_id, stale) if stale else {}
        billing = BillingService(db, workspace_id, "system:memory-embedding")
        try:
            quote = billing.preflight_model_call(
                provider_id=provider.provider_id,
                model_id=provider.model_id,
                feature="memory_embedding",
                estimated_input_tokens=estimate_tokens(query_snippet)
                + sum(estimate_tokens(text) for text in stale_texts.values()),
                estimated_output_tokens=0,
                remote_capability=True,
            )
        except AppError:
            # Budget exhausted: degrade to heuristic recall instead of billing.
            return {}
        started_at = time.monotonic()
        used_tokens = 0
        if stale:
            vectors = provider.embed([stale_texts[record.id] for record in stale])
            used_tokens += int(dict(provider.last_usage or {}).get("input_tokens") or 0)
            _upsert_embeddings(db, workspace_id, model_key, stale, vectors, existing)
            db.commit()
            for record, vector in zip(stale, vectors):
                fresh[record.id] = vector
        query_vector = provider.embed([query_snippet])[0]
        used_tokens += int(dict(provider.last_usage or {}).get("input_tokens") or 0)
        billing.record_usage(
            quote,
            input_tokens=used_tokens,
            output_tokens=0,
            attempt=1,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            usage_reported=used_tokens > 0,
        )
        db.commit()
        weight = float(embedding_cfg.get("semantic_weight") or 0.8)
        boosts: dict[str, float] = {}
        for memory_id, vector in fresh.items():
            similarity = _cosine(query_vector, vector)
            if similarity > 0:
                boosts[memory_id] = weight * similarity
        return boosts
    except Exception:
        logger.warning(
            "Semantic memory recall degraded to heuristic scoring for workspace %s",
            workspace_id,
            exc_info=True,
        )
        return {}


def semantic_boosts_for_documents(
    db: Session,
    workspace_id: str,
    settings: Settings,
    query_text: str,
    documents: list[MemorySearchDocument],
    config: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Embedding boost for v2 MemorySearchDocument rows.

    This mirrors ``semantic_boosts_for_records`` but reads the document
    projection directly. Any provider failure or missing configuration degrades
    to lexical retrieval; it never raises into the chat hot path.
    """
    query = (query_text or "").strip()
    if not query or not documents:
        return {}
    try:
        config = config or load_enhancement_config(db, workspace_id)
        embedding_cfg = config["embedding"]
        if not embedding_cfg["enabled"]:
            return {}
        provider = embedding_provider_for_workspace(
            db,
            workspace_id,
            settings,
            provider_id=embedding_cfg["provider_id"],
            model_id=embedding_cfg["model_id"],
        )
        if provider is None:
            return {}
        model_key = _model_key(provider.provider_id, provider.model_id)
        ids = [document.target_id for document in documents]
        existing = _embedding_rows(db, workspace_id, model_key, ids)
        fresh: dict[str, list[float]] = {}
        stale: list[MemorySearchDocument] = []
        for document in documents:
            row = existing.get(document.target_id)
            if row is not None and row.vector and row.content_hash == document.content_hash:
                fresh[document.target_id] = list(row.vector)
            else:
                stale.append(document)
        stale = stale[:_BACKFILL_PER_CALL]
        if not fresh and not stale:
            return {}
        query_snippet = query[:_QUERY_CHAR_CAP]
        billing = BillingService(db, workspace_id, "system:memory-embedding")
        try:
            quote = billing.preflight_model_call(
                provider_id=provider.provider_id,
                model_id=provider.model_id,
                feature="memory_embedding",
                estimated_input_tokens=estimate_tokens(query_snippet)
                + sum(
                    estimate_tokens(f"{doc.subject}\n{doc.content}")
                    for doc in stale
                ),
                estimated_output_tokens=0,
                remote_capability=True,
            )
        except AppError:
            return {}
        started_at = time.monotonic()
        used_tokens = 0
        if stale:
            vectors = provider.embed(
                [f"{doc.subject}\n{doc.content}"[:_MEMORY_TEXT_CHAR_CAP] for doc in stale]
            )
            used_tokens += int(dict(provider.last_usage or {}).get("input_tokens") or 0)
            for document, vector in zip(stale, vectors):
                row = existing.get(document.target_id)
                if row is None:
                    row = MemoryEmbedding(
                        workspace_id=workspace_id,
                        memory_id=document.target_id,
                        model_key=model_key,
                    )
                    db.add(row)
                    existing[document.target_id] = row
                row.content_hash = document.content_hash
                row.dim = len(vector)
                row.vector = vector
                fresh[document.target_id] = vector
            db.commit()
        query_vector = provider.embed([query_snippet])[0]
        used_tokens += int(dict(provider.last_usage or {}).get("input_tokens") or 0)
        billing.record_usage(
            quote,
            input_tokens=used_tokens,
            output_tokens=0,
            attempt=1,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            usage_reported=used_tokens > 0,
        )
        db.commit()
        weight = float(embedding_cfg.get("semantic_weight") or 0.8)
        boosts: dict[str, float] = {}
        for document_id, vector in fresh.items():
            similarity = _cosine(query_vector, vector)
            if similarity > 0:
                boosts[document_id] = weight * similarity
        return boosts
    except Exception:
        logger.warning(
            "Semantic v2 recall degraded to lexical scoring for workspace %s",
            workspace_id,
            exc_info=True,
        )
        return {}


def reindex_memory_embeddings(
    db: Session,
    workspace_id: str,
    settings: Settings,
    *,
    prune_stale: bool = False,
) -> dict[str, Any]:
    """Embed every active memory under the configured model (best-effort).

    Batches over *all* active memories (each batch up to ``_REINDEX_CAP``
    rows) so a workspace larger than the old single-pass cap converges in one
    call. When ``prune_stale`` is set, vector rows for embedding models other
    than the currently configured one are deleted afterwards — use this after
    a provider/model switch to reclaim the previous model's cache.
    """

    config = load_enhancement_config(db, workspace_id)
    embedding_cfg = config["embedding"]
    if not embedding_cfg["enabled"]:
        raise AppError(409, "memory_embedding_disabled", "Embedding enhancement is not enabled")
    provider = embedding_provider_for_workspace(
        db,
        workspace_id,
        settings,
        provider_id=embedding_cfg["provider_id"],
        model_id=embedding_cfg["model_id"],
    )
    if provider is None:
        raise AppError(
            409,
            "memory_embedding_provider_unavailable",
            "The configured embedding provider/model cannot be constructed",
        )
    model_key = _model_key(provider.provider_id, provider.model_id)
    records = list(
        db.scalars(
            select(MemoryRecord)
            .where(
                MemoryRecord.workspace_id == workspace_id,
                MemoryRecord.state == "active",
            )
            .order_by(MemoryRecord.updated_at.desc())
        ).all()
    )
    embedded = 0
    already_indexed = 0
    truncated = False
    for start in range(0, len(records), _REINDEX_CAP):
        if start >= _REINDEX_TOTAL_CAP:
            truncated = True
            break
        batch = records[start : start + _REINDEX_CAP]
        existing = _embedding_rows(db, workspace_id, model_key, [r.id for r in batch])
        pending = [
            record
            for record in batch
            if (
                (row := existing.get(record.id)) is None
                or not row.vector
                or row.content_hash != record.content_hash
            )
        ]
        if not pending:
            already_indexed += len(batch)
            continue
        texts = _memory_texts(db, workspace_id, pending)
        billing = BillingService(db, workspace_id, "system:memory-embedding")
        quote = billing.preflight_model_call(
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            feature="memory_embedding",
            estimated_input_tokens=sum(estimate_tokens(text) for text in texts.values()),
            estimated_output_tokens=0,
            remote_capability=True,
        )
        started_at = time.monotonic()
        try:
            vectors = provider.embed([texts[record.id] for record in pending])
        except Exception as exc:
            raise AppError(
                502,
                "memory_embedding_failed",
                f"Embedding request failed: {exc}",
            ) from exc
        used_tokens = int(dict(provider.last_usage or {}).get("input_tokens") or 0)
        billing.record_usage(
            quote,
            input_tokens=used_tokens,
            output_tokens=0,
            attempt=1,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            usage_reported=used_tokens > 0,
        )
        _upsert_embeddings(db, workspace_id, model_key, pending, vectors, existing)
        db.commit()
        embedded += len(pending)
        already_indexed += len(batch) - len(pending)
    stale_freed = 0
    if prune_stale:
        stale_freed = prune_stale_embeddings(db, workspace_id).get("freed_count", 0)
    return {
        "model_key": model_key,
        "total_active": len(records),
        "embedded": embedded,
        "already_indexed": already_indexed,
        "stale_freed": stale_freed,
        "truncated": truncated,
    }


def prune_stale_embeddings(
    db: Session, workspace_id: str, *, keep_model_key: str | None = None
) -> dict[str, Any]:
    """Delete cached vectors for embedding models other than the configured one.

    Embedding rows are projections, never fact sources — pruning only forces
    a lazy re-embed under the active model later. Returns how many rows were
    freed.
    """

    config = load_enhancement_config(db, workspace_id)
    embedding_cfg = config["embedding"]
    if (
        keep_model_key is None
        and embedding_cfg["provider_id"]
        and embedding_cfg["model_id"]
    ):
        keep_model_key = _model_key(
            embedding_cfg["provider_id"], embedding_cfg["model_id"]
        )
    statement = delete(MemoryEmbedding).where(
        MemoryEmbedding.workspace_id == workspace_id
    )
    if keep_model_key:
        statement = statement.where(MemoryEmbedding.model_key != keep_model_key)
    freed = db.execute(statement).rowcount or 0
    db.commit()
    return {"freed_count": freed, "keep_model_key": keep_model_key}


def embedding_index_status(db: Session, workspace_id: str) -> dict[str, Any]:
    config = load_enhancement_config(db, workspace_id)
    embedding_cfg = config["embedding"]
    active_total = len(
        db.scalars(
            select(MemoryRecord.id).where(
                MemoryRecord.workspace_id == workspace_id,
                MemoryRecord.state == "active",
            )
        ).all()
    )
    current_key: str | None = None
    indexed = 0
    if embedding_cfg["provider_id"] and embedding_cfg["model_id"]:
        current_key = _model_key(embedding_cfg["provider_id"], embedding_cfg["model_id"])
        indexed = len(
            db.scalars(
                select(MemoryEmbedding.id).where(
                    MemoryEmbedding.workspace_id == workspace_id,
                    MemoryEmbedding.model_key == current_key,
                )
            ).all()
        )
    counts = {
        str(row[0]): int(row[1])
        for row in db.execute(
            select(
                MemoryEmbedding.model_key,
                sql_func.count(MemoryEmbedding.id),
            )
            .where(MemoryEmbedding.workspace_id == workspace_id)
            .group_by(MemoryEmbedding.model_key)
        ).all()
    }
    stale_model_keys = [
        {"model_key": key, "indexed_count": count}
        for key, count in sorted(counts.items())
        if key != current_key
    ]
    return {
        "active_memories": active_total,
        "indexed_memories": indexed,
        "current_model_key": current_key,
        "stale_model_keys": stale_model_keys,
    }


# ---------------------------------------------------------------------------
# Auto extraction (background "dreaming")
# ---------------------------------------------------------------------------


def _extraction_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "has_more": {
                "type": "boolean",
                "description": "仍有下一批消息值得分析时为 true；没有更多有意义内容时为 false。",
            },
            "memories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": [
                                "CREATE",
                                "UPDATE",
                                "CORRECT",
                                "CONFIRM",
                                "COMPLETE",
                                "CANCEL",
                                "RESCHEDULE",
                                "SUPERSEDE",
                                "RETRACT",
                                "NOOP",
                            ],
                        },
                        "target_memory_id": {"type": "string"},
                        "memory_type": {
                            "type": "string",
                            "enum": list(EXTRACTABLE_MEMORY_TYPES),
                        },
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "atom_kind": {"type": "string"},
                        "canonical_key": {"type": "string"},
                        "temporal_status": {"type": "string"},
                        "time_expression": {"type": "string"},
                        "original_expression": {"type": "string"},
                        "timezone_name": {"type": "string"},
                        "granularity_hint": {"type": "string"},
                        "summary_eligibility": {"type": "string"},
                        "event_at": {"type": ["string", "null"]},
                        "valid_from": {"type": ["string", "null"]},
                        "valid_until": {"type": ["string", "null"]},
                        "next_review_at": {"type": ["string", "null"]},
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "confidence": {"type": "number"},
                        "importance": {"type": "number"},
                    },
                    "required": [
                        "operation",
                        "memory_type",
                        "title",
                        "content",
                        "atom_kind",
                        "canonical_key",
                        "temporal_status",
                        "summary_eligibility",
                        "evidence_ids",
                        "confidence",
                    ],
                },
            }
        },
        "required": ["has_more", "memories"],
    }


def _type_catalog_lines() -> str:
    lines = []
    for name in EXTRACTABLE_MEMORY_TYPES:
        item = MEMORY_TYPE_REGISTRY[name]
        lines.append(f"- {name}: {item.description}")
    return "\n".join(lines)


def _extraction_prompt(
    transcript: str, existing_lines: str, session: ChatSession
) -> str:
    scope_hint = "该会话关联了一个学习目标。" if session.goal_id else "该会话没有关联学习目标。"
    return (
        "你是 LearnGraph 的原子记忆整理器。输入只包含经过来源过滤的用户陈述；"
        "助手回答、文件原文、网页和工具结果没有资格出现在这里。请把用户陈述"
        "提炼为最小、规范化、可验证的原子，而不是复制聊天原文。\n\n"
        "只抽取对未来教学持续有用的稳定信息：学习偏好、稳定的个人事实、"
        "暴露出的错误概念、被验证有效/无效的教学策略、重要决定、阶段性事件总结。\n"
        "不要抽取：一次性的问答内容、当前任务细节、上传/解析文件这个动作、文件中的"
        "人物事实、可以从图谱/掌握度中查到的权威状态（掌握分数、路线版本、文件路径等）、"
        "以及任何未经用户明确表达的臆测。一次事件绝不能推断成习惯。\n"
        f"可信当前时间：{utc_now().isoformat()}；默认时区：Asia/Shanghai。"
        "识别相对时间时，把用户陈述中的时间表达原样放入 time_expression（"
        "例如“下个月”“明天下午”“每周五”），不要自行换算成绝对时间；"
        "程序会用消息发生时间和用户时区确定性规范化。"
        "计划过去不代表已经完成；取消用 CANCEL，"
        "改期用 RESCHEDULE，没有足够证据用 NOOP。\n\n"
        f"可用的记忆类型：\n{_type_catalog_lines()}\n\n"
        f"{scope_hint}\n\n"
        f"已有记忆（避免重复；如需修正请用 operation=UPDATE 并给出 target_memory_id）：\n"
        f"{existing_lines or '（无）'}\n\n"
        f"<eligible_user_evidence>\n{transcript}\n</eligible_user_evidence>\n\n"
        "本次只处理上述证据。只输出真正值得跨会话保留的原子记忆；不要为了凑数而创造记忆。"
        "如果证据中没有更多值得保存的内容，has_more=false；如果仍有后续证据值得继续分析，has_more=true。\n"
        f"要求：最多提出 {_EXTRACTION_MAX_PROPOSALS} 条；标题不超过 60 字；"
        "内容用中文、单条不超过 300 字；confidence 取 0-1 并保守估计"
        "（仅当用户明确表达时才 >= 0.75）；每条非 NOOP 原子必须引用输入中的"
        " evidence_id；没有值得记住的内容时返回空数组。"
    )


def _new_messages_since(
    db: Session,
    workspace_id: str,
    session_id: str,
    state: MemoryExtractionState | None,
) -> list[Message]:
    statement = (
        select(Message)
        .where(
            Message.workspace_id == workspace_id,
            Message.session_id == session_id,
            Message.role == "user",
            Message.status == "completed",
        )
        .order_by(Message.created_at, Message.id)
    )
    messages = [item for item in db.scalars(statement).all() if (item.content or "").strip()]
    if state is not None and state.last_message_id:
        cutoff = next(
            (index for index, item in enumerate(messages) if item.id == state.last_message_id),
            None,
        )
        if cutoff is not None:
            messages = messages[cutoff + 1 :]
        elif state.last_message_at is not None:
            last_at = state.last_message_at
            messages = [
                item
                for item in messages
                if item.created_at.replace(tzinfo=last_at.tzinfo) > last_at
            ]
    return messages[-_TRANSCRIPT_MESSAGE_CAP:]


def _user_evidence_for_messages(
    db: Session,
    workspace_id: str,
    messages: list[Message],
) -> dict[str, MemoryEvidence]:
    by_message: dict[str, MemoryEvidence] = {}
    for message in messages:
        evidence = db.scalar(
            select(MemoryEvidence).where(
                MemoryEvidence.workspace_id == workspace_id,
                MemoryEvidence.source_kind == "user_statement",
                MemoryEvidence.source_id == message.id,
                MemoryEvidence.deleted_at.is_(None),
            )
        )
        if evidence is None:
            content = (message.content or "").strip()
            evidence = MemoryEvidence(
                workspace_id=workspace_id,
                source_kind="user_statement",
                source_id=message.id,
                message_id=message.id,
                authorship="user",
                observed_at=message.created_at,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                excerpt=content[:1_200],
                profile_eligible=True,
                eligibility_reason="direct_user_message",
            )
            db.add(evidence)
            db.flush()
        by_message[message.id] = evidence
    return by_message


def _existing_memory_context(
    db: Session, workspace_id: str, session_id: str
) -> tuple[list[MemoryRecord], str]:
    records = list(
        db.scalars(
            select(MemoryRecord)
            .where(
                MemoryRecord.workspace_id == workspace_id,
                MemoryRecord.state == "active",
                (MemoryRecord.namespace == "workspace")
                | (
                    (MemoryRecord.namespace == "session")
                    & (MemoryRecord.session_id == session_id)
                ),
            )
            .order_by(MemoryRecord.updated_at.desc())
            .limit(_EXISTING_MEMORY_CONTEXT_CAP)
        ).all()
    )
    lines = "\n".join(
        (
            f"- id={record.id} type={record.record_kind} "
            f"atom_kind={getattr(record, 'atom_kind', 'fact')} "
            f"canonical_key={getattr(record, 'canonical_key', '') or '-'} "
            f"ledger={getattr(record, 'ledger_status', 'active')} "
            f"temporal={getattr(record, 'temporal_status', 'timeless')} "
            f"title={record.title}"
        )
        for record in records
    )
    return records, lines


def _proposal_scope(memory_type: str, session: ChatSession) -> str:
    """Coerce the registry default scope to what this session can satisfy.

    Extraction has no reliable node context, so node-scoped defaults (for
    example misconception) land on the session's goal when one exists.
    """

    default_scope = get_memory_type(memory_type).default_scope
    if default_scope in {"goal", "node"}:
        return "goal" if session.goal_id else "workspace"
    if default_scope == "session":
        return "session"
    return "workspace"


def extract_session_memories(
    db: Session,
    workspace: Workspace,
    session_id: str,
    settings: Settings,
    *,
    actor_id: str = "system:memory-extraction",
    force: bool = False,
) -> dict[str, Any]:
    """Run one extraction pass for a session and route results through drafts."""

    from app.services.memory import MemoryService

    config = load_enhancement_config(db, workspace.id)
    extraction_cfg = config["extraction"]
    if not extraction_cfg["enabled"] and not force:
        return {"status": "disabled", "drafts_created": 0}
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.workspace_id == workspace.id,
            ChatSession.id == session_id,
        )
    )
    if session is None:
        raise AppError(404, "session_not_found", "session not found")
    if (
        not _workspace_memory_enabled(db, workspace.id)
        or not session.memory_enabled
    ):
        if force:
            policy = MemoryService(
                db,
                workspace,
                actor_id,
                memory_provider_for_workspace(db, workspace, actor_id, settings),
                settings.memory_root,
            ).policy(session_id)
            blockers = [
                name
                for name, enabled in (
                    ("workspace_enabled", policy.workspace_enabled),
                    ("workspace_learning_enabled", policy.workspace_learning_enabled),
                    ("session_enabled", policy.session_enabled),
                    ("session_learning_enabled", policy.session_learning_enabled),
                )
                if not enabled
            ]
            raise AppError(
                409,
                "memory_policy_disabled",
                "Enable workspace and session learning before extraction",
                {"blockers": blockers, "policy": policy.model_dump()},
            )
        return {"status": "policy_disabled", "drafts_created": 0}

    state = db.scalar(
        select(MemoryExtractionState).where(
            MemoryExtractionState.workspace_id == workspace.id,
            MemoryExtractionState.session_id == session_id,
        )
    )
    messages = _new_messages_since(db, workspace.id, session_id, state)
    if not messages:
        return {"status": "no_new_messages", "drafts_created": 0}

    if state is None:
        state = MemoryExtractionState(workspace_id=workspace.id, session_id=session_id)
        db.add(state)
    now = utc_now()
    state.last_run_at = now

    def _persist_extraction_state() -> None:
        # Re-attach the bookmark after a locked-commit rollback and re-apply
        # the same values so the retried commit writes them again.
        db.add(state)
        state.last_run_at = now

    # Persist the sweep bookmark BEFORE any remote model call.  Keeping a dirty
    # ORM session across a long generation holds the single SQLite write lock
    # for the whole call and starves every other writer (other scheduler
    # sweeps, chat commits), which then fail with "database is locked" after
    # the busy timeout.
    commit_with_locked_retry(db, redo=_persist_extraction_state)

    model = model_provider_for_workspace(
        db,
        workspace.id,
        settings,
        model_id=extraction_cfg["model_id"] or None,
        provider_id=extraction_cfg["provider_id"] or None,
    )
    if not getattr(model, "available", False):
        state.last_status = "provider_unavailable"
        commit_with_locked_retry(
            db,
            redo=lambda: setattr(state, "last_status", "provider_unavailable"),
        )
        if force:
            raise AppError(
                503,
                "memory_extraction_provider_unavailable",
                "The configured extraction model provider is unavailable",
            )
        return {"status": "provider_unavailable", "drafts_created": 0}

    evidence_by_message = _user_evidence_for_messages(
        db, workspace.id, messages
    )
    eligible_evidence_ids = {
        evidence.id for evidence in evidence_by_message.values()
    }
    transcript = "\n".join(
        (
            f"<evidence id=\"{evidence_by_message[item.id].id}\" "
            f"message_id=\"{item.id}\">"
            f"{item.content[:_TRANSCRIPT_CHARS_PER_MESSAGE]}"
            "</evidence>"
        )
        for item in messages
    )
    existing_records, existing_lines = _existing_memory_context(
        db, workspace.id, session_id
    )
    known_ids = {record.id for record in existing_records}
    known_hashes = {record.content_hash for record in existing_records}
    pending_titles = {
        (title or "").strip()
        for title in db.scalars(
            select(MemoryDraft.title).where(
                MemoryDraft.workspace_id == workspace.id,
                MemoryDraft.status == "PENDING",
            )
        ).all()
    }

    prompt = _extraction_prompt(transcript, existing_lines, session)
    billing = BillingService(db, workspace.id, actor_id)
    try:
        quote = billing.preflight_model_call(
            provider_id=model.provider_id,
            model_id=getattr(model, "model_id", extraction_cfg["model_id"]),
            feature="memory_extraction",
            estimated_input_tokens=estimate_tokens(prompt),
            estimated_output_tokens=1_024,
            remote_capability=bool(getattr(model, "remote_capability", True)),
        )
    except AppError as exc:
        state.last_status = "budget_blocked"
        state.last_error = str(getattr(exc, "message", exc))[:500]
        commit_with_locked_retry(
            db,
            redo=lambda: (
                setattr(state, "last_status", "budget_blocked"),
                setattr(state, "last_error", str(getattr(exc, "message", exc))[:500]),
            ),
        )
        if force:
            raise
        return {"status": "budget_blocked", "drafts_created": 0}
    started_at = time.monotonic()
    call_error: Exception | None = None
    payload: dict[str, Any] = {}
    try:
        payload = model.generate_json(prompt, "memory_extraction", _extraction_schema())
    except Exception as exc:  # noqa: BLE001 — mapped below after usage recording
        call_error = exc
    usage = dict(getattr(model, "last_usage", {}) or {})
    billing.record_usage(
        quote,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
        cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
        attempt=1,
        latency_ms=int((time.monotonic() - started_at) * 1000),
        usage_reported=bool(usage),
    )
    if call_error is not None:
        state.last_status = "model_error"
        state.last_error = str(call_error)[:500]
        commit_with_locked_retry(
            db,
            redo=lambda: (
                setattr(state, "last_status", "model_error"),
                setattr(state, "last_error", str(call_error)[:500]),
            ),
        )
        if force:
            raise AppError(
                502,
                "memory_extraction_failed",
                f"Extraction model call failed: {call_error}",
            ) from call_error
        return {"status": "model_error", "drafts_created": 0}
    commit_with_locked_retry(db)

    memory_service = MemoryService(
        db,
        workspace,
        actor_id,
        memory_provider_for_workspace(db, workspace, actor_id, settings),
        settings.memory_root,
    )
    from app.services.memory import _content_hash

    proposals = payload.get("memories") if isinstance(payload, dict) else None
    drafts_created = 0
    auto_committed = 0
    skipped = 0
    observed_at = messages[-1].created_at if messages else utc_now()
    observed_by_evidence = {
        evidence.id: evidence.observed_at
        for evidence in evidence_by_message.values()
    }
    normalizer = TemporalNormalizer()
    for proposal in (proposals or [])[:_EXTRACTION_MAX_PROPOSALS]:
        if not isinstance(proposal, dict):
            continue
        memory_type = str(proposal.get("memory_type") or "").strip()
        title = str(proposal.get("title") or "").strip()[:240]
        content = str(proposal.get("content") or "").strip()[:4_000]
        if memory_type not in EXTRACTABLE_MEMORY_TYPES or not title or not content:
            skipped += 1
            continue
        try:
            confidence = min(1.0, max(0.0, float(proposal.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            importance = min(1.0, max(0.0, float(proposal.get("importance") or 0.5)))
        except (TypeError, ValueError):
            importance = 0.5
        operation = str(proposal.get("operation") or "CREATE").strip().upper()
        if operation == "NOOP":
            continue
        target_memory_id = str(proposal.get("target_memory_id") or "").strip() or None
        canonical_key = str(proposal.get("canonical_key") or "").strip() or None
        if operation != "CREATE":
            resolved_target = resolve_target_for_operation(
                operation,
                target_memory_id=target_memory_id,
                canonical_key=canonical_key,
                records=existing_records,
            )
            if resolved_target is None:
                # Never downgrade CANCEL/RESCHEDULE/COMPLETE into an orphan
                # CREATE. The proposal stays a NOOP until a target is clear.
                skipped += 1
                continue
            target_memory_id = resolved_target
            target_record = next(
                (record for record in existing_records if record.id == resolved_target),
                None,
            )
            canonical_key = canonical_key or str(
                getattr(target_record, "canonical_key", "") or ""
            ).strip() or None
        if operation == "CREATE":
            if _content_hash(title, content) in known_hashes or title in pending_titles:
                skipped += 1
                continue
        evidence_ids = list(
            dict.fromkeys(
                str(value)
                for value in (proposal.get("evidence_ids") or [])
                if str(value) in eligible_evidence_ids
            )
        )
        if not evidence_ids:
            skipped += 1
            continue
        raw_temporal_status = str(
            proposal.get("temporal_status") or "timeless"
        ).strip()
        summary_eligibility = str(
            proposal.get("summary_eligibility") or "durable"
        ).strip()
        event_at = proposal.get("event_at")
        valid_until = proposal.get("valid_until")
        next_review_at = proposal.get("next_review_at")
        observed_for_proposal = observed_at
        for evidence_id in evidence_ids:
            evidence_observed_at = observed_by_evidence.get(evidence_id)
            if evidence_observed_at is not None:
                observed_for_proposal = _as_utc(evidence_observed_at)
                break
        temporal_semantics: dict[str, Any] | None = None
        original_expression = str(
            proposal.get("time_expression") or proposal.get("original_expression") or ""
        ).strip()
        timezone_name = str(proposal.get("timezone_name") or "Asia/Shanghai").strip()
        if original_expression and raw_temporal_status in {
            "planned", "ongoing", "scheduled", "tentative", "rescheduled",
            "in_progress", "completed", "cancelled", "lapsed_unverified", "expired",
        }:
            semantics = normalizer.normalize(
                original_expression,
                observed_at=observed_for_proposal,
                timezone_name=timezone_name,
            )
            temporal_semantics = semantics.as_dict()
            if semantics.start_at is not None and event_at is None:
                event_at = semantics.start_at.isoformat()
            if semantics.end_at is not None and valid_until is None:
                valid_until = semantics.end_at.isoformat()
        temporal_anchor = (
            _parse_extraction_time(valid_until)
            or _parse_extraction_time(event_at)
        )
        temporal_status = normalize_plan_status(
            raw_temporal_status,
            start_at=temporal_anchor,
            now=_as_utc(observed_for_proposal),
        )
        if temporal_status == "lapsed_unverified":
            # A past plan is evidence that a plan existed, never evidence that
            # the event happened. Keep it out of the current profile until the
            # user confirms, cancels, or reschedules it.
            summary_eligibility = "historical"
            next_review_at = None
        elif temporal_status in PLAN_STATES and temporal_anchor is not None:
            if _parse_extraction_time(next_review_at) is None:
                next_review_at = (temporal_anchor + timedelta(days=1)).isoformat()
        structured_payload = {
            "atom_schema_version": 1,
            "atom_kind": str(proposal.get("atom_kind") or "fact")[:64],
            "canonical_key": canonical_key or "",
            "ledger_status": "active",
            "temporal_status": temporal_status,
            "summary_eligibility": summary_eligibility,
            "event_at": event_at,
            "valid_from": proposal.get("valid_from"),
            "valid_until": valid_until,
            "next_review_at": next_review_at,
            "last_verified_at": utc_now().isoformat(),
            "timezone_name": timezone_name,
            "evidence_ids": evidence_ids,
            "temporal_semantics": temporal_semantics,
            "provenance": {
                "authorship": "user",
                "source_kinds": ["user_statement"],
                "profile_eligible": True,
            },
        }
        structured_payload = ensure_plan_canonical_key(
            structured_payload, title=title
        )
        if operation == "RESCHEDULE":
            structured_payload["plan_change_text"] = plan_change_text("rescheduled")
        elif operation == "CANCEL":
            structured_payload["plan_change_text"] = plan_change_text("cancelled")
        elif operation == "COMPLETE":
            structured_payload["plan_change_text"] = plan_change_text("completed")
        if temporal_status == "rescheduled":
            structured_payload["plan_change_text"] = plan_change_text("rescheduled")
        try:
            draft = memory_service.create_draft(
                MemoryDraftCreateRequest(
                    operation=operation,
                    memory_type=memory_type,
                    target_memory_id=target_memory_id,
                    title=title,
                    content=content,
                    structured_payload=structured_payload,
                    proposed_scope_type=_proposal_scope(memory_type, session),
                    goal_id=session.goal_id,
                    session_id=session.id,
                    confidence=confidence,
                    importance=importance,
                    # UPDATE rewrites an existing memory: always human-reviewed.
                    auto_commit=bool(extraction_cfg["auto_commit"]),
                    created_by="memory_extraction",
                    source_refs=[
                        {
                            "type": "memory_evidence",
                            "id": evidence_id,
                            "source_kind": "user_statement",
                        }
                        for evidence_id in evidence_ids
                    ],
                )
            )
        except AppError as exc:
            logger.info(
                "Memory extraction proposal rejected for session %s: %s",
                session_id,
                exc.code if hasattr(exc, "code") else exc,
            )
            skipped += 1
            continue
        drafts_created += 1
        pending_titles.add(title)
        if draft.status == "COMMITTED":
            auto_committed += 1

    last = messages[-1]
    state.last_message_id = last.id
    state.last_message_at = last.created_at
    state.last_status = "ok" if drafts_created else "empty"
    state.last_error = ""
    state.extracted_count = int(state.extracted_count or 0) + drafts_created
    db.commit()
    return {
        "status": "ok",
        "session_id": session_id,
        "messages_reviewed": len(messages),
        "drafts_created": drafts_created,
        "auto_committed": auto_committed,
        "skipped": skipped,
        "completion_reason": "model_completed" if not bool(payload.get("has_more")) else "batch_safety_limit",
    }


# ---------------------------------------------------------------------------
# Background rolling session summaries (ContextSummary kind='model')
# ---------------------------------------------------------------------------

_SUMMARY_MIN_HISTORY_TOKENS = 6_000
_SUMMARY_KEEP_RECENT_MESSAGES = 12
_SUMMARY_MIN_NEW_MESSAGES = 6
_SUMMARY_MESSAGE_CHAR_CAP = 1_200
_SUMMARY_BATCH_MESSAGE_CAP = 60
_SUMMARY_TEXT_CHAR_CAP = 6_000


def _summarization_model_selection(config: dict[str, Any]) -> tuple[str, str]:
    """Summarization model, falling back to the extraction model when unset."""

    cfg = config["summarization"]
    provider_id = cfg["provider_id"] or config["extraction"]["provider_id"]
    model_id = cfg["model_id"] or config["extraction"]["model_id"]
    return provider_id, model_id


def _summary_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }


def _summary_prompt(base_summary: str, transcript: str) -> str:
    base_block = (
        f"已有的早期会话摘要（在此基础上滚动合并，不要丢失其中的关键事实）：\n{base_summary}\n\n"
        if base_summary
        else ""
    )
    return (
        "你是 LearnGraph 的会话摘要器。请把下面这段学习对话压缩成一份可复用的会话摘要，"
        "供后续对话作为上下文注入。\n\n"
        f"{base_block}"
        f"新增对话内容：\n{transcript}\n\n"
        "要求：用中文；保留结论、约定、用户偏好、已解决与未解决的问题、关键实体和数字；"
        "省略寒暄和重复；按主题组织成条目；总长不超过 600 字。"
        "输出合并后的完整摘要（覆盖已有摘要 + 新增内容）。"
    )


def summarize_session_context(
    db: Session,
    workspace: Workspace,
    session_id: str,
    settings: Settings,
    *,
    actor_id: str = "system:context-summary",
    force: bool = False,
) -> dict[str, Any]:
    """Roll the session's older turns into an LLM ContextSummary (kind='model').

    Chat compaction then reuses the covered prefix verbatim and only falls
    back to mechanical truncation for uncovered messages. Runs off the hot
    path; a session below the size threshold is skipped cheaply.
    """

    config = load_enhancement_config(db, workspace.id)
    if not config["summarization"]["enabled"] and not force:
        return {"status": "disabled"}
    provider_id, model_id = _summarization_model_selection(config)
    if not provider_id or not model_id:
        if force:
            raise AppError(
                409,
                "context_summarization_unconfigured",
                "Configure a summarization (or extraction) provider and model first",
            )
        return {"status": "unconfigured"}
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.workspace_id == workspace.id,
            ChatSession.id == session_id,
        )
    )
    if session is None:
        raise AppError(404, "session_not_found", "session not found")

    messages = [
        item
        for item in db.scalars(
            select(Message)
            .where(
                Message.workspace_id == workspace.id,
                Message.session_id == session_id,
                Message.role.in_(["user", "assistant"]),
                Message.status == "completed",
            )
            .order_by(Message.created_at, Message.id)
        ).all()
        if (item.content or "").strip()
    ]
    if len(messages) <= _SUMMARY_KEEP_RECENT_MESSAGES and not force:
        return {"status": "too_short"}
    total_tokens = sum(estimate_tokens(item.content) for item in messages)
    if total_tokens < _SUMMARY_MIN_HISTORY_TOKENS and not force:
        return {"status": "below_threshold"}

    prefix = messages[:-_SUMMARY_KEEP_RECENT_MESSAGES]
    if force and not prefix:
        prefix = messages
    prefix_ids = [item.id for item in prefix]
    latest = db.scalar(
        select(ContextSummary)
        .where(
            ContextSummary.workspace_id == workspace.id,
            ContextSummary.session_id == session_id,
            ContextSummary.kind == "model",
        )
        .order_by(ContextSummary.version.desc())
        .limit(1)
    )
    covered = set(latest.source_message_ids or []) if latest is not None else set()
    # Rolling base only stays valid while it covers a subset of the prefix
    # (edits/branch switches can orphan old ids — then start fresh).
    base_summary = ""
    if latest is not None and covered and covered.issubset(set(prefix_ids)):
        base_summary = latest.summary
    else:
        covered = set()
    fresh_messages = [item for item in prefix if item.id not in covered]
    if not fresh_messages:
        return {"status": "fresh"}
    if len(fresh_messages) < _SUMMARY_MIN_NEW_MESSAGES and base_summary and not force:
        return {"status": "fresh_enough"}
    fresh_messages = fresh_messages[-_SUMMARY_BATCH_MESSAGE_CAP:]

    model = model_provider_for_workspace(
        db, workspace.id, settings, model_id=model_id, provider_id=provider_id
    )
    if not getattr(model, "available", False):
        if force:
            raise AppError(
                503,
                "context_summarization_provider_unavailable",
                "The configured summarization model provider is unavailable",
            )
        return {"status": "provider_unavailable"}
    transcript = "\n".join(
        f"[{item.role}] {item.content[:_SUMMARY_MESSAGE_CHAR_CAP]}"
        for item in fresh_messages
    )
    prompt = _summary_prompt(base_summary, transcript)
    billing = BillingService(db, workspace.id, actor_id)
    try:
        quote = billing.preflight_model_call(
            provider_id=model.provider_id,
            model_id=getattr(model, "model_id", model_id),
            feature="context_summarization",
            estimated_input_tokens=estimate_tokens(prompt),
            estimated_output_tokens=1_024,
            remote_capability=bool(getattr(model, "remote_capability", True)),
        )
    except AppError:
        if force:
            raise
        return {"status": "budget_blocked"}
    started_at = time.monotonic()
    call_error: Exception | None = None
    payload: dict[str, Any] = {}
    try:
        payload = model.generate_json(prompt, "context_summary", _summary_schema())
    except Exception as exc:  # noqa: BLE001 — surfaced after usage recording
        call_error = exc
    usage = dict(getattr(model, "last_usage", {}) or {})
    billing.record_usage(
        quote,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
        cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
        attempt=1,
        latency_ms=int((time.monotonic() - started_at) * 1000),
        usage_reported=bool(usage),
    )
    if call_error is not None:
        db.commit()
        if force:
            raise AppError(
                502,
                "context_summarization_failed",
                f"Summarization model call failed: {call_error}",
            ) from call_error
        return {"status": "model_error"}
    summary_text = str(payload.get("summary") or "").strip()[:_SUMMARY_TEXT_CHAR_CAP]
    if not summary_text:
        db.commit()
        return {"status": "model_empty"}

    new_covered_ids = [
        item.id for item in prefix if item.id in covered or item in fresh_messages
    ]
    version = (
        db.scalar(
            select(sql_func.max(ContextSummary.version)).where(
                ContextSummary.workspace_id == workspace.id,
                ContextSummary.session_id == session_id,
            )
        )
        or 0
    ) + 1
    covered_messages = [item for item in prefix if item.id in set(new_covered_ids)]
    source_hash = hashlib.sha256(
        "\n".join(f"{item.id}:{item.content}" for item in covered_messages).encode("utf-8")
    ).hexdigest()
    summary = ContextSummary(
        workspace_id=workspace.id,
        session_id=session_id,
        version=version,
        kind="model",
        source_message_ids=new_covered_ids,
        source_hash=source_hash,
        summary=summary_text,
        estimated_tokens_before=sum(
            estimate_tokens(item.content) for item in covered_messages
        ),
        estimated_tokens_after=estimate_tokens(summary_text),
    )
    db.add(summary)
    db.flush()
    db.commit()
    return {
        "status": "ok",
        "session_id": session_id,
        "version": version,
        "covered_messages": len(new_covered_ids),
        "newly_summarized": len(fresh_messages),
        "summary": {
            "id": summary.id,
            "session_id": summary.session_id,
            "version": summary.version,
            "kind": summary.kind,
            "source_message_ids": summary.source_message_ids,
            "summary": summary.summary,
            "estimated_tokens_before": summary.estimated_tokens_before,
            "estimated_tokens_after": summary.estimated_tokens_after,
            "created_at": summary.created_at,
        },
    }


def run_workspace_summarization_sweep(
    db: Session,
    workspace: Workspace,
    settings: Settings,
    *,
    idle_seconds: int,
    sessions_per_sweep: int,
) -> dict[str, int]:
    totals = {"sessions_summarized": 0}
    config = load_enhancement_config(db, workspace.id)
    if not config["summarization"]["enabled"]:
        return totals
    provider_id, model_id = _summarization_model_selection(config)
    if not provider_id or not model_id:
        return totals
    quiet_before = utc_now() - timedelta(seconds=max(30, idle_seconds))
    candidates = list(
        db.scalars(
            select(ChatSession)
            .where(
                ChatSession.workspace_id == workspace.id,
                ChatSession.updated_at < quiet_before,
            )
            .order_by(ChatSession.updated_at.desc())
            .limit(25)
        ).all()
    )
    for session in candidates:
        if totals["sessions_summarized"] >= max(1, sessions_per_sweep):
            break
        try:
            result = summarize_session_context(db, workspace, session.id, settings)
        except Exception:
            logger.exception(
                "Context summarization failed for session %s in workspace %s",
                session.id,
                workspace.id,
            )
            db.rollback()
            continue
        if result.get("status") == "ok":
            totals["sessions_summarized"] += 1
    return totals


def run_workspace_extraction_sweep(
    db: Session,
    workspace: Workspace,
    settings: Settings,
    *,
    idle_seconds: int,
    sessions_per_sweep: int,
) -> dict[str, int]:
    """Extract from quiet sessions with unprocessed turns (dreaming-style)."""

    totals = {"sessions_processed": 0, "drafts_created": 0, "auto_committed": 0}
    config = load_enhancement_config(db, workspace.id)
    extraction_cfg = config["extraction"]
    if (
        not extraction_cfg["enabled"]
        or not _workspace_memory_enabled(db, workspace.id)
    ):
        return totals
    quiet_before = utc_now() - timedelta(seconds=max(30, idle_seconds))
    candidates = list(
        db.scalars(
            select(ChatSession)
            .where(
                ChatSession.workspace_id == workspace.id,
                ChatSession.memory_enabled.is_(True),
                ChatSession.updated_at < quiet_before,
            )
            .order_by(ChatSession.updated_at.desc())
            .limit(25)
        ).all()
    )
    for session in candidates:
        if totals["sessions_processed"] >= max(1, sessions_per_sweep):
            break
        try:
            result = extract_session_memories(
                db, workspace, session.id, settings, force=False
            )
        except Exception:
            logger.exception(
                "Memory extraction failed for session %s in workspace %s",
                session.id,
                workspace.id,
            )
            db.rollback()
            continue
        if result.get("status") in {"ok"}:
            totals["sessions_processed"] += 1
            totals["drafts_created"] += int(result.get("drafts_created") or 0)
            totals["auto_committed"] += int(result.get("auto_committed") or 0)
    return totals
