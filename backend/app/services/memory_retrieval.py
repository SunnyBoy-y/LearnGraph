from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select, text
from app.domain.models import MemoryRecord
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.memory_event_models import MemoryScopeContext, MemorySearchDocument
from app.services.memory_enhancement import (
    load_enhancement_config,
    semantic_boosts_for_documents,
)
from app.services.memory_projector import (
    ensure_memory_search_fts,
    normalize_bm25_score,
    probe_memory_search_fts_capability,
)


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    document_id: str
    target_type: str
    target_id: str
    title: str
    content: str
    source_event_id: str
    status: str
    sensitivity: str
    confidence: float
    importance: float
    score: float
    component_scores: dict[str, float] = field(default_factory=dict)
    retrieval_reason: str = "hybrid_memory_search"
    external_source: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    candidates: tuple[RetrievalCandidate, ...]
    excluded: dict[str, int]
    degraded_modes: tuple[str, ...] = ()


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[\w㐀-鿿]{2,}", value, flags=re.UNICODE)
    }


class MemoryHybridRetriever:
    """Scope-first structured + FTS retrieval with best-effort degradation."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def search(
        self,
        scope: MemoryScopeContext,
        query: str,
        *,
        allowed_layers: frozenset[str] = frozenset({"L1", "L2", "L3", "L4", "L5", "L6"}),
        top_k: int = 6,
        min_score: float = 0.20,
        min_confidence: float = 0.0,
    ) -> RetrievalResult:
        excluded = {
            "out_of_scope": 0,
            "sensitivity": 0,
            "lifecycle": 0,
            "quality": 0,
            "duplicate": 0,
            "expired": 0,
        }
        query_tokens = _tokens(query)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # ── Hard scope filters ──────────────────────────────────────────────
        workspace_scope = (
            (MemorySearchDocument.workspace_id == scope.workspace_id)
            | (
                MemorySearchDocument.workspace_id.is_(None)
                & (MemorySearchDocument.subject_user_id == scope.principal_user_id)
            )
        )
        if scope.cross_workspace_authorized:
            workspace_scope = workspace_scope | (
                MemorySearchDocument.subject_user_id == scope.principal_user_id
            )
        hard_filters = (
            MemorySearchDocument.tenant_id == scope.tenant_id,
            MemorySearchDocument.memory_layer.in_(allowed_layers),
            MemorySearchDocument.status == "active",
            workspace_scope,
            (MemorySearchDocument.subject_user_id == scope.principal_user_id)
            | (MemorySearchDocument.subject_user_id.is_(None)),
            MemorySearchDocument.sensitivity.in_(scope.allowed_sensitivity),
        )
        if top_k <= 0:
            return RetrievalResult((), excluded)
        fts_scores = self._fts_scores(scope, query, limit=max(top_k * 5, 20))
        D, R = MemorySearchDocument, MemoryRecord
        canonical_ok = and_(
            R.tenant_id == scope.tenant_id,
            R.workspace_id.is_not_distinct_from(D.workspace_id),
            or_(R.subject_user_id.is_(None), R.subject_user_id == scope.principal_user_id),
            R.state == "active", R.lifecycle_status == "active", R.ledger_status == "active",
            R.zone != "archive", R.deleted_at.is_(None), R.content_destroyed_at.is_(None),
            R.auto_recall_suppressed.is_(False),
            R.sensitivity.in_(scope.allowed_sensitivity), R.memory_layer.in_(allowed_layers),
            or_(R.atom_schema_version < 1, R.summary_eligibility.in_(("durable", "current"))),
            R.temporal_status.not_in(("cancelled", "rescheduled", "lapsed_unverified", "expired")),
            or_(R.valid_until.is_(None), R.valid_until >= now),
        )
        statement = (select(D).outerjoin(R, and_(D.target_type == "memory", R.id == D.target_id))
            .where(*hard_filters, or_(R.id.is_(None), canonical_ok),
                   or_(D.valid_until.is_(None), D.valid_until >= now), D.confidence >= min_confidence)
            .order_by(D.id).execution_options(yield_per=128))
        # Bound memory/embedding cost AFTER every eligible document can compete.
        pool_limit = max(top_k, min(200, max(24, top_k * 8)))
        pool = {}
        stream = self.db.scalars(statement)
        try:
            for document in stream:
                tokens = _tokens(f"{document.subject} {document.content}")
                lexical = len(query_tokens & tokens) / math.sqrt(len(query_tokens) * len(tokens)) if query_tokens and tokens else 0.0
                lexical = max(lexical, fts_scores.get(document.id, 0.0))
                entity = float(bool(query_tokens & _tokens(document.entity_aliases_text)))
                scope_score = 1.0 if document.task_id and document.task_id == scope.task_id else 0.85 if document.workspace_id == scope.workspace_id else 0.70 if document.workspace_id is None else 0.45
                score = 0.20 * lexical + 0.15 * scope_score + 0.15 * entity + 0.05 + 0.10 * document.importance + 0.05 * document.confidence
                external = (document.metadata_json or {}).get("external_source") or {}
                try:
                    observed = datetime.fromisoformat(str(external.get("observed_at") or "").replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError, OverflowError):
                    observed = 0.0
                rank = (-score, -observed, document.target_id, document.id)
                key = (document.target_type, " ".join(document.content.casefold().split()))
                previous = pool.get(key)
                if previous is not None:
                    excluded["duplicate"] += 1
                if previous is None or rank < previous[0]:
                    pool[key] = (rank, document)
                if len(pool) > pool_limit:
                    del pool[max(pool, key=lambda value: pool[value][0])]
        finally:
            stream.close()
        documents = [item[1] for item in sorted(pool.values(), key=lambda item: item[0])]
        semantic_boosts: dict[str, float] = {}
        embedding_enabled = False
        try:
            embedding_enabled = bool(
                load_enhancement_config(self.db, scope.workspace_id)
                .get("embedding", {})
                .get("enabled", False)
            )
            if embedding_enabled and documents:
                semantic_boosts = semantic_boosts_for_documents(
                    self.db,
                    scope.workspace_id,
                    get_settings(),
                    query,
                    list(documents),
                )
        except Exception:
            semantic_boosts = {}
        candidates: list[RetrievalCandidate] = []
        for document in documents:
            # ── Lifecycle: expired documents ────────────────────────────────
            # ── Audience / scope ────────────────────────────────────────────
            audience_ok = False
            scope_score = 0.0
            if document.task_id and scope.task_id == document.task_id:
                audience_ok, scope_score = True, 1.0
            elif document.workspace_id == scope.workspace_id:
                audience_ok, scope_score = True, 0.85
            elif (
                document.workspace_id is None
                and document.subject_user_id == scope.principal_user_id
            ):
                audience_ok, scope_score = True, 0.70
            elif scope.cross_workspace_authorized and document.subject_user_id == scope.principal_user_id:
                audience_ok, scope_score = True, 0.45
            if not audience_ok:
                excluded["out_of_scope"] += 1
                continue
            if document.subject_user_id and document.subject_user_id != scope.principal_user_id:
                excluded["out_of_scope"] += 1
                continue
            if document.sensitivity not in scope.allowed_sensitivity:
                excluded["sensitivity"] += 1
                continue
            if document.confidence < min_confidence:
                excluded["quality"] += 1
                continue
            # ── Scoring ─────────────────────────────────────────────────────
            raw_fts = fts_scores.get(document.id)
            document_tokens = _tokens(f"{document.subject} {document.content}")
            if raw_fts is not None:
                lexical = raw_fts
            elif query_tokens and document_tokens:
                lexical = len(query_tokens & document_tokens) / math.sqrt(
                    len(query_tokens) * len(document_tokens)
                )
            else:
                lexical = 0.0
            entity = 1.0 if query_tokens & _tokens(document.entity_aliases_text) else 0.0
            recency = 0.5
            confidence = max(0.0, min(1.0, document.confidence))
            importance = max(0.0, min(1.0, document.importance))
            semantic = min(
                1.0,
                max(0.0, float(semantic_boosts.get(document.target_id, 0.0))),
            )
            score = (
                0.25 * semantic
                + 0.20 * lexical
                + 0.15 * scope_score
                + 0.15 * entity
                + 0.10 * importance
                + 0.10 * recency
                + 0.05 * confidence
            )
            if score < min_score:
                excluded["quality"] += 1
                continue
            candidates.append(
                RetrievalCandidate(
                    document.id,
                    document.target_type,
                    document.target_id,
                    document.subject,
                    document.content,
                    document.source_event_id,
                    document.status,
                    document.sensitivity,
                    document.confidence,
                    document.importance,
                    score,
                    {
                        "semantic": semantic,
                        "lexical": lexical,
                        "scope": scope_score,
                        "entity": entity,
                        "importance": importance,
                        "recency": recency,
                        "confidence": confidence,
                    },
                    external_source=(
                        dict(document.metadata_json.get("external_source") or {})
                        if isinstance(getattr(document, "metadata_json", None), dict)
                        else None
                    ),
                )
            )
        def _sort_key(item: RetrievalCandidate) -> tuple[float, float, str]:
            observed_rank = 0.0
            if item.external_source:
                try:
                    observed_rank = datetime.fromisoformat(
                        str(item.external_source.get("observed_at") or "").replace("Z", "+00:00")
                    ).timestamp()
                except (TypeError, ValueError, OverflowError):
                    observed_rank = 0.0
            return (-item.score, -observed_rank, item.target_id)

        # Benchmark updates with the same lexical score prefer the latest
        # historical observation, while ordinary memories retain the stable ID
        # tie-break used before this provenance field existed.
        candidates.sort(key=_sort_key)
        deduped: list[RetrievalCandidate] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            key = (candidate.target_type, re.sub(r"\s+", " ", candidate.content.casefold()).strip())
            if key in seen:
                excluded["duplicate"] += 1
                continue
            seen.add(key)
            deduped.append(candidate)
            if len(deduped) >= top_k:
                break
        degraded: tuple[str, ...] = ()
        if embedding_enabled and not semantic_boosts:
            degraded = ("embedding_unavailable",)
        return RetrievalResult(tuple(deduped), excluded, degraded)

    def _fts_scores(
        self, scope: MemoryScopeContext, query: str, *, limit: int
    ) -> dict[str, float]:
        """Return document_id → normalized lexical score in [0, 1).

        SQL uses ``ORDER BY bm25(...) ASC`` (smaller raw bm25 = more relevant).
        """

        if self.db.bind is None or self.db.bind.dialect.name != "sqlite" or not query.strip():
            return {}
        capability = probe_memory_search_fts_capability(self.db)
        if capability == "unavailable":
            ensure_memory_search_fts(self.db)
            capability = probe_memory_search_fts_capability(self.db)
            if capability == "unavailable":
                return {}
        safe_query = " ".join(sorted(_tokens(query)))
        if not safe_query:
            return {}
        try:
            rows = self.db.execute(
                text(
                    "SELECT document_id, bm25(memory_search_fts) AS rank "
                    "FROM memory_search_fts WHERE memory_search_fts MATCH :query "
                    "AND tenant_id = :tenant AND (workspace_id = :workspace OR workspace_id = '') "
                    "ORDER BY bm25(memory_search_fts) ASC LIMIT :limit"
                ),
                {
                    "query": safe_query,
                    "tenant": scope.tenant_id,
                    "workspace": scope.workspace_id,
                    "limit": limit,
                },
            ).all()
        except Exception:
            return {}
        return {str(row[0]): normalize_bm25_score(float(row[1])) for row in rows}
