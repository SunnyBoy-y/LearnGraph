#!/usr/bin/env python3
"""Merge multiple PDFs in order into one file (offline).

Reuses ``learngraph_tasks.pdf_merge``; pass --inputs as an ordered list.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from learngraph_tasks import pdf_merge


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge PDFs in order (offline).")
    parser.add_argument("--inputs", nargs="+", required=True, help="ordered workspace-relative .pdf paths")
    parser.add_argument("--output", required=True, help="workspace-relative .pdf path")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if len(args.inputs) < 1:
        raise RuntimeError("at least one input PDF is required")
    sources = [_safe(item) for item in args.inputs]
    for src in sources:
        if not src.is_file():
            raise RuntimeError(f"input file not found: {src}")
    dst = _safe(args.output)
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    merged = pdf_merge([str(item) for item in sources], dst)
    data = merged.read_bytes()
    print(
        json.dumps(
            {
                "status": "ok",
                "inputs": [str(item) for item in sources],
                "output": str(dst),
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
