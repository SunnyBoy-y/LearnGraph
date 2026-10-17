"""Scoped, durable tool output with bounded model previews and recoverable pages."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from app.core.errors import AppError
from app.services.harness_contracts import canonical_json, text_page


class ToolResultStore:
    def __init__(self, storage_root: str, workspace_id: str, actor_id: str, chat_session_id: str):
        scope = hashlib.sha256(canonical_json([workspace_id, actor_id, chat_session_id]).encode()).hexdigest()
        self.root = Path(storage_root).resolve() / "tool-results" / scope

    def save(self, value: dict[str, Any]) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        ref = "result_" + uuid.uuid4().hex
        target = self.root / (ref + ".json")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(canonical_json(value), encoding="utf-8")
        temporary.replace(target)
        return ref

    def read(self, ref: str, *, field: str | None = None, cursor: str | None = None,
             max_chars: int = 16000, pattern: str | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"result_[0-9a-f]{32}", ref):
            raise AppError(422, "invalid_result_ref", "Use a result_ref returned by a tool")
        try:
            text = (self.root / (ref + ".json")).read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise AppError(404, "tool_result_not_found", "Result was not found in this session") from exc
        if field:
            value = json.loads(text)
            if field not in value and isinstance(value.get("details"), dict):
                value = value["details"]
            if field not in value:
                raise AppError(422, "tool_result_field_not_found", "Requested result field does not exist")
            text = value[field] if isinstance(value[field], str) else canonical_json(value[field])
        if pattern:
            # Literal search avoids executing untrusted regexes over large outputs.
            text = "\n".join(f"{i}: {line}" for i, line in enumerate(text.splitlines(), 1) if pattern in line)
        return {"result_ref": ref, "field": field, **text_page(
            text, key=f"{ref}:{field}:{pattern}", cursor=cursor, max_chars=max_chars)}


def result_preview(value: dict[str, Any], ref: str, max_bytes: int) -> str:
    """Keep control fields and stderr independently of potentially huge stdout."""
    control = {k: value[k] for k in ("status", "exit_code", "error", "error_class", "timed_out", "job_id", "next_output_cursor") if k in value}
    control.update(result_ref=ref, truncated=True,
                   recovery="Use sandbox_read_result with result_ref; field='stdout' or 'stderr' selects a stream")
    # Leave enough room for JSON escaping and control fields even at 4 KiB.
    stream_budget = max(128, (max_bytes - 1500) // 4)
    for key in ("message", "stderr", "stdout"):
        if key in value:
            control[key] = str(value[key])[:stream_budget]
    if not any(k in value for k in ("message", "stderr", "stdout")):
        control["preview"] = canonical_json(value)[:16000]
    while len(canonical_json(control).encode("utf-8")) > max_bytes:
        keys = [key for key in ("preview", "stdout", "stderr", "message") if len(control.get(key, "")) > 32]
        if not keys:
            raise ValueError("Tool preview control fields exceed the output budget")
        key = max(keys, key=lambda k: len(control[k]))
        control[key] = control[key][:len(control[key]) // 2]
    return canonical_json(control)
