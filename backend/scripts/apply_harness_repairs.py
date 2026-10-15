"""Apply reviewed exact-anchor repairs without changing unrelated bytes.
Usage: python backend/scripts/apply_harness_repairs.py PLAN.json --apply
All anchors and Python syntax are checked before any source file is written.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def prepare(root: Path, changes: list[dict]) -> tuple[dict, dict]:
    before, pending = {}, {}
    for change in changes:
        path = (root / change["path"]).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"Not an existing workspace file: {change['path']}")
        if path not in before:
            before[path] = path.read_bytes()
            pending[path] = before[path]
        raw = pending[path]
        newline = b"\r\n" if b"\r\n" in raw else b"\n"
        encode = lambda value: value.encode("utf-8").replace(b"\n", newline)
        new = encode(change["new"])
        if change.get("kind", "replace") == "between":
            start, end = encode(change["start"]), encode(change["end"])
            if start not in raw and new in raw:
                continue
            if raw.count(start) != 1 or raw.count(end) != 1:
                raise ValueError(f"Non-unique range anchor: {change['path']}")
            lo, hi = raw.index(start), raw.index(end)
            if hi <= lo:
                raise ValueError("Reversed replacement range")
            pending[path] = raw[:lo] + new + raw[hi:]
        else:
            old = encode(change["old"])
            count = change.get("count", 1)
            if old not in raw and new in raw:
                continue
            if raw.count(old) != count:
                raise ValueError(f"Expected {count} anchors, got {raw.count(old)}: {change['path']} / {change.get('label', '')}")
            pending[path] = raw.replace(old, new, count)
    for path, raw in pending.items():
        if path.suffix == ".py":
            compile(raw, str(path), "exec")
        if path.suffix == ".json":
            json.loads(raw)
    return before, pending


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    plan = (root / args.plan).resolve()
    if root not in plan.parents:
        raise SystemExit("Plan must be inside workspace")
    before, pending = prepare(root, json.loads(plan.read_text(encoding="utf-8")))
    if any(path.read_bytes() != raw for path, raw in before.items()):
        raise SystemExit("A source file changed during preflight; nothing written")
    for path, raw in pending.items():
        changed = raw != before[path]
        if args.apply and changed:
            path.write_bytes(raw)
        print(json.dumps({"path": path.relative_to(root).as_posix(),
            "changed": changed, "applied": args.apply and changed,
            "sha256": hashlib.sha256(raw).hexdigest()}))


if __name__ == "__main__":
    main()
