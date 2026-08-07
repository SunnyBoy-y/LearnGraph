#!/usr/bin/env python3
"""Verify a built frontend's dist/ is self-contained and list external refs (offline).

Scans HTML/CSS/JS for http(s):// and protocol-relative references so previews
and publishes stay fully offline (CSP / no-external constraint).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_EXTERNAL = re.compile(r"""(?:https?:)?//[^\s"'<>\\]+|url\(\s*(?:https?:)?//""", re.IGNORECASE)
_TEXT_EXT = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".svg", ".txt", ".md"}


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def scan(dist: Path, max_external: int) -> dict:
    if not dist.is_dir():
        raise RuntimeError(f"dist directory not found: {dist}")
    files = [p for p in dist.rglob("*") if p.is_file()]
    refs: dict[str, list[str]] = {}
    total = 0
    for path in files:
        if path.suffix.casefold() not in _TEXT_EXT:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits = list(dict.fromkeys(_EXTERNAL.findall(text)))
        if hits:
            refs[str(path.relative_to(dist))] = hits
            total += len(hits)
    return {
        "status": "ok",
        "dist": str(dist),
        "file_count": len(files),
        "external_files": {k: v for k, v in refs.items()},
        "external_total": total,
        "within_limit": total <= max_external,
        "max_external": max_external,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a built dist/ for external references (offline).")
    parser.add_argument("--dir", required=True, help="workspace-relative project directory")
    parser.add_argument("--max-external", type=int, default=0, help="allowed external refs (default 0)")
    args = parser.parse_args()
    root = _safe(args.dir)
    report = scan(root / "dist", args.max_external)
    if not report["within_limit"]:
        raise RuntimeError(
            f"{report['external_total']} external reference(s) exceed the limit {args.max_external}"
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
