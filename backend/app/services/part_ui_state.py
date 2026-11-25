from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import ChatSession, Message, MessagePartRecord, MessageVersion


_INTERACTIVE_UI_STATE_COMPONENT_TYPES = frozenset(
    {
        "option_group",
        "single_choice",
        "multiple_choice",
        "fill_blank",
        "short_answer_table",
        "question_batch",
    }
)
MAX_PART_UI_STATE_BYTES = 65_536
MAX_PART_UI_STATE_DEPTH = 10


def validate_part_ui_state(ui_state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ui_state, dict):
        raise AppError(422, "invalid_ui_state", "ui_state must be a JSON object")

    def _check(value: Any, depth: int) -> None:
        if depth > MAX_PART_UI_STATE_DEPTH:
            raise AppError(
                422, "invalid_ui_state", "ui_state exceeds maximum nesting depth"
            )
        if value is None or isinstance(value, (bool, int, float)):
            return
        if isinstance(value, str):
            if "\x00" in value:
                raise AppError(
                    422, "invalid_ui_state", "ui_state cannot contain null bytes"
                )
            return
        if isinstance(value, list):
            for item in value:
                _check(item, depth + 1)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str) or "\x00" in key:
                    raise AppError(
                        422, "invalid_ui_state", "ui_state keys must be valid strings"
                    )
                _check(item, depth + 1)
            return
        raise AppError(
            422, "invalid_ui_state", "ui_state contains unsupported value type"
        )

    _check(ui_state, 0)
    try:
        encoded = json.dumps(ui_state, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise AppError(
            422, "invalid_ui_state", "ui_state must be JSON serializable"
        ) from exc
    if len(encoded.encode("utf-8")) > MAX_PART_UI_STATE_BYTES:
        raise AppError(422, "invalid_ui_state", "ui_state exceeds maximum size limit")
    return dict(json.loads(encoded))


def update_part_ui_state(
    db: Session,
    *,
    workspace_id: str,
    session_id: str,
    part_id: str,
    ui_state: dict[str, Any],
    timeline_message_ids: Callable[[str], set[str]] | None = None,
    verify_session_write: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.workspace_id == workspace_id,
            ChatSession.id == session_id,
        )
    )
    if session is None:
        raise AppError(404, "not_found", "Session not found in this workspace")

    part_record = db.scalar(
        select(MessagePartRecord).where(
            MessagePartRecord.workspace_id == workspace_id,
            MessagePartRecord.id == part_id,
        )
    )
    if part_record is None:
        raise AppError(404, "message_part_not_found", "Message part not found")

    version = db.scalar(
        select(MessageVersion).where(
            MessageVersion.workspace_id == workspace_id,
            MessageVersion.id == part_record.message_version_id,
        )
    )
    if version is None:
        raise AppError(404, "message_part_not_found", "Message version not found")

    message = db.scalar(
        select(Message).where(
            Message.workspace_id == workspace_id,
            Message.id == version.message_id,
        )
    )
    if message is None:
        raise AppError(404, "message_part_not_found", "Message not found")

    if message.session_id != session.id:
        allowed_ids = (
            timeline_message_ids(session.id)
            if timeline_message_ids is not None
            else set()
        )
        if message.id not in allowed_ids:
            raise AppError(
                404,
                "message_part_not_in_session",
                "Message part does not belong to this session",
            )
        if verify_session_write is not None:
            verify_session_write(message.session_id)

    part_data = part_record.data if isinstance(part_record.data, dict) else {}
    component_type = part_data.get("component_type")
    is_supported = part_record.part_type == "quiz" or (
        part_record.part_type == "component"
        and isinstance(component_type, str)
        and component_type in _INTERACTIVE_UI_STATE_COMPONENT_TYPES
    )
    if not is_supported:
        raise AppError(
            409,
            "part_ui_state_unsupported",
            "This message part does not support interactive UI state persistence",
        )

    cleaned_state = validate_part_ui_state(ui_state)
    part_record.data = {**part_data, "ui_state": cleaned_state}

    if version.version == message.version:
        updated_parts: list[dict[str, Any]] = []
        matched = False
        for snapshot in message.parts or []:
            if isinstance(snapshot, dict) and snapshot.get("id") == part_record.id:
                matched = True
                snap_data = (
                    snapshot.get("data")
                    if isinstance(snapshot.get("data"), dict)
                    else {}
                )
                updated_parts.append(
                    {
                        **snapshot,
                        "data": {**snap_data, "ui_state": cleaned_state},
                    }
                )
            else:
                updated_parts.append(snapshot)
        if matched:
            message.parts = updated_parts
        else:
            part_records = db.scalars(
                select(MessagePartRecord)
                .where(
                    MessagePartRecord.workspace_id == workspace_id,
                    MessagePartRecord.message_version_id == version.id,
                )
                .order_by(MessagePartRecord.ordinal)
            ).all()
            message.parts = [
                {
                    "id": item.id,
                    "type": item.part_type,
                    "status": item.status,
                    "content": item.content,
                    "data": item.data or {},
                    "sequence": item.ordinal,
                }
                for item in part_records
                if not (
                    isinstance(item.data, dict)
                    and isinstance(item.data.get("artifact_registry"), dict)
                    and item.data["artifact_registry"].get("suppressed") is True
                )
            ]

    db.commit()
    return {
        "part_id": part_record.id,
        "session_id": session.id,
        "message_id": message.id,
        "ui_state": cleaned_state,
    }
