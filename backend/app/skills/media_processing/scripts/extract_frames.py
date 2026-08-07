#!/usr/bin/env python3
"""Extract video frames as PNGs with ffmpeg (offline).

Use --every N to grab a frame every N seconds, or --ss/--count for a window.
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
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def extract_frames(src: Path, out_dir: Path, every: float, ss: str | None, t: str | None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "frame-%04d.png")
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    if ss:
        argv += ["-ss", ss]
    argv += ["-i", str(src)]
    if t:
        argv += ["-t", t]
    argv += ["-vf", f"fps=1/{every}", pattern]
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=150, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()[:500]
        raise RuntimeError(f"ffmpeg frame extraction failed: {detail}")
    frames = sorted(out_dir.glob("frame-*.png"))
    if not frames:
        raise RuntimeError("no frames extracted")
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract video frames as PNG (offline ffmpeg).")
    parser.add_argument("--input", required=True, help="workspace-relative media path")
    parser.add_argument("--output", required=True, help="workspace-relative output directory")
    parser.add_argument("--every", type=float, default=2.0, help="one frame every N seconds")
    parser.add_argument("--ss", default="", help="start offset seconds")
    parser.add_argument("--t", default="", help="window duration seconds")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = _safe(args.input)
    out_dir = _safe(args.output)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    if out_dir.exists() and any(out_dir.glob("frame-*.png")) and not args.overwrite:
        raise RuntimeError("output directory already has frames; pass --overwrite to replace")
    frames = extract_frames(src, out_dir, args.every, args.ss or None, args.t or None)
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in frames]
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
                "output": str(out_dir),
                "frames": [str(p) for p in frames],
                "frame_count": len(frames),
                "sha256": hashes[0],
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
