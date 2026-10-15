"""Deterministic, side-effect-free contracts shared by agent tools."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from typing import Any

from app.core.errors import AppError


AGENT_TOOL_RESULT_MAX_BYTES = 128 * 1024


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def bounded_json(value: Any, max_bytes: int, *, result_ref: str | None = None) -> tuple[str, bool]:
    """Enforce the limit *after* encoding the preview envelope."""
    encoded = canonical_json(value)
    raw = encoded.encode("utf-8")
    if len(raw) <= max_bytes:
        return encoded, False
    envelope: dict[str, Any] = {"truncated": True, "total_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(), "preview": ""}
    if result_ref:
        envelope.update(result_ref=result_ref, next_cursor=None,
                        recovery="Use read_tool_result with result_ref to read the complete result")
    else:
        envelope["recovery"] = "Request a narrower range or fewer fields"
    if len(canonical_json(envelope).encode()) > max_bytes:
        minimal = canonical_json({"truncated": True})
        if len(minimal.encode()) > max_bytes:
            raise ValueError("JSON budget is too small for a valid envelope")
        return minimal, True
    low, high = 0, min(len(encoded), max_bytes)
    while low < high:
        middle = (low + high + 1) // 2
        envelope["preview"] = encoded[:middle]
        if len(canonical_json(envelope).encode("utf-8")) <= max_bytes:
            low = middle
        else:
            high = middle - 1
    envelope["preview"] = encoded[:low]
    return canonical_json(envelope), True


def text_page(text: str, *, key: str, max_chars: int = 16384,
              cursor: str | None = None, start_line: int | None = None,
              end_line: int | None = None) -> dict[str, Any]:
    if not 1 <= max_chars <= 1048576:
        raise AppError(422, "invalid_page_size", "max_chars must be between 1 and 1048576")
    version = hashlib.sha256(text.encode("utf-8")).hexdigest()
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    lo = (start_line or 1) - 1
    hi = min(end_line if end_line is not None else total_lines, total_lines)
    offset = 0
    if cursor:
        try:
            if len(cursor) > 2048:
                raise ValueError("cursor too long")
            state = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            if state["version"] != version or state["key"] != key:
                raise AppError(409, "read_version_changed", "File or result changed; restart the read")
            lo, hi, offset = state["lo"], state["hi"], state["offset"]
            if any(type(v) is not int for v in (lo, hi, offset)):
                raise ValueError("cursor positions must be integers")
            if start_line is not None and start_line - 1 != lo:
                raise ValueError("cursor range differs from start_line")
            if end_line is not None and min(end_line, total_lines) != hi:
                raise ValueError("cursor range differs from end_line")
        except AppError:
            raise
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            raise AppError(422, "invalid_read_cursor", "Use the next_cursor returned by the previous read") from exc
    if not 0 <= lo <= hi <= total_lines:
        raise AppError(422, "sandbox_file_range_out_of_bounds", "Requested line range is outside the file")
    selected = "".join(lines[lo:hi])
    if not 0 <= offset <= len(selected):
        raise AppError(422, "invalid_read_cursor", "Cursor is outside the selected range")
    end = min(len(selected), offset + max_chars)
    page = selected[offset:end]
    has_more = end < len(selected)
    next_cursor = None
    if has_more:
        state = {"version": version, "key": key, "lo": lo, "hi": hi, "offset": end}
        next_cursor = base64.urlsafe_b64encode(canonical_json(state).encode()).decode()
    absolute_start = sum(len(line) for line in lines[:lo]) + offset
    actual_start = lo + 1 + selected[:offset].count("\n") if total_lines else None
    actual_end = (actual_start + page.count("\n") - int(page.endswith("\n"))) if page and actual_start else actual_start
    return {"content": page, "sha256": version, "version": version,
            "total_bytes": len(text.encode()), "total_chars": len(text),
            "total_lines": total_lines, "start_line": actual_start, "end_line": actual_end,
            "returned_char_start": absolute_start, "returned_char_end": absolute_start + len(page),
            "partial_last_line": bool(has_more and page and not page.endswith("\n")),
            "truncated": has_more, "has_more": has_more, "next_cursor": next_cursor}


def _pointer(path: str) -> list[str]:
    if path == "":
        return []
    if not isinstance(path, str) or not path.startswith("/") or re.search(r"~(?![01])", path):
        raise AppError(422, "json_pointer_invalid", "Use an RFC 6901 JSON Pointer")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _index(token: str, length: int, *, add: bool = False) -> int:
    if add and token == "-":
        return length
    if not re.fullmatch(r"0|[1-9][0-9]*", token):
        raise AppError(422, "json_pointer_invalid", "Array index must be a canonical non-negative integer")
    index = int(token)
    if index >= length + int(add):
        raise AppError(422, "json_pointer_not_found", "Array index does not exist")
    return index


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(v, right[k]) for k, v in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if type(left) is not type(right) and not (isinstance(left, (int, float)) and isinstance(right, (int, float))):
        return False
    return left == right


def apply_json_patch(document: Any, operations: list[dict[str, Any]]) -> Any:
    """Atomic in-memory RFC 6902 subset: test/add/remove/replace."""
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        raise AppError(422, "json_patch_invalid", "Provide between 1 and 100 patch operations")
    result = copy.deepcopy(document)
    for ordinal, operation in enumerate(operations):
        if not isinstance(operation, dict) or operation.get("op") not in {"test", "add", "remove", "replace"}:
            raise AppError(422, "json_patch_invalid", "Supported operations: test/add/remove/replace", {"operation": ordinal})
        op = operation["op"]
        if "path" not in operation or not isinstance(operation["path"], str):
            raise AppError(422, "json_patch_invalid", "Each operation requires a string path")
        if op in {"test", "add", "replace"} and "value" not in operation:
            raise AppError(422, "json_patch_invalid", "This operation requires value")
        tokens = _pointer(operation["path"])
        if not tokens:
            if op == "remove":
                raise AppError(422, "json_patch_invalid", "Removing the whole document is not supported")
            if op == "test":
                if not _json_equal(result, operation["value"]):
                    raise AppError(409, "json_patch_test_failed", "JSON test failed; no changes were committed")
            else:
                result = copy.deepcopy(operation["value"])
            continue
        parent = result
        try:
            for token in tokens[:-1]:
                parent = parent[_index(token, len(parent))] if isinstance(parent, list) else parent[token]
            token = tokens[-1]
            if isinstance(parent, list):
                index = _index(token, len(parent), add=op == "add")
                if op == "add":
                    parent.insert(index, copy.deepcopy(operation["value"]))
                elif op == "remove":
                    parent.pop(index)
                elif op == "replace":
                    parent[index] = copy.deepcopy(operation["value"])
                elif not _json_equal(parent[index], operation["value"]):
                    raise AppError(409, "json_patch_test_failed", "JSON test failed; no changes were committed")
            elif isinstance(parent, dict):
                if op != "add" and token not in parent:
                    raise KeyError(token)
                if op in {"add", "replace"}:
                    parent[token] = copy.deepcopy(operation["value"])
                elif op == "remove":
                    del parent[token]
                elif not _json_equal(parent[token], operation["value"]):
                    raise AppError(409, "json_patch_test_failed", "JSON test failed; no changes were committed")
            else:
                raise TypeError("parent is not an object or array")
        except (KeyError, IndexError, TypeError) as exc:
            raise AppError(422, "json_pointer_not_found", "JSON path does not exist", {"operation": ordinal, "path": operation["path"]}) from exc
    canonical_json(result)  # reject non-finite values before any caller commits
    return result
