#!/usr/bin/env python3
"""Extract a page range of a PDF into a new file (offline).

Uses PyMuPDF to import pages so dimensions are preserved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def split_range(src: Path, dst: Path, start: int, end: int) -> tuple[int, int, int]:
    import fitz

    doc = fitz.open(src)
    try:
        if doc.is_encrypted:
            raise RuntimeError("PDF is encrypted; offline decryption is unavailable")
        page_count = doc.page_count
        lo = max(0, start - 1)
        hi = min(page_count, end)
        if lo >= hi:
            raise RuntimeError("empty page range after clamping")
        out = fitz.open()
        try:
            out.insert_pdf(doc, from_page=lo, to_page=hi - 1)
        finally:
            out.save(dst, garbage=4, deflate=True)
            out.close()
        return page_count, lo + 1, hi
    finally:
        doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract a page range of a PDF (offline).")
    parser.add_argument("--input", required=True, help="workspace-relative .pdf path")
    parser.add_argument("--output", required=True, help="workspace-relative .pdf path")
    parser.add_argument("--start", type=int, default=1, help="first page to keep (1-based)")
    parser.add_argument("--end", type=int, required=True, help="last page to keep (1-based, inclusive)")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = _safe(args.input)
    dst = _safe(args.output)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    page_count, lo, hi = split_range(src, dst, args.start, args.end)
    data = dst.read_bytes()
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
                "output": str(dst),
                "source_pages": page_count,
                "kept_pages": [lo, hi],
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
