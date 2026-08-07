#!/usr/bin/env python3
"""Safely extract a zip archive (offline, zip-slip / symlink protected).

Rejects absolute paths, '..' traversal, drive letters, and symlink members;
any unsafe entry fails the whole extraction.
"""
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


def _validate_member(name: str) -> str:
    candidate = PurePosixPath(name)
    if candidate.is_absolute() or ".." in candidate.parts or ":" in name:
        raise RuntimeError(f"unsafe zip member path: {name!r}")
    return name


def extract(zip_path: Path, target: Path, overwrite: bool) -> int:
    if target.exists() and any(target.iterdir()) and not overwrite:
        raise RuntimeError("target directory is not empty; pass --overwrite to replace")
    count = 0
    with zipfile.ZipFile(zip_path) as bundle:
        for info in bundle.infolist():
            name = _validate_member(info.filename)
            if info.is_dir():
                continue
            mode = info.external_attr >> 16
            if mode & 0o170000 == 0o120000:  # S_IFLNK
                raise RuntimeError(f"unsupported link entry in archive: {name!r}")
            destination = (target / name).resolve()
            if destination != target and target.resolve() not in destination.parents:
                raise RuntimeError(f"zip entry escapes the target directory: {name!r}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, open(destination, "wb") as out:
                out.write(source.read())
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely extract a zip (offline).")
    parser.add_argument("--zip", required=True, help="workspace-relative .zip path")
    parser.add_argument("--output", required=True, help="workspace-relative output directory")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    zip_path = _safe(args.zip)
    target = _safe(args.output)
    if not zip_path.is_file():
        raise RuntimeError(f"zip file not found: {zip_path}")
    count = extract(zip_path, target, args.overwrite)
    entries = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    print(
        json.dumps(
            {
                "status": "ok",
                "zip": str(zip_path),
                "output": str(target),
                "files": count,
                "entries": entries,
                "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
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
