#!/usr/bin/env python3
"""Report audio/video metadata via ffprobe (offline).

Thin CLI wrapper over ``learngraph_tasks.media_info``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from learngraph_tasks import media_info


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Report audio/video metadata (offline ffprobe).")
    parser.add_argument("--input", required=True, help="workspace-relative media path")
    args = parser.parse_args()
    src = _safe(args.input)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    info = media_info(src)
    format_info = info.get("format") or {}
    streams = info.get("streams") or []
    duration = format_info.get("duration")
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
                "format_name": format_info.get("format_name"),
                "duration_seconds": round(float(duration), 3) if duration else None,
                "size_bytes": format_info.get("size"),
                "stream_count": len(streams),
                "streams": streams,
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
