from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from pathlib import Path, PurePosixPath


MAX_TEXT_CHARS = 2_000_000


def safe_relative(value: str) -> Path:
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError("path must remain inside the sandbox workspace")
    return Path("/workspace", *candidate.parts)


def inspect_file(path: Path) -> dict:
    payload = path.read_bytes()
    return {
        "schema_version": "1.0",
        "task_type": "file_inspect",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "detected_mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    }


def extract_inert_text(path: Path) -> dict:
    payload = path.read_bytes()
    if b"\x00" in payload[:8_192]:
        raise ValueError("binary content is not accepted by the inert text runner")
    text = payload.decode("utf-8-sig", errors="strict")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("text output exceeds the runner limit")
    return {
        "schema_version": "1.0",
        "task_type": "extract_inert_text",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "text": text,
        "locator": "document",
    }


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--task", choices=("file_inspect", "extract_inert_text"), required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = safe_relative(args.input)
    target = safe_relative(args.output)
    if not source.is_file():
        raise ValueError("authorized input file does not exist")
    result = inspect_file(source) if args.task == "file_inspect" else extract_inert_text(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
