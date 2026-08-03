from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import subprocess
from pathlib import Path, PurePosixPath


MAX_TEXT_CHARS = 2_000_000
MAX_PREVIEW_CHARS = 100_000
MAX_MCP_REQUEST_BYTES = 64 * 1024
MAX_MCP_RESPONSE_BYTES = 256 * 1024
MCP_SERVER_EXECUTABLES = frozenset({"python", "python3", "node", "nodejs"})
_EXTERNAL_SCHEME = re.compile(r"(?:^|[\s\"'])https?://", re.IGNORECASE)
_SCRIPT_SRC = re.compile(r"<script[^>]*\bsrc\s*=", re.IGNORECASE)


def safe_relative(value: str) -> Path:
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError("path must remain inside the sandbox workspace")
    return Path("/workspace", *candidate.parts)


def render_component(path: Path) -> dict:
    payload = path.read_bytes()
    if len(payload) > MAX_PREVIEW_CHARS:
        raise ValueError("preview document exceeds the renderer size limit")
    document = payload.decode("utf-8-sig", errors="strict")
    # The server-owned preview must stay fully inert: no external fetch, no
    # remote scripts.  Any violation fails the render (the caller degrades to
    # the unavailable baseline rather than weakening it).
    if _EXTERNAL_SCHEME.search(document):
        raise ValueError("preview document contains an external http(s) reference")
    if _SCRIPT_SRC.search(document) or "<script>" in document.casefold():
        raise ValueError("preview document contains a script element")
    if "Content-Security-Policy" not in document:
        raise ValueError("preview document is missing the server-owned CSP")
    return {
        "schema_version": "1.0",
        "task_type": "render_component",
        "status": "ok",
        "valid": True,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def mcp_stdio(request_path: Path, launch_path: Path, target: Path) -> dict:
    """One-shot JSON-RPC over stdio inside the isolated container.

    The FastAPI host never spawns the third-party server command; only this
    fixed, reviewed task in the offline container may launch it, subject to the
    executable allowlist, argument bound, request/response size limits and a
    hard timeout carried in the immutable launch spec.
    """

    launch_payload = json.loads(launch_path.read_bytes().decode("utf-8"))
    command = launch_payload.get("command")
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise ValueError("MCP launch command must be a non-empty string list")
    max_args = int(launch_payload.get("max_args") or 16)
    if len(command) > max_args:
        raise ValueError("MCP launch command exceeds the argument bound")
    executable = PurePosixPath(command[0].replace("\\", "/")).name.casefold()
    if executable not in MCP_SERVER_EXECUTABLES:
        raise ValueError("MCP server executable is not in the runner allowlist")

    request_bytes = request_path.read_bytes()
    if not request_bytes:
        raise ValueError("MCP request is empty")
    if len(request_bytes) > MAX_MCP_REQUEST_BYTES:
        raise ValueError("MCP request exceeds the size limit")
    timeout_seconds = float(launch_payload.get("timeout_seconds") or 60)
    completed = subprocess.run(
        command,
        input=request_bytes,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise ValueError(f"MCP server exited {completed.returncode}: {detail}")
    if len(completed.stdout) > MAX_MCP_RESPONSE_BYTES:
        raise ValueError("MCP response exceeds the size limit")
    try:
        parsed = json.loads(completed.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("MCP server returned non-JSON output") from exc
    if not isinstance(parsed, dict):
        raise ValueError("MCP server returned a non-object JSON-RPC response")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
    return {
        "schema_version": "1.0",
        "task_type": "mcp_stdio",
        "status": "ok",
        "bytes": len(completed.stdout),
    }


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
        choices=(
            "file_inspect",
            "extract_inert_text",
            "extract_legacy_doc",
            "render_component",
            "mcp_stdio",
        ),
        required=True,
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--spec")
    args = parser.parse_args()
    source = safe_relative(args.input)
    target = safe_relative(args.output)
    if not source.is_file():
        raise ValueError("authorized input file does not exist")
    if args.task == "file_inspect":
        result = inspect_file(source)
    elif args.task == "extract_inert_text":
        result = extract_inert_text(source)
    elif args.task == "extract_legacy_doc":
        result = extract_legacy_doc(source)
    elif args.task == "render_component":
        result = render_component(source)
    elif args.task == "mcp_stdio":
        if not args.spec:
            raise ValueError("mcp_stdio requires a launch spec file")
        result = mcp_stdio(source, safe_relative(args.spec), target)
        # ``mcp_stdio`` already wrote the JSON-RPC response to ``target``; the
        # fixed-task summary goes to stdout only, so the caller reads exactly
        # the server response instead of a wrapper artifact.
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    else:
        raise ValueError(f"unknown task: {args.task}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
