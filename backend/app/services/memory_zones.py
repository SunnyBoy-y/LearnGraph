from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.memory_event_models import MemorySearchDocument
from app.domain.models import ChatSession, Goal, MemoryRecord

_ZONES = ("hot", "recent", "topics", "archive")
_RECENT_WINDOW = timedelta(days=3)
_GOAL_ACTIVE_STATUSES = frozenset({"clarifying", "published", "active", "learning"})


@dataclass(slots=True)
class ReconcileZonesReport:
    reviewed: int = 0
    changed: int = 0
    archived: int = 0
    hot: int = 0
    recent: int = 0
    topics: int = 0


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _terminal(
    *,
    state: str,
    lifecycle_status: str,
    ledger_status: str,
    valid_until: datetime | None,
    now: datetime,
) -> bool:
    if state != "active":
        return True
    if lifecycle_status != "active":
        return True
    if ledger_status != "active":
        return True
    valid = _as_utc(valid_until)
    return valid is not None and valid <= now


def derive_record_zone(
    record: MemoryRecord,
    *,
    active_goal_ids: set[str],
    closed_session_ids: set[str],
    now: datetime | None = None,
) -> str:
    """Return the automatic cold/hot layer for a v1 MemoryRecord.

    Order matters: terminal states win, then active-goal hot memories, then
    unconfirmed/recent working memory, and finally stable topic memory.
    """

    now = now or datetime.now(timezone.utc)
    if _terminal(
        state=record.state,
        lifecycle_status=record.lifecycle_status or "active",
        ledger_status=record.ledger_status or "active",
        valid_until=record.valid_until,
        now=now,
    ):
        return "archive"
    if record.namespace == "session" and record.session_id in closed_session_ids:
        return "archive"
    goal_id = getattr(record, "goal_id", None)
    if goal_id and goal_id in active_goal_ids:
        return "hot"
    created = _as_utc(record.created_at) or now
    confirmation_count = int(record.confirmation_count or 0)
    if confirmation_count == 0 or created >= now - _RECENT_WINDOW:
        return "recent"
    return "topics"


def derive_document_zone(
    document: MemorySearchDocument,
    *,
    now: datetime | None = None,
) -> str:
    """Return the automatic layer for an event-sourced search document."""

    now = now or datetime.now(timezone.utc)
    if document.status != "active":
        return "archive"
    valid_until = _as_utc(document.valid_until)
    if valid_until is not None and valid_until <= now:
        return "archive"
    if float(document.importance or 0) >= 0.8:
        return "hot"
    updated = _as_utc(document.updated_at) or now
    if updated >= now - _RECENT_WINDOW:
        return "recent"
    return "topics"


def reconcile_memory_zones(
    db: Session,
    workspace_id: str,
    *,
    now: datetime | None = None,
) -> ReconcileZonesReport:
    """Persist automatic layering for v1 records and v2 search projections.

    The event stream remains the source of truth; this only maintains the
    display/retrieval metadata used by the unified memory views.
    """

    now = now or datetime.now(timezone.utc)
    report = ReconcileZonesReport()
    active_goal_ids = {
        str(goal.id)
        for goal in db.scalars(
            select(Goal).where(
                Goal.workspace_id == workspace_id,
                Goal.status.in_(tuple(_GOAL_ACTIVE_STATUSES)),
            )
        ).all()
    }
    closed_session_ids = {
        str(session.id)
        for session in db.scalars(
            select(ChatSession).where(
                ChatSession.workspace_id == workspace_id,
                ChatSession.status == "closed",
            )
        ).all()
    }

    records = db.scalars(
        select(MemoryRecord).where(MemoryRecord.workspace_id == workspace_id)
    ).all()
    record_zone_by_id: dict[str, str] = {}
    for record in records:
        report.reviewed += 1
        target = derive_record_zone(
            record,
            active_goal_ids=active_goal_ids,
            closed_session_ids=closed_session_ids,
            now=now,
        )
        record_zone_by_id[str(record.id)] = target
        if record.zone != target:
            record.zone = target
            report.changed += 1
        if target == "archive":
            report.archived += 1
        elif target == "hot":
            report.hot += 1
        elif target == "recent":
            report.recent += 1
        else:
            report.topics += 1

    documents = db.scalars(
        select(MemorySearchDocument).where(
            MemorySearchDocument.target_type == "memory",
            MemorySearchDocument.workspace_id == workspace_id,
        )
    ).all()
    for document in documents:
        if str(document.target_id) in record_zone_by_id:
            target = record_zone_by_id[str(document.target_id)]
        else:
            target = derive_document_zone(document, now=now)
        report.reviewed += 1
        if document.zone != target:
            document.zone = target
            report.changed += 1
        if target == "archive":
            report.archived += 1
        elif target == "hot":
            report.hot += 1
        elif target == "recent":
            report.recent += 1
        else:
            report.topics += 1
    db.flush()
    return report


def normalize_zone(value: Any, *, default: str = "recent") -> str:
    """Coerce event payload/record zone values into the supported set."""

    candidate = str(value or default).strip().casefold()
    return candidate if candidate in _ZONES else default
