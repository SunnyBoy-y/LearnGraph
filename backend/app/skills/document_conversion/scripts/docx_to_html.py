#!/usr/bin/env python3
"""Convert .docx to a standalone, CJK-safe HTML string/file (offline).

Thin CLI wrapper over ``learngraph_tasks.docx_to_html`` so skills compose the
same pipeline as the image's own runner tasks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from learngraph_tasks import docx_to_html


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert .docx to standalone HTML (CJK-safe, offline).")
    parser.add_argument("--input", required=True, help="workspace-relative .docx path")
    parser.add_argument("--output", required=True, help="workspace-relative .html path")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = _safe(args.input)
    dst = _safe(args.output)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    html = docx_to_html(src)
    if not html.strip():
        raise RuntimeError("docx produced empty HTML")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(html, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
                "output": str(dst),
                "chars": len(html),
                "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
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
