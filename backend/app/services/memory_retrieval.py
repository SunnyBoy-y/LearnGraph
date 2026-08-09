from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
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
        documents = self.db.scalars(
            select(MemorySearchDocument).where(
                MemorySearchDocument.tenant_id == scope.tenant_id,
                MemorySearchDocument.memory_layer.in_(allowed_layers),
                MemorySearchDocument.status == "active",
                workspace_scope,
                (MemorySearchDocument.subject_user_id == scope.principal_user_id)
                | (MemorySearchDocument.subject_user_id.is_(None)),
                MemorySearchDocument.sensitivity.in_(scope.allowed_sensitivity),
            )
        ).all()
        fts_scores = self._fts_scores(scope, query, limit=max(top_k * 5, 20))
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
            if document.valid_until is not None and document.valid_until < now:
                excluded["expired"] += 1
                continue
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
                )
            )
        candidates.sort(key=lambda item: (-item.score, item.target_id))
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
