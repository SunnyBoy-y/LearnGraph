#!/usr/bin/env python3
"""Extract readable UTF-8 text from DOC/DOCX/RTF/HTML documents (offline).

Runs only inside the LearnGraph Docker sandbox. The document is never
re-parsed on the host; this script owns extraction and writes plain text to a
workspace-relative output file, then prints a small JSON summary on stdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def _html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    return "\n".join(lines)


def extract_text(path: Path, fmt: str) -> str:
    fmt = (fmt or path.suffix.lstrip(".")).casefold()
    if fmt == "doc":
        completed = subprocess.run(
            ["/usr/bin/antiword", "-m", "UTF-8.txt", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:500]
            raise RuntimeError(f"antiword failed (exit {completed.returncode}): {detail}")
        return completed.stdout.strip()
    if fmt == "docx":
        import mammoth

        with open(path, "rb") as stream:
            return mammoth.extract_raw_text(stream).value.strip()
    if fmt == "rtf":
        from striprtf.striprtf import rtf_to_text

        return rtf_to_text(path.read_text(encoding="utf-8", errors="replace")).strip()
    if fmt in ("html", "htm", "xhtml"):
        return _html_to_text(path.read_text(encoding="utf-8", errors="replace"))
    raise RuntimeError(f"unsupported format: {fmt or '?'} (doc/docx/rtf/html)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract text from legacy/HTML documents (offline).")
    parser.add_argument("--input", required=True, help="workspace-relative source path")
    parser.add_argument("--output", required=True, help="workspace-relative target .txt path")
    parser.add_argument("--format", default="", help="doc|docx|rtf|html (default: from extension)")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output")
    args = parser.parse_args()
    src = _safe(args.input)
    dst = _safe(args.output)
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    text = extract_text(src, args.format)
    if not text.strip():
        raise RuntimeError("no extractable text")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    payload = {
        "status": "ok",
        "input": str(src),
        "output": str(dst),
        "chars": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
