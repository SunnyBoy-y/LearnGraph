#!/usr/bin/env python3
"""Build a frontend project offline (Vite/React/Vue/HTML) using the image's
pre-installed /node_modules. Never runs npm install.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def _run(argv: list[str], cwd: Path) -> None:
    completed = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, timeout=170, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:1500]
        raise RuntimeError(f"{' '.join(argv)} failed (exit {completed.returncode}): {detail}")


def build(root: Path, overwrite: bool) -> Path:
    if not root.is_dir():
        raise RuntimeError(f"project directory not found: {root}")
    index = root / "index.html"
    if index.is_file() and not (root / "package.json").is_file():
        # Plain single-file HTML: no build step; treat the file itself as dist.
        dist = root / "dist"
        if dist.exists() and not overwrite:
            raise RuntimeError("dist exists; pass --overwrite to replace")
        dist.mkdir(parents=True, exist_ok=True)
        shutil.copy2(index, dist / "index.html")
        return dist
    if not (root / "package.json").is_file():
        raise RuntimeError("no package.json; scaffold a project first (scaffold_vite.py)")
    if not (root / "node_modules").exists():
        raise RuntimeError("node_modules missing; cannot build offline (no npm install allowed)")
    _run(["npm", "run", "build"], cwd=root)
    dist = root / "dist"
    if not (dist / "index.html").is_file():
        raise RuntimeError("build produced no dist/index.html")
    return dist


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a frontend project offline.")
    parser.add_argument("--dir", required=True, help="workspace-relative project directory")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = _safe(args.dir)
    dist = build(root, args.overwrite)
    files = sorted(str(p.relative_to(dist)) for p in dist.rglob("*") if p.is_file())
    hashes = {}
    for rel in files:
        hashes[rel] = hashlib.sha256((dist / rel).read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "status": "ok",
                "dir": str(root),
                "dist": str(dist),
                "files": files,
                "file_count": len(files),
                "sha256": hashes,
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
