#!/usr/bin/env python3
"""Extract/transcode/normalize an audio track with ffmpeg (offline).

Thin CLI wrapper over ``learngraph_tasks.audio_transcode``, with optional
segment bounds. Primary use: normalize a recording to 16 kHz mono WAV before
handing it to the host ASR bridge (sandbox_transcribe_audio).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from learngraph_tasks import audio_transcode


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def _segment(source: str, target: str, ss: str | None, t: str | None) -> None:
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    if ss:
        argv += ["-ss", ss]
    argv += ["-i", source]
    if t:
        argv += ["-t", t]
    argv += ["-c", "copy", target]
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=150, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()[:500]
        raise RuntimeError(f"ffmpeg segment failed: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract/transcode/normalize audio (offline ffmpeg).")
    parser.add_argument("--input", required=True, help="workspace-relative media path")
    parser.add_argument("--output", required=True, help="workspace-relative output path")
    parser.add_argument("--format", default="", help="output extension hint (e.g. mp3/wav)")
    parser.add_argument("--sample-rate", "-sr", type=int, default=0, help="resample to this Hz (16000 for ASR)")
    parser.add_argument("--channels", "-ac", type=int, default=0, help="mono=1 / stereo=2")
    parser.add_argument("--bitrate", default="", help="audio bitrate e.g. 128k")
    parser.add_argument("--ss", default="", help="start offset seconds")
    parser.add_argument("--t", default="", help="segment duration seconds")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = _safe(args.input)
    dst = _safe(args.output)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    if args.ss or args.t:
        _segment(str(src), str(dst), args.ss or None, args.t or None)
    else:
        audio_transcode(
            src,
            dst,
            sample_rate=args.sample_rate or None,
            channels=args.channels or None,
            bitrate=args.bitrate or None,
        )
    data = dst.read_bytes()
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
                "output": str(dst),
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
