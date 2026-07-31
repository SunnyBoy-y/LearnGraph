from __future__ import annotations

from enum import StrEnum


class MemoryEventType(StrEnum):
    MEMORY_CREATED = "memory.atom_created"
    MEMORY_CORRECTED = "memory.atom_corrected"
    MEMORY_CONFIRMED = "memory.atom_confirmed"
    MEMORY_SUPERSEDED = "memory.atom_superseded"
    MEMORY_RETRACTED = "memory.atom_retracted"
    MEMORY_DELETE_REQUESTED = "memory.atom_delete_requested"
    MEMORY_RESTORED = "memory.atom_restored"
    MEMORY_FORGOTTEN = "memory.atom_forgotten"
    MEMORY_SCOPE_CHANGED = "memory.scope_changed"
    MEMORY_SENSITIVITY_CHANGED = "memory.sensitivity_changed"
    MEMORY_AUTO_RECALL_SUPPRESSED = "memory.auto_recall_suppressed"
    MEMORY_FEEDBACK_RECORDED = "memory.feedback_recorded"
    TASK_CREATED = "task.created"
    TASK_STAGE_CHANGED = "task.stage_changed"
    TASK_STEP_COMPLETED = "task.step_completed"
    TASK_BLOCKED = "task.blocked"
    TASK_RESUMED = "task.resumed"
    TASK_COMPLETED = "task.completed"
    TASK_CANCELLED = "task.cancelled"
    EPISODE_OPENED = "episode.opened"
    EPISODE_CLOSED = "episode.closed"
    EPISODE_CORRECTED = "episode.corrected"
    EPISODE_INVALIDATED = "episode.invalidated"
    ARTIFACT_REVISION_ACTIVATED = "artifact.revision_activated"
    ARTIFACT_REVISION_INVALIDATED = "artifact.revision_invalidated"
    LEARNING_EVIDENCE_RECORDED = "learning.evidence_recorded"
    LEARNING_EVIDENCE_INVALIDATED = "learning.evidence_invalidated"
    LEARNING_NODE_STATE_CHANGED = "learning.node_state_changed"
    AGENT_RUN_STARTED = "agent.run_started"
    AGENT_RUN_COMPLETED = "agent.run_completed"
    AGENT_RUN_FAILED = "agent.run_failed"
    STRATEGY_CANDIDATE_CREATED = "strategy.candidate_created"
    STRATEGY_VERIFIED = "strategy.verified"
    STRATEGY_DEGRADED = "strategy.degraded"


CURRENT_EVENT_SCHEMA_VERSIONS: dict[str, int] = {
    event_type.value: 1 for event_type in MemoryEventType
}


MEMORY_EVENT_TYPES = frozenset(
    event_type.value for event_type in MemoryEventType if event_type.value.startswith("memory.")
)
