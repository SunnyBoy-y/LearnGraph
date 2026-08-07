#!/usr/bin/env python3
"""Transform a JSON document: select fields, filter rows, limit (offline)."""
from __future__ import annotations

import argparse
import hashlib
import json
import operator
import re
import sys
from pathlib import Path

_OPS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "lt": operator.lt,
    "ge": operator.ge,
    "le": operator.le,
    "contains": lambda a, b: b in a,
    "in": lambda a, b: a in b,
}


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def _dig(obj, dotted: str):
    current = obj
    for part in dotted.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if 0 <= idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def transform(data, select: list[str], filters: list[tuple[str, str, str]], limit: int):
    items = data if isinstance(data, list) else [data]
    result: list = []
    for item in items:
        if not isinstance(item, dict):
            if select or filters:
                raise RuntimeError("select/filter require object items")
            result.append(item)
            continue
        ok = True
        for key, op, value in filters:
            actual = _dig(item, key)
            fn = _OPS.get(op)
            if fn is None:
                raise RuntimeError(f"unsupported filter op: {op}")
            if op == "contains":
                ok = value in str(actual)
            elif op == "in":
                ok = str(actual) in [part.strip() for part in value.split(",")]
            else:
                if isinstance(actual, (int, float)) and not isinstance(actual, bool):
                    try:
                        compare = float(value) if isinstance(actual, float) else int(value)
                    except ValueError:
                        compare = value
                else:
                    compare = value
                if not fn(actual, compare):
                    ok = False
                    break
        if ok:
            if select:
                picked = {}
                for key in select:
                    picked[key] = _dig(item, key)
                result.append(picked)
            else:
                result.append(item)
        if limit and len(result) >= limit:
            break
    return result if isinstance(data, list) else (result[0] if result else None)


def _parse_filter(raw: list[str]) -> list[tuple[str, str, str]]:
    parsed: list[tuple[str, str, str]] = []
    for item in raw:
        m = re.fullmatch(r"([A-Za-z0-9_.]+)=([a-z]+)=(.+)", item)
        if not m:
            raise RuntimeError(f"invalid filter (expected key=op=value): {item!r}")
        key, op, value = m.group(1), m.group(2), m.group(3)
        parsed.append((key, op, value))
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Transform a JSON document (offline).")
    parser.add_argument("--input", required=True, help="workspace-relative .json path")
    parser.add_argument("--output", required=True, help="workspace-relative .json path")
    parser.add_argument("--select", nargs="+", default=[], help="dotted fields to keep")
    parser.add_argument("--filter", nargs="+", default=[], metavar="key=op=value",
                        help="e.g. score=ge=60 name=contains=张")
    parser.add_argument("--limit", type=int, default=0, help="max items to keep")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = _safe(args.input)
    dst = _safe(args.output)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON: {exc}") from exc
    filters = _parse_filter(args.filter)
    result = transform(data, args.select, filters, args.limit)
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    dst.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
                "output": str(dst),
                "items": len(result) if isinstance(result, list) else 1,
                "bytes": len(text.encode("utf-8")),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
