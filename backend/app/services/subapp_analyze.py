"""Built-in 'analyze this subapp' aggregation for bidirectional sub-applications.

Read-only. Filters interaction events by bundle/component/session/time and
returns per-type / per-session / per-hour distributions plus an optional bounded
raw digest. The tool definition lives here too so the runtime file stays small;
both must stay in sync with ``_execute_subapp_observe``'s bounded-digest
treatment.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.errors import AppError


ANALYZE_TOOL_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "subapp_analyze_events",
        "description": (
            "Aggregate interaction events for the built-in 'analyze this subapp' "
            "workflow. Returns counts by event_type, by session, and an hourly "
            "distribution, plus optional bounded raw event digests (payload "
            "summaries are size/field-limited like subapp_observe). Use it to "
            "summarize user behavior, find error patterns, measure engagement, "
            "or tailor guidance from a published sub-application. Filters: "
            "bundle_id or component_id (the published app), session_id (one "
            "usage session), time_range, purpose (free-text goal, advisory "
            "only), group_by (event_type|session|hour), include_raw_events, "
            "limit. This tool never writes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "bundle_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "Published subapp bundle id.",
                },
                "component_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Component id of the published subapp (bundle_<id>).",
                },
                "session_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "Optional single usage session filter.",
                },
                "purpose": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Free-text analysis goal (advisory only).",
                },
                "time_range": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string", "format": "date-time"},
                        "to": {"type": "string", "format": "date-time"},
                    },
                    "additionalProperties": False,
                    "description": "Optional ISO-8601 window on created_at.",
                },
                "group_by": {
                    "type": "string",
                    "enum": ["event_type", "session", "hour"],
                    "description": "Primary aggregation axis (default event_type).",
                },
                "include_raw_events": {
                    "type": "boolean",
                    "description": "Include a bounded digest of the most recent raw events.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Raw digest size when include_raw_events is true (default 20).",
                },
            },
            "additionalProperties": False,
        },
    },
}


def execute_analyze_events(runtime: Any, arguments: dict[str, Any]) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Aggregate interaction events for the built-in subapp analysis entry.

    ``runtime`` is the AgentToolRuntime (provides ``extensions.db``,
    ``workspace_id``, ``_parse_iso_datetime``, ``_subapp_event_digest``,
    ``_success``).
    """
    from sqlalchemy import func, select

    from app.domain.models import SubAppInteractionEvent

    db = runtime.extensions.db
    workspace_id = runtime.workspace_id

    bundle_id = arguments.get("bundle_id")
    if bundle_id is not None and (not isinstance(bundle_id, str) or not bundle_id.strip()):
        raise AppError(422, "invalid_tool_arguments", "bundle_id must be a non-empty string")
    bundle_id = bundle_id.strip() if isinstance(bundle_id, str) else None
    if bundle_id and len(bundle_id) > 64:
        raise AppError(422, "invalid_tool_arguments", "bundle_id is too long")

    component_id = arguments.get("component_id")
    if component_id is not None and (not isinstance(component_id, str) or not component_id.strip()):
        raise AppError(422, "invalid_tool_arguments", "component_id must be a non-empty string")
    component_id = component_id.strip() if isinstance(component_id, str) else None
    if component_id and len(component_id) > 120:
        raise AppError(422, "invalid_tool_arguments", "component_id is too long")

    session_id = arguments.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise AppError(422, "invalid_tool_arguments", "session_id must be a string")
    session_id = session_id.strip() if isinstance(session_id, str) else None
    if session_id and len(session_id) > 64:
        raise AppError(422, "invalid_tool_arguments", "session_id is too long")

    purpose = arguments.get("purpose")
    if purpose is not None and not isinstance(purpose, str):
        raise AppError(422, "invalid_tool_arguments", "purpose must be a string")
    purpose = purpose.strip() if isinstance(purpose, str) else None
    if purpose and len(purpose) > 500:
        raise AppError(422, "invalid_tool_arguments", "purpose is too long")

    group_by = arguments.get("group_by", "event_type")
    if group_by not in {"event_type", "session", "hour"}:
        raise AppError(422, "invalid_tool_arguments", "group_by must be event_type, session, or hour")

    include_raw = arguments.get("include_raw_events") is True
    limit = arguments.get("limit", 20)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise AppError(422, "invalid_tool_arguments", "limit must be an integer")
    limit = max(1, min(limit, 100))

    time_from: datetime | None = None
    time_to: datetime | None = None
    time_range = arguments.get("time_range")
    if time_range is not None:
        if not isinstance(time_range, dict):
            raise AppError(422, "invalid_tool_arguments", "time_range must be an object")
        unknown = set(time_range) - {"from", "to"}
        if unknown:
            raise AppError(
                422,
                "invalid_tool_arguments",
                f"time_range has unexpected field(s): {sorted(unknown)}",
            )
        time_from = runtime._parse_iso_datetime(time_range.get("from"), "time_range.from")
        time_to = runtime._parse_iso_datetime(time_range.get("to"), "time_range.to")
    if time_from is not None and time_to is not None and time_from > time_to:
        raise AppError(
            422,
            "invalid_tool_arguments",
            "time_range.from must not be after time_range.to",
        )

    filters = [SubAppInteractionEvent.workspace_id == workspace_id]
    if bundle_id:
        filters.append(SubAppInteractionEvent.bundle_id == bundle_id)
    if component_id:
        filters.append(SubAppInteractionEvent.component_id == component_id)
    if session_id:
        filters.append(SubAppInteractionEvent.session_id == session_id)
    if time_from is not None:
        filters.append(SubAppInteractionEvent.created_at >= time_from)
    if time_to is not None:
        filters.append(SubAppInteractionEvent.created_at <= time_to)

    total_count = int(
        db.scalar(select(func.count(SubAppInteractionEvent.id)).where(*filters)) or 0
    )

    type_rows = db.execute(
        select(
            SubAppInteractionEvent.event_type,
            func.count(SubAppInteractionEvent.id),
        )
        .where(*filters)
        .group_by(SubAppInteractionEvent.event_type)
        .order_by(func.count(SubAppInteractionEvent.id).desc())
    ).all()
    events_by_type: dict[str, int] = {
        str(row[0] or "unknown"): int(row[1]) for row in type_rows
    }

    events_by_session: dict[str, int] | None = None
    events_by_hour: dict[str, int] | None = None
    if group_by == "session":
        session_rows = db.execute(
            select(
                SubAppInteractionEvent.session_id,
                func.count(SubAppInteractionEvent.id),
            )
            .where(*filters)
            .group_by(SubAppInteractionEvent.session_id)
            .order_by(func.count(SubAppInteractionEvent.id).desc())
        ).all()
        events_by_session = {
            str(row[0] or "unknown"): int(row[1]) for row in session_rows
        }
    elif group_by == "hour":
        hour_expr = func.strftime("%Y-%m-%dT%H:00", SubAppInteractionEvent.created_at)
        hour_rows = db.execute(
            select(hour_expr, func.count(SubAppInteractionEvent.id))
            .where(*filters)
            .group_by(hour_expr)
            .order_by(hour_expr)
        ).all()
        events_by_hour = {str(row[0]): int(row[1]) for row in hour_rows}

    recent_events: list[dict[str, Any]] = []
    if include_raw:
        recent_rows = list(
            db.scalars(
                select(SubAppInteractionEvent)
                .where(*filters)
                .order_by(
                    SubAppInteractionEvent.created_at.desc(),
                    SubAppInteractionEvent.id.desc(),
                )
                .limit(limit)
            ).all()
        )
        recent_events = [runtime._subapp_event_digest(event) for event in recent_rows]

    # Audit ledger: record the analysis request (best-effort, never blocks).
    try:
        from app.domain.models import SubAppAnalysisRequest, utc_now

        db.add(
            SubAppAnalysisRequest(
                workspace_id=workspace_id,
                session_id=session_id,
                chat_session_id=None,
                bundle_id=bundle_id,
                component_id=component_id,
                scope="session" if session_id else "all",
                purpose=purpose or "",
                status="completed",
                message_id=None,
                error=None,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 — audit must never break analysis
        db.rollback()

    return runtime._success(
        {
            "filters_applied": {
                "bundle_id": bundle_id,
                "component_id": component_id,
                "session_id": session_id,
                "purpose": purpose,
                "group_by": group_by,
                "time_range": {
                    "from": time_from.isoformat() if time_from else None,
                    "to": time_to.isoformat() if time_to else None,
                },
            },
            "total_events": total_count,
            "events_by_type": events_by_type,
            "events_by_session": events_by_session,
            "events_by_hour": events_by_hour,
            "recent_events": recent_events,
            "recent_events_truncated": total_count > len(recent_events),
        },
        {
            "tool": "subapp_analyze_events",
            "total_events": total_count,
            "event_type_count": len(events_by_type),
            "recent_events_returned": len(recent_events),
        },
        [],
    )
