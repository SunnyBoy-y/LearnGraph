#!/usr/bin/env python3
"""Build a compact, structured media report JSON (offline)."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from learngraph_tasks import media_info


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def media_report(src: Path) -> dict:
    raw = media_info(src)
    fmt = raw.get("format") or {}
    streams = raw.get("streams") or []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [s for s in streams if s.get("codec_type") == "video"]
    duration = fmt.get("duration")
    return {
        "input": str(src),
        "format_name": fmt.get("format_name"),
        "duration_seconds": round(float(duration), 3) if duration else None,
        "size_bytes": fmt.get("size"),
        "bit_rate": fmt.get("bit_rate"),
        "audio_streams": [
            {
                "codec": s.get("codec_name"),
                "sample_rate": s.get("sample_rate"),
                "channels": s.get("channels"),
            }
            for s in audio
        ],
        "video_streams": [
            {
                "codec": s.get("codec_name"),
                "width": s.get("width"),
                "height": s.get("height"),
                "fps": s.get("avg_frame_rate"),
            }
            for s in video
        ],
        "stream_count": len(streams),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a compact media report JSON (offline).")
    parser.add_argument("--input", required=True, help="workspace-relative media path")
    parser.add_argument("--output", default="", help="optional workspace-relative .json path")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = _safe(args.input)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    report = media_report(src)
    report.update({"status": "ok", "sha256": hashlib.sha256(src.read_bytes()).hexdigest()})
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
