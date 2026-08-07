#!/usr/bin/env python3
"""Render a built frontend's dist/index.html to PNG/PDF preview (offline).

Thin CLI wrapper over ``learngraph_tasks.html_to_png`` / ``html_to_pdf``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from learngraph_tasks import html_to_pdf, html_to_png


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a built dist/index.html to PNG/PDF (offline).")
    parser.add_argument("--dir", required=True, help="workspace-relative project directory")
    parser.add_argument("--output", default="", help="workspace-relative output (default <dir>/preview.png)")
    parser.add_argument("--format", choices=["png", "pdf"], default="png")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--full-page", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = _safe(args.dir)
    index = root / "dist" / "index.html"
    if not index.is_file():
        raise RuntimeError("dist/index.html not found; build first (build_frontend.py)")
    out = _safe(args.output) if args.output else root / f"preview.{args.format}"
    if out.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    if args.format == "png":
        result = html_to_png(index, out, width=args.width, height=args.height, full_page=args.full_page)
    else:
        result = html_to_pdf(index, out)
    data = result.read_bytes()
    print(
        json.dumps(
            {
                "status": "ok",
                "dir": str(root),
                "output": str(out),
                "format": args.format,
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
