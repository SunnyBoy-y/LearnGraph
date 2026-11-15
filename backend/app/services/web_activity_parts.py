"""Persist actual native-search activity in the existing tool-part event stream."""
from __future__ import annotations

from typing import Any


def push_native_web_activity(service: Any, activity: dict, *, session_id: str, message_id: str,
                             version_id: str, sequence: int, ordinal: int, parts: list):
    from app.domain.models import MessagePartRecord

    records = getattr(service, "_native_web_activity_records", None)
    if records is None:
        records = service._native_web_activity_records = {}
    key = (version_id, str(activity.get("id") or "native-web"))
    state = activity.get("status")
    if state not in {"pending", "streaming", "completed", "failed"}:
        return [], sequence, ordinal
    record = records.get(key)
    if record is not None and record.status == state and record.data.get("web_activity") == activity:
        return [], sequence, ordinal
    events = []
    if record is None:
        record = service.message_parts.add(MessagePartRecord(
            workspace_id=service.workspace_id, message_version_id=version_id, ordinal=ordinal,
            part_type="tool_call", status=state, content="",
            data={"tool_name": "native_web_search" if activity.get("operation") == "search" else "native_web_read",
                  "native": True, "web_activity": activity},
        ))
        records[key] = record
        parts.append(record)
        ordinal += 1
        event_type = "part.started"
    else:
        record.status = state
        record.data = {**record.data, "web_activity": activity}
        event_type = "part.completed" if state == "completed" else ("part.failed" if state == "failed" else "part.delta")
    record.content = {"pending": "", "streaming": "", "completed": "原生联网操作已完成", "failed": "原生联网操作失败"}[state]
    event = service._append_event(
        session_id=session_id, message_id=message_id, message_version_id=version_id,
        part_id=record.id, sequence=sequence, event_type=event_type,
        payload={"part": service._part_snapshot(record.id, "tool_call", state, record.content, record.data, sequence=record.ordinal)},
    )
    events.append(service._encode_event(event))
    return events, sequence + 1, ordinal
