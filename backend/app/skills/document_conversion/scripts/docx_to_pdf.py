#!/usr/bin/env python3
"""Convert .docx to PDF offline (mammoth HTML -> Chromium print).

Thin CLI wrapper over ``learngraph_tasks.docx_to_pdf``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from learngraph_tasks import docx_to_pdf


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert .docx to PDF (offline, CJK-safe).")
    parser.add_argument("--input", required=True, help="workspace-relative .docx path")
    parser.add_argument("--output", required=True, help="workspace-relative .pdf path")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = _safe(args.input)
    dst = _safe(args.output)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    pdf = docx_to_pdf(src, dst)
    data = pdf.read_bytes()
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
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
