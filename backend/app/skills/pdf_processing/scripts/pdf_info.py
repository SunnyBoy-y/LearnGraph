#!/usr/bin/env python3
"""Report PDF metadata (pages, size, encryption, title) without extracting text (offline).

Uses PyMuPDF (fitz) so we also learn page dimensions. Prints a small JSON
summary; never renders or extracts content here.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def pdf_info(path: Path) -> dict:
    import fitz

    doc = fitz.open(path)
    try:
        encrypted = doc.is_encrypted
        pages = []
        for page in doc:
            rect = page.rect
            pages.append({"index": page.number, "width": round(rect.width, 1), "height": round(rect.height, 1)})
        meta = {k: v for k, v in doc.metadata.items() if v}
        return {
            "page_count": len(pages),
            "encrypted": bool(encrypted),
            "needs_password": bool(encrypted and doc.needs_pass),
            "pages": pages,
            "metadata": meta,
        }
    finally:
        doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Report PDF metadata (offline).")
    parser.add_argument("--input", required=True, help="workspace-relative .pdf path")
    args = parser.parse_args()
    src = _safe(args.input)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    info = pdf_info(src)
    info.update({"status": "ok", "input": str(src)})
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
