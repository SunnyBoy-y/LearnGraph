"""Pure-rule episode boundary detection.

This module has no backend/app imports on purpose: it is a standalone rule
layer the episode service (or a scheduler worker) calls to decide whether the
current conversation should emit an ``EPISODE_OPENED`` boundary. Keeping it
backend-free makes the rules unit-testable in isolation and forces the
decision logic to stay deterministic — no DB, no I/O.

Boundary signals follow plan §5.4 "Episode 触发顺序":

1. explicit topic switch / task stage completed
2. conversation close / child conversation close
3. cumulative tokens since the last episode reached 4,000–8,000
4. idle window
5. current entity set overlap with the previous episode fell below threshold
6. Context Builder is about to compress history that is not yet an episode

Each signal is returned with a ``priority`` (lower wins) and a normalized
``strength`` in [0, 1] so the caller can rank competing candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

# Token window before we want an episode boundary (plan §5.4 item 3).
TOKEN_THRESHOLD_MIN = 4_000
TOKEN_THRESHOLD_MAX = 8_000
# Minimum entity overlap (Jaccard) between the current window and the previous
# episode before we treat the topic as having drifted (plan §5.4 item 5).
ENTITY_OVERLAP_MIN = 0.3
# Idle window in seconds before an implicit boundary (plan §5.4 item 4).
IDLE_SECONDS_MIN = 1_200  # 20 minutes


class BoundaryReason(IntEnum):
    """Ordering mirrors plan §5.4 items 1..6 (lower value = higher priority)."""

    EXPLICIT_SWITCH = 1
    STAGE_COMPLETED = 2
    CONVERSATION_CLOSE = 3
    TOKEN_THRESHOLD = 4
    IDLE = 5
    LOW_ENTITY_OVERLAP = 6
    PRE_COMPRESSION = 7


@dataclass(frozen=True, slots=True)
class BoundarySignal:
    reason: BoundaryReason
    priority: int  # lower wins; equals reason value by default
    strength: float  # [0, 1] confidence that this signal is real

    def __post_init__(self) -> None:
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strength must be in [0,1], got {self.strength}")


@dataclass(frozen=True, slots=True)
class BoundaryInputs:
    """Snapshot of what the boundary detector needs to reason about.

    All fields are optional so the caller can provide only what it knows;
    absent fields simply do not contribute a signal.
    """

    cumulative_tokens_since_episode: int | None = None
    idle_seconds: float | None = None
    entity_overlap: float | None = None  # Jaccard [0,1]
    explicit_topic_switch: bool = False
    task_stage_completed: bool = False
    conversation_closed: bool = False
    pre_compression: bool = False


def _ratio(value: float, low: float, high: float) -> float:
    """Normalize ``value`` in [low, high] to [0, 1], clamping outliers."""
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def detect_boundary(inputs: BoundaryInputs) -> tuple[BoundarySignal, ...]:
    """Return the boundary signals that fire for ``inputs``, highest priority first.

    Multiple signals can fire at once (e.g. an idle window right after a token
    threshold). The caller decides how to merge them — typically it takes the
    highest-priority signal as ``boundary_reason`` and keeps the rest as
    contributing signals.
    """
    signals: list[BoundarySignal] = []

    if inputs.explicit_topic_switch:
        signals.append(BoundarySignal(BoundaryReason.EXPLICIT_SWITCH, 1, 1.0))
    if inputs.task_stage_completed:
        signals.append(BoundarySignal(BoundaryReason.STAGE_COMPLETED, 2, 1.0))
    if inputs.conversation_closed:
        signals.append(BoundarySignal(BoundaryReason.CONVERSATION_CLOSE, 3, 1.0))

    if inputs.cumulative_tokens_since_episode is not None:
        tokens = inputs.cumulative_tokens_since_episode
        if tokens >= TOKEN_THRESHOLD_MIN:
            # Strength ramps 0→1 across the 4k–8k window; at 8k+ it saturates.
            strength = _ratio(tokens, TOKEN_THRESHOLD_MIN, TOKEN_THRESHOLD_MAX)
            signals.append(BoundarySignal(BoundaryReason.TOKEN_THRESHOLD, 4, strength))

    if inputs.idle_seconds is not None and inputs.idle_seconds >= IDLE_SECONDS_MIN:
        # Strength saturates once idle is well past the floor.
        strength = _ratio(inputs.idle_seconds, IDLE_SECONDS_MIN, IDLE_SECONDS_MIN * 2)
        signals.append(BoundarySignal(BoundaryReason.IDLE, 5, strength))

    if inputs.entity_overlap is not None:
        if inputs.entity_overlap < ENTITY_OVERLAP_MIN:
            # Lower overlap → stronger signal that the topic drifted.
            strength = _ratio(
                ENTITY_OVERLAP_MIN - inputs.entity_overlap,
                0.0,
                ENTITY_OVERLAP_MIN,
            )
            signals.append(BoundarySignal(BoundaryReason.LOW_ENTITY_OVERLAP, 6, strength))

    if inputs.pre_compression:
        signals.append(BoundarySignal(BoundaryReason.PRE_COMPRESSION, 7, 1.0))

    return tuple(sorted(signals, key=lambda s: s.priority))


def top_boundary_signal(
    inputs: BoundaryInputs,
) -> BoundarySignal | None:
    """Return the single highest-priority signal, or ``None`` if none fired."""
    signals = detect_boundary(inputs)
    return signals[0] if signals else None


def reason_label(reason: BoundaryReason) -> str:
    return {
        BoundaryReason.EXPLICIT_SWITCH: "explicit_topic_switch",
        BoundaryReason.STAGE_COMPLETED: "task_stage_completed",
        BoundaryReason.CONVERSATION_CLOSE: "conversation_closed",
        BoundaryReason.TOKEN_THRESHOLD: "token_threshold",
        BoundaryReason.IDLE: "idle",
        BoundaryReason.LOW_ENTITY_OVERLAP: "low_entity_overlap",
        BoundaryReason.PRE_COMPRESSION: "pre_compression",
    }[reason]


def boundary_reason_value(inputs: BoundaryInputs) -> str:
    """Convenience for persisting ``boundary_reason`` on an episode row."""
    signal = top_boundary_signal(inputs)
    return reason_label(signal.reason) if signal is not None else "none"
