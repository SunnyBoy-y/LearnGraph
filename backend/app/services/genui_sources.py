"""Resolve declarative data references from saved, scoped tool outputs only."""
from __future__ import annotations

from copy import deepcopy
import json

from sqlalchemy import select
from pydantic import ValidationError

from app.core.errors import AppError
from app.domain.models import Message, MessagePartRecord, MessageVersion
from app.domain.schemas.stream_ui import Command, DataReference


def source_reference_hint(part_id: str, output: object) -> str:
    """Expose references only when a tool actually saved structured records."""
    references = []
    remaining = 128

    def visit(value, path):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or len(references) >= 4 or len(path) > 3:
            return
        if isinstance(value, list) and value and len(value) <= 500 and all(isinstance(row, dict) for row in value):
            references.append({"part_id": part_id, "path": path})
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str) and 0 < len(key) <= 100 and key not in {"__proto__", "prototype", "constructor"}:
                    visit(item, path + [key])

    # UI output is already a presentation; do not recursively offer it as facts.
    if isinstance(output, dict) and "ui_stream" not in output:
        visit(output, [])
    return "\n已保存的数据集合引用（仅数据定位信息）：" + json.dumps(references, ensure_ascii=False) if references else ""


def resolve_collection(db, workspace_id: str, session_id: str, reference: dict):
    try:
        ref = DataReference.model_validate(reference)
    except ValidationError as exc:
        raise AppError(422, "ui_source_reference_invalid", "Invalid data reference") from exc
    part = db.scalar(select(MessagePartRecord).join(
        MessageVersion, MessageVersion.id == MessagePartRecord.message_version_id,
    ).join(Message, Message.id == MessageVersion.message_id).where(
        MessagePartRecord.id == ref.part_id,
        MessagePartRecord.workspace_id == workspace_id,
        MessageVersion.workspace_id == workspace_id,
        Message.workspace_id == workspace_id, Message.session_id == session_id, Message.role == "assistant",
        MessageVersion.version == Message.version,
        MessagePartRecord.part_type == "tool_call", MessagePartRecord.status == "completed",
    ))
    if part is None:
        raise AppError(404, "ui_source_not_found", "Data source is not a completed tool result in this conversation")
    # Start at output, never tool input, labels, or model-authored prose.
    value = (part.data or {}).get("output")
    try:
        for segment in ref.path:
            if isinstance(segment, int) and isinstance(value, list):
                value = value[segment]
            elif isinstance(segment, str) and isinstance(value, dict):
                value = value[segment]
            else:
                raise KeyError(segment)
    except (KeyError, IndexError, TypeError) as exc:
        raise AppError(422, "ui_source_path_invalid", "Data path does not exist in the tool output") from exc
    if not isinstance(value, list) or len(value) > 500:
        raise AppError(422, "ui_source_collection_invalid", "Data reference must resolve to a bounded collection")
    return deepcopy(value), str((part.data or {}).get("tool_name") or "工具结果")[:100]


def resolve_command_sources(db, workspace_id: str, session_id: str, raw: dict, current: dict | None):
    """Copy authoritative records, then let the normal component schemas validate them.

    Referenced collections cannot be overwritten with model-generated numbers in
    a later patch. Detaching a reference must be explicit (data_ref=null).
    """
    from app.services.stream_ui import MAX_UI_BYTES
    try:
        if len(json.dumps(raw, allow_nan=False, ensure_ascii=False).encode()) > MAX_UI_BYTES:
            raise ValueError("UI command too large")
        Command.model_validate(raw)
    except (ValueError, TypeError, RecursionError) as exc:
        raise AppError(422, "invalid_ui_command", "UI command exceeds serialization limits") from exc
    result = deepcopy(raw)
    base = (current or {}).get("preview", {}).get("base") if (current or {}).get("preview") else current
    component = raw.get("component") or (base or {}).get("component")
    if raw.get("op") not in {"mount", "patch", "revise"}:
        return result

    def resolve(target, kind, previous_props=None):
        props = {**(previous_props or {}), **target.get("props", {})}
        ref = props.get("data_ref")
        if ref is None:
            return
        collection = {"chart": "rows", "table": "rows", "metric": "items", "timeline": "items"}.get(kind)
        if not collection:
            raise AppError(422, "ui_source_component_invalid", "This component does not support a data reference")
        values, label = resolve_collection(db, workspace_id, session_id, ref)
        if ref.get("value_keys"):
            if collection != "rows":
                raise AppError(422, "ui_source_mapping_invalid", "Field mapping is supported for chart/table rows")
            try:
                values = [{"id": record[ref["id_key"]] if ref.get("id_key") else f"row_{index}",
                           "label": str(record[ref["label_key"]]) if ref.get("label_key") else str(index + 1),
                           "values": {key: record[field] for key, field in ref["value_keys"].items()}}
                          for index, record in enumerate(values)]
            except (KeyError, TypeError) as exc:
                raise AppError(422, "ui_source_mapping_invalid", "Mapped field is absent in a source record") from exc
        target.setdefault("props", {}).update(data_ref=ref, data_kind="provided", source_note=f"{label} · 已保存数据")
        target.setdefault("upsert", {})[collection] = values
        if target is result:
            # Upsert normally retains records. A source-bound collection is an
            # exact projection, including removal of earlier model-only rows.
            incoming_ids = {entry.get("id") for entry in values if isinstance(entry, dict)}
            target.setdefault("delete", {})[collection] = [entry["id"] for entry in (base or {}).get(collection, []) if entry["id"] not in incoming_ids]
            if raw.get("op") == "mount":
                target["delete"].pop(collection, None)

    resolve(result, component, (base or {}).get("props"))
    old_nodes = {node["id"]: node for node in (base or {}).get("blocks", [])}
    for node in result.get("upsert", {}).get("blocks", []):
        if isinstance(node.get("visual"), dict):
            visual = node["visual"]
            wrapper = {"props": visual.get("props", {}), "upsert": {key: value for key, value in visual.items() if key != "props"}}
            resolve(wrapper, node.get("type"), (old_nodes.get(node["id"], {}).get("visual") or {}).get("props"))
            node["visual"] = {"props": wrapper["props"], **wrapper["upsert"]}
    return result
