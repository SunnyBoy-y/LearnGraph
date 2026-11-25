"""Shared occurrence-window semantics for memory views and recall."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.errors import AppError
from app.services.temporal_normalizer import TemporalNormalizer, parse_temporal_query


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def temporal_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = (payload or {}).get("temporal_semantics")
    return raw if isinstance(raw, dict) else {}


def iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return utc_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class OccurrenceWindow:
    start_at: datetime | None
    end_at: datetime | None
    display: str = ""

    def __post_init__(self) -> None:
        # SQLite drops offsets when binding DateTime values; all SQL boundaries
        # must be UTC even when callers construct a window directly.
        if self.start_at is not None:
            object.__setattr__(self, "start_at", utc_datetime(self.start_at))
        if self.end_at is not None:
            object.__setattr__(self, "end_at", utc_datetime(self.end_at))

    def contains(self, start: datetime | None, end: datetime | None = None) -> bool:
        if start is None:
            return False
        start = utc_datetime(start)
        end = utc_datetime(end) if end is not None else None
        if self.end_at is not None and start >= self.end_at:
            return False
        if self.start_at is None:
            return True
        if end is not None and end > start:
            return end > self.start_at
        return start >= self.start_at

    def matches_memory(self, event_at: datetime | None, metadata: dict[str, Any]) -> bool:
        start = iso_datetime(metadata.get("start_at"))
        end = iso_datetime(metadata.get("end_at"))
        # An explicit occurrence timestamp wins over conflicting expression metadata.
        if event_at is not None and start is not None and utc_datetime(event_at) != start:
            return self.contains(event_at)
        return self.contains(event_at or start, end)

    def as_dict(self) -> dict[str, str | None]:
        return {
            "start_at": self.start_at.isoformat() if self.start_at is not None else None,
            "end_at": self.end_at.isoformat() if self.end_at is not None else None,
            "display": self.display,
        }


def temporal_recall_window(query: str, *, timezone_name: str | None = None) -> tuple[OccurrenceWindow | None, str]:
    from app.services.temporal_normalizer import _TEMPORAL_WHAT_HAPPENED_RE, _TEMPORAL_WHEN_RE

    intent = _TEMPORAL_WHAT_HAPPENED_RE.search(query) or _TEMPORAL_WHEN_RE.search(query)
    if not intent:
        return None, query
    parsed = parse_temporal_query(query, timezone_name=timezone_name)
    if not parsed or parsed.start_at is None:
        return None, query
    window = resolve_occurrence_window(start_at=parsed.start_at, end_at=parsed.end_at or parsed.start_at + timedelta(days=1))
    residual = _TEMPORAL_WHEN_RE.sub("", _TEMPORAL_WHAT_HAPPENED_RE.sub("", parsed.residual_query))
    residual = residual.strip(" 的在于我我们您你了过 ，。？！,.?!")
    return window, residual


def resolve_occurrence_window(
    *,
    time_query: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    timezone_name: str | None = None,
    observed_at: datetime | None = None,
) -> OccurrenceWindow | None:
    display = ""
    if time_query is not None:
        now = observed_at or datetime.now(timezone.utc)
        parsed = parse_temporal_query(time_query, now=now, timezone_name=timezone_name)
        if parsed is None or parsed.start_at is None:
            parsed = TemporalNormalizer().normalize(
                time_query, observed_at=now, timezone_name=timezone_name, prefer_past=True,
            )
        if parsed.start_at is None:
            raise AppError(422, "invalid_time_query", "A resolvable time expression is required")
        start_at = start_at if start_at is not None else parsed.start_at
        end_at = end_at if end_at is not None else parsed.end_at or parsed.start_at + timedelta(days=1)
        display = parsed.normalized_display
    start_at = utc_datetime(start_at) if start_at is not None else None
    end_at = utc_datetime(end_at) if end_at is not None else None
    if start_at is None and end_at is None:
        return None
    if start_at is not None and end_at is not None and start_at >= end_at:
        raise AppError(422, "invalid_time_window", "start_at must be earlier than end_at")
    return OccurrenceWindow(start_at, end_at, display)
