from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.memory_event_models import MemoryScopeContext, MemorySearchDocument


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
        }
        query_tokens = _tokens(query)
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
        candidates: list[RetrievalCandidate] = []
        for document in documents:
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
            raw_fts = fts_scores.get(document.id)
            document_tokens = _tokens(f"{document.subject} {document.content}")
            lexical = (
                1.0 / (1.0 + max(0.0, raw_fts))
                if raw_fts is not None
                else (
                    len(query_tokens & document_tokens)
                    / math.sqrt(len(query_tokens) * len(document_tokens))
                    if query_tokens and document_tokens
                    else 0.0
                )
            )
            entity = 1.0 if query_tokens & _tokens(document.entity_aliases_text) else 0.0
            recency = 0.5
            confidence = max(0.0, min(1.0, document.confidence))
            importance = max(0.0, min(1.0, document.importance))
            semantic = 0.0
            score = (
                0.20 * lexical
                + 0.15 * scope_score
                + 0.15 * entity
                + 0.10 * importance
                + 0.10 * recency
                + 0.05 * confidence
                + 0.25 * semantic
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
        degraded = ("embedding_unavailable",)
        return RetrievalResult(tuple(deduped), excluded, degraded)

    def _fts_scores(
        self, scope: MemoryScopeContext, query: str, *, limit: int
    ) -> dict[str, float]:
        if self.db.bind is None or self.db.bind.dialect.name != "sqlite" or not query.strip():
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
                    "ORDER BY rank ASC LIMIT :limit"
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
        return {str(row[0]): abs(float(row[1])) for row in rows}
