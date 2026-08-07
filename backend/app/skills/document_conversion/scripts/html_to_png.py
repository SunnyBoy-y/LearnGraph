#!/usr/bin/env python3
"""Screenshot a local HTML file to PNG with headless Chromium (offline).

Thin CLI wrapper over ``learngraph_tasks.html_to_png``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from learngraph_tasks import html_to_png


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Screenshot a local HTML file to PNG (offline Chromium).")
    parser.add_argument("--input", required=True, help="workspace-relative .html path")
    parser.add_argument("--output", required=True, help="workspace-relative .png path")
    parser.add_argument("--width", type=int, default=1280, help="viewport width (default 1280)")
    parser.add_argument("--height", type=int, default=720, help="viewport height (default 720)")
    parser.add_argument("--full-page", action="store_true", help="capture the whole page")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = _safe(args.input)
    dst = _safe(args.output)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    png = html_to_png(src, dst, width=args.width, height=args.height, full_page=args.full_page)
    data = png.read_bytes()
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
                "output": str(dst),
                "bytes": len(data),
                "width": args.width,
                "height": args.height,
                "full_page": args.full_page,
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
