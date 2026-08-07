#!/usr/bin/env python3
"""Generate a Markdown or self-contained HTML report from structured content JSON (offline)."""
from __future__ import annotations

import argparse
import hashlib
import html as _html
import json
import sys
from pathlib import Path


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


def _md_section(section: dict) -> str:
    parts: list[str] = []
    heading = str(section.get("heading") or "").strip()
    if heading:
        parts.append(f"## {heading}")
    for para in section.get("paragraphs") or []:
        parts.append(str(para))
    for bullet in section.get("bullets") or []:
        parts.append(f"- {bullet}")
    table = section.get("table")
    if isinstance(table, dict):
        columns = [str(c) for c in (table.get("columns") or [])]
        rows = table.get("rows") or []
        if columns:
            table_lines = ["| " + " | ".join(columns) + " |"]
            table_lines.append("|" + "|".join("---" for _ in columns) + "|")
            for row in rows:
                table_lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
            parts.append("\n".join(table_lines))
    return "\n\n".join(parts)


def to_markdown(payload: dict) -> str:
    title = str(payload.get("title") or "报告")
    sections = payload.get("sections") or []
    body = "\n\n".join(_md_section(section) for section in sections if isinstance(section, dict))
    return f"# {title}\n\n{body}".strip()


def to_html(payload: dict) -> str:
    import markdown_it

    md = to_markdown(payload)
    renderer = markdown_it.MarkdownIt()
    body = renderer.render(md)
    title = _html.escape(str(payload.get("title") or "报告"))
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>"
        "body{font-family:'Noto Sans CJK SC',sans-serif;margin:2.5em;line-height:1.7;max-width:820px;}"
        "table{border-collapse:collapse;}td,th{border:1px solid #ccc;padding:4px 8px;}"
        "code{background:#f3f4f6;padding:.1em .3em;border-radius:4px;}</style></head>"
        f"<body>{body}</body></html>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Markdown/HTML report (offline).")
    parser.add_argument("--input", required=True, help="workspace-relative content.json path")
    parser.add_argument("--output", required=True, help="workspace-relative .md or .html path")
    parser.add_argument("--title", default="", help="override report title")
    parser.add_argument("--format", choices=["md", "html"], default="")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    src = _safe(args.input)
    dst = _safe(args.output)
    if not src.is_file():
        raise RuntimeError(f"input file not found: {src}")
    if dst.exists() and not args.overwrite:
        raise RuntimeError("output already exists; pass --overwrite to replace")
    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid content JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("content must be a JSON object")
    if args.title:
        payload["title"] = args.title
    fmt = args.format or (dst.suffix.lstrip(".") if dst.suffix.lstrip(".") in ("md", "html") else "md")
    text = to_html(payload) if fmt == "html" else to_markdown(payload)
    if not text.strip():
        raise RuntimeError("empty report")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "input": str(src),
                "output": str(dst),
                "format": fmt,
                "chars": len(text),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
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
