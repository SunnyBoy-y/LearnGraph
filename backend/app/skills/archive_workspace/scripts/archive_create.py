#!/usr/bin/env python3
"""Create a zip archive from workspace files/directories (offline, zip-slip-safe)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def create(output: Path, inputs: list[Path], overwrite: bool) -> int:
    if output.exists() and not overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    if not inputs:
        raise RuntimeError("at least one input is required")
    count = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for item in inputs:
            if item.is_dir():
                for member in sorted(item.rglob("*")):
                    if member.is_file():
                        bundle.write(member, member.relative_to(item.parent))
                        count += 1
            elif item.is_file():
                bundle.write(item, item.name)
                count += 1
            else:
                raise FileNotFoundError(str(item))
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a zip archive from workspace paths (offline).")
    parser.add_argument("--inputs", nargs="+", required=True, help="workspace-relative files/directories")
    parser.add_argument("--output", required=True, help="workspace-relative .zip path")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    inputs = [_safe(item) for item in args.inputs]
    output = _safe(args.output)
    count = create(output, inputs, args.overwrite)
    data = output.read_bytes()
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(output),
                "files": count,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
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
