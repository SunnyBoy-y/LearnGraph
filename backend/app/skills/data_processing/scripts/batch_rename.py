#!/usr/bin/env python3
"""Safely batch-rename files inside a workspace directory (offline).

Supports prefix/suffix insertion, substring replacement, and zero-padded
numbering. Never follows symlinks or renames outside the given directory.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def _split_ext(name: str) -> tuple[str, str]:
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".md", ".txt", ".csv", ".json", ".html", ".py", ".js"):
        if name.endswith(ext):
            return name[: -len(ext)], ext
    return name, ""


def _new_name(name: str, stem_map: dict[str, str], prefix: str, suffix: str, pad: int) -> str:
    stem, ext = _split_ext(name)
    if pad:
        new_stem = stem_map.get(stem, stem)
        return f"{prefix}{new_stem}{ext}"
    mapped = stem_map.get(stem, stem)
    return f"{prefix}{mapped}{suffix}{ext}"


def plan(base: Path, prefix: str, suffix: str, replace_pairs: dict[str, str], ext: str, pad: int) -> list[dict]:
    files = sorted(p for p in base.iterdir() if p.is_file() and (not ext or p.suffix.casefold() == ext.casefold()))
    plans: list[dict] = []
    for index, path in enumerate(files, start=1):
        stem = path.stem
        mapped = stem
        for old, new in replace_pairs.items():
            mapped = mapped.replace(old, new)
        stem_map = {stem: f"{index:0{pad}d}" if pad else mapped}
        new_name = _new_name(path.name, stem_map, prefix, suffix, pad)
        if not new_name or new_name == path.name:
            continue
        target = base / new_name
        if target.exists():
            raise RuntimeError(f"target already exists: {new_name}")
        plans.append({"from": str(path.relative_to(base)), "to": new_name})
    return plans


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely batch-rename files in a workspace dir (offline).")
    parser.add_argument("--dir", required=True, help="workspace-relative directory")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--suffix", default="")
    parser.add_argument("--replace", nargs="+", default=[], metavar="OLD=NEW")
    parser.add_argument("--ext", default="", help="only rename this extension (e.g. .png)")
    parser.add_argument("--pad", type=int, default=0, help="replace stem with zero-padded index")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    base = _safe(args.dir)
    if not base.is_dir():
        raise RuntimeError(f"directory not found: {base}")
    replace_pairs: dict[str, str] = {}
    for item in args.replace:
        if "=" not in item:
            raise RuntimeError(f"expected OLD=NEW, got {item!r}")
        old, _, new = item.partition("=")
        replace_pairs[old] = new
    plans = plan(base, args.prefix, args.suffix, replace_pairs, args.ext, args.pad)
    if not args.dry_run:
        for entry in plans:
            (base / entry["from"]).rename(base / entry["to"])
    print(json.dumps({"status": "ok", "dir": str(base), "dry_run": args.dry_run, "renamed": plans, "count": len(plans)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
