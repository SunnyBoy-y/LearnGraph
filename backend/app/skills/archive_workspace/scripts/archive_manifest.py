#!/usr/bin/env python3
"""List an archive's or directory's member manifest (path, size, sha256, unsafe flags)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def _is_unsafe(name: str) -> str | None:
    candidate = PurePosixPath(name)
    if candidate.is_absolute() or ".." in candidate.parts or ":" in name:
        return "unsafe_path"
    return None


def _from_zip(zip_path: Path, limit: int) -> tuple[list[dict], int]:
    entries: list[dict] = []
    total = 0
    with zipfile.ZipFile(zip_path) as bundle:
        for info in bundle.infolist():
            if info.is_dir():
                continue
            mode = info.external_attr >> 16
            flags = []
            if mode & 0o170000 == 0o120000:
                flags.append("symlink")
            unsafe = _is_unsafe(info.filename)
            if unsafe:
                flags.append(unsafe)
            total += info.file_size
            if limit and len(entries) >= limit:
                continue
            entries.append(
                {
                    "path": info.filename,
                    "size": info.file_size,
                    "flags": flags,
                    "compressed": info.compress_size,
                }
            )
    return entries, total


def _from_dir(base: Path, limit: int) -> tuple[list[dict], int]:
    entries: list[dict] = []
    total = 0
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        total += path.stat().st_size
        if limit and len(entries) >= limit:
            continue
        rel = path.relative_to(base).as_posix()
        entries.append(
            {
                "path": rel,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "flags": [],
            }
        )
    return entries, total


def main() -> int:
    parser = argparse.ArgumentParser(description="List member manifest of a zip or directory (offline).")
    parser.add_argument("--zip", default="", help="workspace-relative .zip path")
    parser.add_argument("--dir", default="", help="workspace-relative directory")
    parser.add_argument("--limit", type=int, default=0, help="cap listed entries")
    parser.add_argument("--output", default="", help="optional workspace-relative .json path")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if bool(args.zip) == bool(args.dir):
        raise RuntimeError("exactly one of --zip or --dir is required")
    if args.zip:
        source = _safe(args.zip)
        if not source.is_file():
            raise RuntimeError(f"zip file not found: {source}")
        entries, total = _from_zip(source, args.limit)
    else:
        source = _safe(args.dir)
        if not source.is_dir():
            raise RuntimeError(f"directory not found: {source}")
        entries, total = _from_dir(source, args.limit)
    unsafe = [e for e in entries if e.get("flags")]
    report = {
        "status": "ok",
        "source": str(source),
        "kind": "zip" if args.zip else "dir",
        "entries_listed": len(entries),
        "entries_total": None if args.limit else len(entries),
        "total_bytes": total,
        "unsafe_entries": unsafe,
        "entries": entries,
    }
    if args.output:
        dst = _safe(args.output)
        if dst.exists() and not args.overwrite:
            raise RuntimeError("output already exists; pass --overwrite to replace")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
