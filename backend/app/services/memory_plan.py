"""Canonical plan state machine for memory atoms.

A plan is addressed by a stable ``plan:<normalized subject>`` key. Reschedule,
cancel and complete operations must target an existing memory id or canonical
plan key; they must never be downgraded into a new CREATE because the model
omitted an id.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

PLAN_CANONICAL_PREFIX = "plan:"

# States used by the product state machine. ``planned`` / ``ongoing`` are
# accepted as legacy aliases and normalized to ``scheduled`` / ``in_progress``.
PLAN_STATES = frozenset(
    {
        "tentative",
        "scheduled",
        "rescheduled",
        "in_progress",
        "completed",
        "cancelled",
        "lapsed_unverified",
    }
)

_PLAN_TRANSITIONS: dict[str, frozenset[str]] = {
    "tentative": frozenset({"scheduled", "rescheduled", "in_progress", "completed", "cancelled", "lapsed_unverified"}),
    "scheduled": frozenset({"rescheduled", "in_progress", "completed", "cancelled", "lapsed_unverified"}),
    "rescheduled": frozenset({"scheduled", "in_progress", "completed", "cancelled", "lapsed_unverified"}),
    "in_progress": frozenset({"completed", "cancelled", "lapsed_unverified"}),
    "completed": frozenset({"lapsed_unverified"}),
    "cancelled": frozenset({"rescheduled", "lapsed_unverified"}),
    "lapsed_unverified": frozenset({"scheduled", "rescheduled", "in_progress", "completed", "cancelled"}),
}

_CHANGE_TEXT = {
    "scheduled": "原计划 → 已安排",
    "rescheduled": "原计划 → 改期",
    "in_progress": "原计划 → 进行中",
    "completed": "原计划 → 完成",
    "cancelled": "原计划 → 取消",
    "lapsed_unverified": "原计划 → 已逾期，待确认",
}

_TERMINAL_FOR_RECALL = frozenset({"completed", "cancelled", "lapsed_unverified", "rescheduled"})


def normalize_plan_subject(subject: str) -> str:
    subject = re.sub(r"\s+", " ", (subject or "").strip())
    subject = re.sub(r"[，。！？!?：:；;、,.\-—]+", "", subject)
    return subject[:120]


def canonical_plan_key(subject: str) -> str:
    return f"{PLAN_CANONICAL_PREFIX}{normalize_plan_subject(subject)}"


def normalize_plan_status(
    value: str | None,
    *,
    start_at: datetime | None = None,
    now: datetime | None = None,
) -> str:
    status = (value or "").strip()
    if status in {"planned", "scheduled"}:
        if start_at is not None and now is not None and start_at <= now:
            return "lapsed_unverified"
        return "scheduled" if start_at is not None else "tentative"
    if status == "ongoing":
        return "in_progress"
    if status in PLAN_STATES:
        return status
    return "timeless"


def ensure_plan_canonical_key(
    structured: dict[str, Any], *, title: str | None = None
) -> dict[str, Any]:
    """Return structured payload with a stable plan key when it is plan-like."""
    status = normalize_plan_status(str(structured.get("temporal_status") or ""))
    if status not in PLAN_STATES:
        return structured
    canonical = str(structured.get("canonical_key") or "").strip()
    if not canonical:
        canonical = canonical_plan_key(title or str(structured.get("title") or ""))
    return {**structured, "canonical_key": canonical}


def resolve_target_for_operation(
    operation: str,
    *,
    target_memory_id: str | None,
    canonical_key: str | None,
    records: list[Any],
) -> str | None:
    """Resolve an explicit id or a stable canonical plan key.

    Returns ``None`` when the target cannot be proven to exist. The caller must
    treat that as NOOP/needs-confirmation, never as CREATE.
    """
    operation = (operation or "").strip().upper()
    if operation == "CREATE":
        return target_memory_id
    known_ids = {str(getattr(record, "id", record.get("id") if isinstance(record, dict) else "")) for record in records}
    if target_memory_id and target_memory_id in known_ids:
        return target_memory_id
    if not canonical_key:
        return None
    normalized = canonical_key.strip()
    if not normalized.startswith(PLAN_CANONICAL_PREFIX):
        normalized = canonical_plan_key(normalized)
    for record in records:
        record_key = (
            record.get("canonical_key") if isinstance(record, dict) else getattr(record, "canonical_key", "")
        )
        if str(record_key or "").strip() == normalized:
            record_id = (
                record.get("id") if isinstance(record, dict) else getattr(record, "id", "")
            )
            return str(record_id)
    return None


def plan_change_text(current: str) -> str:
    return _CHANGE_TEXT.get(current, "")


def plan_visible_for_recall(status: str) -> bool:
    return (status or "").strip() not in _TERMINAL_FOR_RECALL
