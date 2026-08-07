#!/usr/bin/env python3
"""Extract text from a PDF, optionally for a page range (offline).

Writes UTF-8 text to --output and prints a JSON summary. Large PDFs should use
--pages to stay within the sandbox wall-time budget.
"""
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


def _parse_pages(spec: str, page_count: int) -> list[int]:
    if not spec:
        return list(range(page_count))
    wanted: list[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise RuntimeError(f"invalid page spec: {part!r}")
        start = int(m.group(1)) - 1
        end = int(m.group(2) or m.group(1)) - 1
        if start < 0 or end < start:
            raise RuntimeError(f"invalid page range: {part!r}")
        wanted.extend(range(start, end + 1))
    seen: set[int] = set()
    return [i for i in wanted if i < page_count and not (i in seen or seen.add(i))]


def extract_text(path: Path, pages: list[int]) -> str:
    import fitz

    doc = fitz.open(path)
    try:
        if doc.is_encrypted:
            raise RuntimeError("PDF is encrypted; offline decryption is unavailable")
        chunks = [doc.load_page(i).get_text("text") for i in pages]
        return "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip())
    finally:
        doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract text from a PDF (offline).")
    parser.add_argument("--input", required=True, help="workspace-relative .pdf path")
    parser.add_argument("--output", required=True, help="workspace-relative .txt path")
    parser.add_argument("--pages", default="", help='page ranges, e.g. "1-5" or "1,3-4" (1-based)')
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = _safe(args.input)
    dst = _safe(args.output)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    import fitz

    with fitz.open(src) as doc:
        if doc.is_encrypted:
            raise RuntimeError("PDF is encrypted; offline decryption is unavailable")
        page_count = doc.page_count
    pages = _parse_pages(args.pages, page_count)
    text = extract_text(src, pages)
    if not text.strip():
        raise RuntimeError("no extractable text (scanned pages have no text layer?)")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
                "output": str(dst),
                "page_count": page_count,
                "extracted_pages": len(pages),
                "chars": len(text),
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
