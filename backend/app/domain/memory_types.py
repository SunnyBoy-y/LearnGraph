"""Goal-Scoped Memory Graph — type registry and plane invariants.

Canonical business state (Goal, Graph, Mastery, Roadmap, FileRecord, …) must
never be re-homed as free-form Memory content. Memory holds recallable
preferences, teacher focus, misconceptions, strategies, and decisions, always
with source references and a typed scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ScopeType = Literal["workspace", "goal", "node", "session"]
MergeStrategy = Literal[
    "OVERRIDE",
    "UNION",
    "KEYED_MERGE",
    "APPEND",
    "LOCAL_ONLY",
    "INHERIT_UNTIL_OVERRIDE",
    "REFERENCE_ONLY",
]
DecayPolicy = Literal[
    "NONE",
    "SLOW",
    "FAST_VALIDATION_DECAY",
    "GOAL_LIFECYCLE",
    "SESSION_ONLY",
]
ResolutionStatus = Literal[
    "none",
    "active_misconception",
    "improving",
    "resolved",
    "recurring",
]
DraftOperation = Literal[
    "CREATE",
    "UPDATE",
    "MERGE",
    "SUPERSEDE",
    "RETRACT",
    "PROMOTE",
    "DEMOTE",
    "ARCHIVE",
]
DraftStatus = Literal["PENDING", "COMMITTED", "REJECTED", "CANCELLED"]

# Fact planes that must stay in structured tables / evidence, not Memory body.
CANONICAL_STATE_FIELDS = frozenset(
    {
        "mastery_score",
        "mastery_stars",
        "retrieval_state",
        "evidence_state",
        "roadmap_version",
        "deadline_at",
        "graph_revision",
        "storage_uri",
        "file_bytes",
        "parse_status",
    }
)

SCOPE_RANK: dict[str, int] = {
    "workspace": 10,
    "goal": 20,
    "node": 30,
    "session": 40,
}


@dataclass(frozen=True, slots=True)
class MemoryTypeDefinition:
    memory_type: str
    default_scope: ScopeType
    merge_strategy: MergeStrategy
    decay_policy: DecayPolicy
    requires_confirmation: bool = False
    description: str = ""
    schema: dict[str, str] | None = None


MEMORY_TYPE_REGISTRY: dict[str, MemoryTypeDefinition] = {
    "semantic_memory": MemoryTypeDefinition(
        memory_type="semantic_memory",
        default_scope="workspace",
        merge_strategy="UNION",
        decay_policy="SLOW",
        description="Generic stable fact (legacy default).",
    ),
    "learning_preference": MemoryTypeDefinition(
        memory_type="learning_preference",
        default_scope="workspace",
        merge_strategy="INHERIT_UNTIL_OVERRIDE",
        decay_policy="NONE",
        description="How the learner prefers explanations and practice order.",
        schema={"style": "string", "language": "string"},
    ),
    "teacher_focus": MemoryTypeDefinition(
        memory_type="teacher_focus",
        default_scope="goal",
        merge_strategy="UNION",
        decay_policy="GOAL_LIFECYCLE",
        description="Exam or teacher emphasis within a goal.",
        schema={"course": "string", "concept_ids": "string[]", "exam_probability": "number"},
    ),
    "misconception": MemoryTypeDefinition(
        memory_type="misconception",
        default_scope="node",
        merge_strategy="LOCAL_ONLY",
        decay_policy="FAST_VALIDATION_DECAY",
        description="Known confusion; track resolution instead of deleting.",
        schema={"confused_concepts": "string[]", "resolution_status": "string"},
    ),
    "strategy_effectiveness": MemoryTypeDefinition(
        memory_type="strategy_effectiveness",
        default_scope="goal",
        merge_strategy="KEYED_MERGE",
        decay_policy="SLOW",
        description="Which teaching or practice strategies worked.",
        schema={"strategy": "string", "outcome": "string", "success_count": "number"},
    ),
    "decision": MemoryTypeDefinition(
        memory_type="decision",
        default_scope="goal",
        merge_strategy="APPEND",
        decay_policy="SLOW",
        description="Why a path, skip, or plan was chosen.",
        schema={"decision": "string", "rationale": "string"},
    ),
    "goal_constraint": MemoryTypeDefinition(
        memory_type="goal_constraint",
        default_scope="goal",
        merge_strategy="OVERRIDE",
        decay_policy="GOAL_LIFECYCLE",
        description="Goal-local constraints that affect teaching style.",
    ),
    "ai_observation": MemoryTypeDefinition(
        memory_type="ai_observation",
        default_scope="session",
        merge_strategy="LOCAL_ONLY",
        decay_policy="FAST_VALIDATION_DECAY",
        requires_confirmation=True,
        description="Low-confidence model observation; fast decay.",
    ),
    "event_summary": MemoryTypeDefinition(
        memory_type="event_summary",
        default_scope="workspace",
        merge_strategy="APPEND",
        decay_policy="SLOW",
        description="Closed learning event capsule.",
    ),
}


def get_memory_type(memory_type: str) -> MemoryTypeDefinition:
    return MEMORY_TYPE_REGISTRY.get(memory_type) or MEMORY_TYPE_REGISTRY["semantic_memory"]


def validate_not_canonical_state_payload(payload: dict[str, Any] | None) -> None:
    """Reject structured payloads that try to store canonical state in Memory."""

    if not payload:
        return
    overlap = CANONICAL_STATE_FIELDS.intersection(payload.keys())
    if overlap:
        raise ValueError(
            "Memory structured_payload must not store canonical state fields: "
            + ", ".join(sorted(overlap))
        )


def normalize_scope(
    *,
    scope_type: str | None,
    scope_id: str | None,
    namespace: str,
    session_id: str | None,
    goal_id: str | None,
    node_id: str | None,
    memory_type: str,
) -> tuple[str, str | None, str | None, str | None]:
    """Return (scope_type, scope_id, goal_id, node_id) with registry defaults."""

    type_def = get_memory_type(memory_type)
    resolved_type = (scope_type or "").strip() or type_def.default_scope
    if namespace == "session":
        resolved_type = "session"
        resolved_id = session_id
    elif resolved_type == "node":
        resolved_id = scope_id or node_id
    elif resolved_type == "goal":
        resolved_id = scope_id or goal_id
    elif resolved_type == "session":
        resolved_id = scope_id or session_id
    else:
        resolved_type = "workspace"
        resolved_id = scope_id
    return resolved_type, resolved_id, goal_id, node_id


def default_decay_rate(policy: str) -> float:
    return {
        "NONE": 0.0,
        "SLOW": 0.01,
        "FAST_VALIDATION_DECAY": 0.08,
        "GOAL_LIFECYCLE": 0.02,
        "SESSION_ONLY": 0.2,
    }.get(policy, 0.02)


def compute_memory_strength(
    *,
    base_importance: float,
    access_count: int,
    confirmation_count: int,
    successful_use_count: int,
    active_goal_bonus: float,
    elapsed_days: float,
    decay_rate: float,
    conflict_penalty: float = 0.0,
) -> float:
    """Lightweight strength score for retrieval ranking (Phase 3)."""

    import math

    raw = (
        max(0.0, min(1.0, base_importance))
        + 0.25 * math.log1p(max(0, access_count))
        + 0.30 * max(0, confirmation_count)
        + 0.20 * max(0, successful_use_count)
        + max(0.0, active_goal_bonus)
        - decay_rate * max(0.0, elapsed_days)
        - max(0.0, conflict_penalty)
    )
    return max(0.0, min(5.0, raw))
