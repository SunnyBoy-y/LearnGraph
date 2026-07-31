from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import subprocess
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


def extract_legacy_doc(path: Path) -> dict:
    payload = path.read_bytes()
    completed = subprocess.run(
        ["/usr/bin/antiword", "-m", "UTF-8.txt", str(path)],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:2_000]
        raise ValueError(f"antiword failed (exit {completed.returncode}): {detail}")
    text = completed.stdout.decode("utf-8", errors="strict").strip()
    if not text:
        raise ValueError("legacy Word document contains no extractable text")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("text output exceeds the runner limit")
    return {
        "schema_version": "1.0",
        "task_type": "extract_legacy_doc",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "text": text,
        "locator": "document",
        "parser_name": "antiword",
        "parser_version": "0.37",
    }


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument(
        "--task",
        choices=("file_inspect", "extract_inert_text", "extract_legacy_doc"),
        required=True,
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = safe_relative(args.input)
    target = safe_relative(args.output)
    if not source.is_file():
        raise ValueError("authorized input file does not exist")
    if args.task == "file_inspect":
        result = inspect_file(source)
    elif args.task == "extract_inert_text":
        result = extract_inert_text(source)
    else:
        result = extract_legacy_doc(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
