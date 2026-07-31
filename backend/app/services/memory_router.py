from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.memory_event_models import MemoryScopeContext
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


@dataclass(frozen=True, slots=True)
class MemoryRouteResult:
    routes: tuple[MemoryRoute, ...]
    retrieval: RetrievalResult
    policy_version: str = "memory-router-v1"


class MemoryRouter:
    def __init__(self, retriever: MemoryHybridRetriever) -> None:
        self.retriever = retriever

    def route(self, scope: MemoryScopeContext, query: str) -> MemoryRouteResult:
        normalized = query.casefold()
        routes: list[MemoryRoute] = []
        if any(marker in normalized for marker in ("继续", "上次", "resume", "continue")):
            routes.extend(("task_state", "episode", "project_decision"))
        if any(marker in normalized for marker in ("为什么", "决定", "why", "decision")):
            routes.extend(("project_decision", "episode"))
        if any(marker in normalized for marker in ("pdf", "文件", "文档", "file")):
            routes.append("file")
        if any(marker in normalized for marker in ("掌握", "复习", "熟练", "mastery")):
            routes.append("learning_state")
        if any(marker in normalized for marker in ("习惯", "偏好", "preference")):
            routes.append("user_preference")
        if any(marker in normalized for marker in ("同类问题", "上次修复", "strategy")):
            routes.extend(("strategy", "task_state"))
        if not routes:
            routes.append("memory")
        routes = list(dict.fromkeys(routes))
        layers: set[str] = set()
        for route in routes:
            layers.update(
                {
                    "task_state": {"L2"},
                    "project_decision": {"L4"},
                    "episode": {"L1"},
                    "file": {"L5"},
                    "learning_state": {"L5"},
                    "user_preference": {"L3"},
                    "strategy": {"L6"},
                    "memory": {"L3", "L4"},
                }[route]
            )
        retrieval = self.retriever.search(
            scope,
            query,
            allowed_layers=frozenset(layers),
            top_k=6,
            min_score=0.20,
        )
        return MemoryRouteResult(tuple(routes), retrieval)
