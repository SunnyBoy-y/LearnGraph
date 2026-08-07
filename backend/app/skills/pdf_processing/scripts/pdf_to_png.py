#!/usr/bin/env python3
"""Render one page of a PDF to PNG for visual verification (offline, fitz)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def render_page(src: Path, dst: Path, page_index: int, dpi: int) -> tuple[int, int]:
    import fitz

    doc = fitz.open(src)
    try:
        if doc.is_encrypted:
            raise RuntimeError("PDF is encrypted; offline decryption is unavailable")
        if page_index < 0 or page_index >= doc.page_count:
            raise RuntimeError(f"page {page_index + 1} out of range (pages: {doc.page_count})")
        page = doc.load_page(page_index)
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        dst.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(dst))
        return pix.width, pix.height
    finally:
        doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Render one PDF page to PNG (offline).")
    parser.add_argument("--input", required=True, help="workspace-relative .pdf path")
    parser.add_argument("--output", required=True, help="workspace-relative .png path")
    parser.add_argument("--page", type=int, default=1, help="page to render (1-based)")
    parser.add_argument("--dpi", type=int, default=150, help="render DPI (default 150)")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = _safe(args.input)
    dst = _safe(args.output)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    width, height = render_page(src, dst, args.page - 1, args.dpi)
    data = dst.read_bytes()
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
                "output": str(dst),
                "page": args.page,
                "width": width,
                "height": height,
                "dpi": args.dpi,
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
