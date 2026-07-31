from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.domain.memory_event_models import (
    MemoryRetrievalTrace,
    MemoryScopeContext,
    MemorySearchDocument,
)
from app.services.memory_projector import probe_memory_search_fts_capability
from app.services.memory_retrieval import MemoryHybridRetriever, RetrievalResult

MemoryRoute = Literal[
    "task_state",
    "project_decision",
    "episode",
    "file",
    "learning_state",
    "user_preference",
    "strategy",
    "memory",
]

ROUTE_LAYER_MAP: dict[MemoryRoute, frozenset[str]] = {
    "task_state": frozenset({"L2"}),
    "project_decision": frozenset({"L4"}),
    "episode": frozenset({"L1"}),
    "file": frozenset({"L5"}),
    "learning_state": frozenset({"L5"}),
    "user_preference": frozenset({"L3"}),
    "strategy": frozenset({"L6"}),
    "memory": frozenset({"L3", "L4"}),
}


@dataclass(frozen=True, slots=True)
class IntentSignal:
    """One detected intent from a query."""

    intent: str
    confidence: float
    marker: str


@dataclass(frozen=True, slots=True)
class MemoryRouteResult:
    routes: tuple[MemoryRoute, ...]
    retrieval: RetrievalResult
    signals: tuple[IntentSignal, ...] = ()
    trace_id: str | None = None
    policy_version: str = "memory-router-v2"


# ── Intent patterns ────────────────────────────────────────────────────────────
# Each entry: (intent_name, regex_pattern, base_confidence, mapped_routes)
_INTENT_MATRIX: list[tuple[str, re.Pattern[str], float, tuple[MemoryRoute, ...]]] = [
    # Continuation / resume
    (
        "continue_task",
        re.compile(
            r"(继续|上次|resume|continue|接着|回到|回到之前|pick\s+up)", re.I
        ),
        0.90,
        ("task_state", "episode", "project_decision"),
    ),
    # Decision recall
    (
        "decision_recall",
        re.compile(
            r"(为什么|决定|决策|原因|理由|why|decision|reason|rationale)", re.I
        ),
        0.85,
        ("project_decision", "episode"),
    ),
    # File / document reference
    (
        "file_reference",
        re.compile(
            r"(pdf|文件|文档|file|document|报告|report|上传|upload)", re.I
        ),
        0.80,
        ("file",),
    ),
    # Learning / mastery
    (
        "learning_mastery",
        re.compile(
            r"(掌握|复习|熟练|学会|mastery|review|learn|记住|背诵|练习)", re.I
        ),
        0.85,
        ("learning_state",),
    ),
    # User preference / habit
    (
        "user_preference",
        re.compile(
            r"(偏好|习惯|喜欢|风格|preference|habit|style|总是|always)", re.I
        ),
        0.80,
        ("user_preference",),
    ),
    # Strategy / approach
    (
        "strategy_recall",
        re.compile(
            r"(同类|上次怎么修|怎么解决|策略|方法|approach|strategy|how\s+to\s+fix)",
            re.I,
        ),
        0.85,
        ("strategy", "task_state"),
    ),
    # Task state direct
    (
        "task_status",
        re.compile(
            r"(进度|状态|下一步|blocked|status|progress|next\s+step|做到哪)", re.I
        ),
        0.85,
        ("task_state",),
    ),
    # Episode recall
    (
        "episode_recall",
        re.compile(
            r"(聊过|讨论过|说过|提到过|mentioned|discussed|talked\s+about)", re.I
        ),
        0.80,
        ("episode", "memory"),
    ),
    # Knowledge / fact question
    (
        "knowledge_query",
        re.compile(
            r"(是什么|什么是|定义|meaning|what\s+is|define|概念|concept)", re.I
        ),
        0.70,
        ("memory", "file"),
    ),
    # Graph / mind map
    (
        "graph_reference",
        re.compile(
            r"(图谱|节点|知识图|关系|graph|node|relationship|mind\s*map)", re.I
        ),
        0.80,
        ("memory", "file"),
    ),
]


def _detect_intents(query: str) -> list[IntentSignal]:
    """Run the intent matrix against the normalized query."""

    normalized = query.casefold()
    signals: list[IntentSignal] = []
    for intent_name, pattern, confidence, _routes in _INTENT_MATRIX:
        match = pattern.search(normalized)
        if match:
            signals.append(IntentSignal(intent_name, confidence, match.group(0)))
    return signals


def _routes_from_signals(signals: list[IntentSignal]) -> tuple[MemoryRoute, ...]:
    """Map detected signals to deduplicated, ordered routes."""

    seen: dict[MemoryRoute, float] = {}
    signal_intents = {s.intent for s in signals}
    for intent_name, _pattern, confidence, routes in _INTENT_MATRIX:
        if intent_name not in signal_intents:
            continue
        for route in routes:
            prev = seen.get(route, 0.0)
            if confidence > prev:
                seen[route] = confidence
    if not seen:
        return ("memory",)
    ordered = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
    return tuple(route for route, _ in ordered)


class MemoryRouter:
    """Intent-driven memory router with multi-domain top-k dispatch.

    When a ``Session`` is provided at construction, each ``route()`` call
    persists a :class:`MemoryRetrievalTrace` with query-hash, signals,
    scores and exclusion counts. The trace is best-effort: failures are
    swallowed so retrieval never blocks chat.
    """

    def __init__(
        self, retriever: MemoryHybridRetriever, *, db: Session | None = None
    ) -> None:
        self.retriever = retriever
        self.db = db

    def route(self, scope: MemoryScopeContext, query: str) -> MemoryRouteResult:
        t0 = time.monotonic()
        signals = _detect_intents(query)
        routes = _routes_from_signals(signals)
        layers: set[str] = set()
        for route in routes:
            layers.update(ROUTE_LAYER_MAP[route])

        retrieval = self.retriever.search(
            scope,
            query,
            allowed_layers=frozenset(layers),
            top_k=6,
            min_score=0.20,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        trace_id = self._persist_trace(scope, query, routes, signals, retrieval, latency_ms)
        return MemoryRouteResult(routes, retrieval, tuple(signals), trace_id)

    def _persist_trace(
        self,
        scope: MemoryScopeContext,
        query: str,
        routes: tuple[MemoryRoute, ...],
        signals: list[IntentSignal],
        retrieval: RetrievalResult,
        latency_ms: int,
    ) -> str | None:
        """Best-effort trace write; returns trace_id or None on failure."""

        if self.db is None:
            return None
        try:
            trace = MemoryRetrievalTrace(
                tenant_id=scope.tenant_id,
                workspace_id=scope.workspace_id,
                subject_user_id=scope.principal_user_id,
                agent_id=scope.agent_id,
                query_hash=hashlib.sha256(query.encode("utf-8")).hexdigest(),
                routes_json=list(routes),
                signals_json=[
                    {"intent": s.intent, "confidence": s.confidence, "marker": s.marker}
                    for s in signals
                ],
                candidate_count=len(retrieval.candidates),
                selected_count=len(retrieval.candidates),
                excluded_counts_json=retrieval.excluded,
                degraded_modes_json=list(retrieval.degraded_modes),
                fts_capability=probe_memory_search_fts_capability(self.db),
                strategy="hybrid_memory_v2",
                status="completed",
                latency_ms=latency_ms,
            )
            self.db.add(trace)
            self.db.flush()
            return trace.id
        except Exception:
            # Telemetry must never break retrieval.
            self.db.rollback()
            return None
