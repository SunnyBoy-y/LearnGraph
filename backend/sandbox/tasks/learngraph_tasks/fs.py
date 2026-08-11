"""Sandbox file-workflow helpers (offline; stdlib + charset-normalizer).

Import from agent workspace scripts::

    from learngraph_tasks.fs import file_stats, grep_lines, read_lines
    print(json.dumps(file_stats("inputs/notes.txt"), ensure_ascii=False))

Everything operates on workspace-relative paths (cwd is /workspace inside the
container). Functions return plain Python structures; keep printed output
structured (JSON + counts) because exec stdout is truncated by
``sandbox_output_bytes``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import mimetypes
import re
from pathlib import Path, PurePosixPath

__all__ = [
    "file_stats",
    "find_files",
    "grep_lines",
    "head_lines",
    "tail_lines",
    "read_lines",
    "replace_all",
    "insert_lines",
    "delete_lines",
    "to_utf8",
    "split_lines",
    "tree",
]

_DEFAULT_ENCODINGS = ("utf-8", "gb18030", "big5", "shift_jis", "cp1252")


def _safe_rel(value: str | Path) -> Path:
    candidate = PurePosixPath(str(value).replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError("path must stay inside the sandbox workspace")
    return Path("/workspace", *candidate.parts)


def _detect_encoding(data: bytes) -> str | None:
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is not None:
            return best.encoding
    except Exception:
        pass
    for encoding in _DEFAULT_ENCODINGS:
        try:
            data.decode(encoding)
            return encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def file_stats(path: str | Path) -> dict:
    """Size, line count, detected encoding, sha256 and mime for one file."""

    target = _safe_rel(path)
    if not target.is_file():
        raise FileNotFoundError(str(path))
    data = target.read_bytes()
    encoding = _detect_encoding(data)
    text = None
    if encoding is not None:
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            text = None
    return {
        "path": str(PurePosixPath(path)),
        "size_bytes": len(data),
        "lines": text.count("\n") + 1 if text is not None else None,
        "encoding": encoding,
        "utf8": encoding == "utf-8",
        "sha256": hashlib.sha256(data).hexdigest(),
        "mime": mimetypes.guess_type(target.name)[0] or "application/octet-stream",
    }


def find_files(
    *,
    name_glob: str | None = None,
    dirs: str | list[str] | None = None,
    min_bytes: int | None = None,
    max_bytes: int | None = None,
    sort: str = "path",
) -> list[dict]:
    """Walk the workspace (or given dirs) and return matching file metadata."""

    roots = [Path("/workspace")]
    if dirs:
        roots = [_safe_rel(item) for item in (dirs if isinstance(dirs, list) else [dirs])]
    results: list[dict] = []
    for root in roots:
        if not root.is_dir():
            raise FileNotFoundError(str(root))
        for item in sorted(root.rglob("*")):
            if not item.is_file():
                continue
            relative = str(item.relative_to("/workspace"))
            if name_glob and not fnmatch.fnmatch(relative, name_glob):
                continue
            size = item.stat().st_size
            if min_bytes is not None and size < min_bytes:
                continue
            if max_bytes is not None and size > max_bytes:
                continue
            results.append(
                {
                    "path": relative,
                    "size_bytes": size,
                    "mtime": int(item.stat().st_mtime),
                }
            )
    if sort == "size":
        results.sort(key=lambda item: item["size_bytes"])
    elif sort == "mtime":
        results.sort(key=lambda item: item["mtime"])
    else:
        results.sort(key=lambda item: item["path"])
    return results


def grep_lines(
    pattern: str,
    *,
    glob: str | None = None,
    context: int = 0,
    max_matches: int = 50,
    case_sensitive: bool = False,
    max_line_chars: int = 500,
) -> dict:
    """Search workspace files line-by-line; returns matches + per-file counts.

    Unlike the host-side ``sandbox_grep`` this can also search files that only
    exist inside the container. Keep ``max_matches`` bounded: output is printed
    and truncated by the host.
    """

    flags = 0 if case_sensitive else re.IGNORECASE
    matcher = re.compile(pattern, flags)
    matches: list[dict] = []
    file_counts: list[dict] = []
    searched = 0
    skipped_binary = 0
    truncated = False

    for info in find_files(name_glob=glob):
        target = Path("/workspace", *info["path"].split("/"))
        data = target.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            skipped_binary += 1
            continue
        searched += 1
        lines = text.splitlines()
        count = 0
        index = 0
        while index < len(lines):
            if not matcher.search(lines[index]):
                index += 1
                continue
            count += 1
            window_lo = max(0, index - context)
            window_hi = min(len(lines) - 1, index + context)
            ctx_rows = []
            for row in range(window_lo, window_hi + 1):
                row_text = lines[row][:max_line_chars]
                if row != index:
                    ctx_rows.append({"line_number": row + 1, "text": row_text})
            matches.append(
                {
                    "path": info["path"],
                    "line_number": index + 1,
                    "text": lines[index][:max_line_chars],
                    "context": ctx_rows,
                }
            )
            if len(matches) >= max_matches:
                truncated = True
                break
            index = window_hi + 1
        if count:
            file_counts.append({"path": info["path"], "matches": count})
        if truncated:
            break

    return {
        "matches": matches,
        "file_counts": file_counts,
        "searched_files": searched,
        "skipped_binary": skipped_binary,
        "truncated": truncated,
    }


def head_lines(path: str | Path, n: int = 50) -> dict:
    return _line_window(path, start=1, end=n)


def tail_lines(path: str | Path, n: int = 50) -> dict:
    target = _safe_rel(path)
    lines = _read_lines(target)
    return _line_window(path, start=max(1, len(lines) - n + 1), end=len(lines))


def read_lines(path: str | Path, start: int = 1, end: int | None = None) -> dict:
    return _line_window(path, start=start, end=end)


def _read_lines(target: Path) -> list[str]:
    if not target.is_file():
        raise FileNotFoundError(str(target))
    data = target.read_bytes()
    encoding = _detect_encoding(data)
    if encoding is None:
        raise ValueError("file encoding could not be detected (binary?)")
    return data.decode(encoding).splitlines()


def _line_window(path: str | Path, *, start: int, end: int | None) -> dict:
    target = _safe_rel(path)
    lines = _read_lines(target)
    if start < 1:
        raise ValueError("start must be >= 1")
    hi = len(lines) if end is None else min(end, len(lines))
    if start > hi:
        raise ValueError(f"line range {start}..{end} is outside the file ({len(lines)} lines)")
    return {
        "path": str(PurePosixPath(path)),
        "total_lines": len(lines),
        "start_line": start,
        "end_line": hi,
        "lines": [{"line_number": i + 1, "text": lines[i]} for i in range(start - 1, hi)],
    }


def replace_all(path: str | Path, old: str, new: str, max_replacements: int = 100) -> dict:
    """Replace every occurrence of ``old`` (safety cap). Returns the count."""

    target = _safe_rel(path)
    if not old:
        raise ValueError("old string must be non-empty")
    text = _read_text(target)
    count = text.count(old)
    if count == 0:
        return {"path": str(PurePosixPath(path)), "replaced": 0}
    if count > max_replacements:
        raise ValueError(f"refusing to replace {count} occurrences (cap {max_replacements})")
    text = text.replace(old, new)
    target.write_text(text, encoding="utf-8")
    return {"path": str(PurePosixPath(path)), "replaced": count}


def insert_lines(path: str | Path, at_line: int, lines: list[str]) -> dict:
    """Insert ``lines`` before 1-based ``at_line`` (append when > line count)."""

    target = _safe_rel(path)
    if at_line < 1:
        raise ValueError("at_line must be >= 1")
    current = _read_lines(target)
    position = min(at_line - 1, len(current))
    current[position:position] = [str(item) for item in lines]
    target.write_text("\n".join(current) + ("\n" if current else ""), encoding="utf-8")
    return {
        "path": str(PurePosixPath(path)),
        "inserted": len(lines),
        "at_line": position + 1,
        "total_lines": len(current),
    }


def delete_lines(path: str | Path, start: int, end: int) -> dict:
    """Remove 1-based inclusive line range [start, end]. Returns removed count."""

    target = _safe_rel(path)
    if start < 1 or end < start:
        raise ValueError("invalid line range")
    current = _read_lines(target)
    if start > len(current):
        return {"path": str(PurePosixPath(path)), "removed": 0, "total_lines": len(current)}
    hi = min(end, len(current))
    removed = hi - start + 1
    del current[start - 1 : hi]
    target.write_text("\n".join(current) + ("\n" if current else ""), encoding="utf-8")
    return {"path": str(PurePosixPath(path)), "removed": removed, "total_lines": len(current)}


def to_utf8(path: str | Path, target: str | Path | None = None) -> dict:
    """Convert a legacy-encoded text file (e.g. GBK) to UTF-8.

    ``target`` defaults to the same path (in-place rewrite). Returns the
    detected source encoding and the new utf-8 stats.
    """

    source = _safe_rel(path)
    data = source.read_bytes()
    encoding = _detect_encoding(data)
    if encoding is None:
        raise ValueError("file encoding could not be detected (binary?)")
    text = data.decode(encoding)
    out = _safe_rel(target) if target is not None else source
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return {
        "path": str(PurePosixPath(path)),
        "from_encoding": encoding,
        "size_bytes": len(data),
        "target": str(PurePosixPath(out)),
    }


def split_lines(path: str | Path, chunk_lines: int = 500, out_dir: str = "work/chunks") -> dict:
    """Split a large text file into numbered chunks for paged reading."""

    if chunk_lines < 1:
        raise ValueError("chunk_lines must be >= 1")
    target = _safe_rel(path)
    lines = _read_lines(target)
    out = _safe_rel(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    chunks: list[dict] = []
    for index in range(0, len(lines), chunk_lines):
        part = lines[index : index + chunk_lines]
        stem = PurePosixPath(path).name
        chunk_path = out / f"{stem}.{index // chunk_lines + 1:04d}.txt"
        chunk_path.write_text("\n".join(part) + ("\n" if part else ""), encoding="utf-8")
        chunks.append(
            {
                "path": str(chunk_path.relative_to("/workspace")),
                "start_line": index + 1,
                "end_line": index + len(part),
                "lines": len(part),
            }
        )
    return {
        "source": str(PurePosixPath(path)),
        "chunk_lines": chunk_lines,
        "chunks": chunks,
        "total_lines": len(lines),
    }


def tree(max_depth: int = 3) -> dict:
    """Workspace directory tree (files + sizes), bounded by depth."""

    root = Path("/workspace")

    def walk(node: Path, depth: int) -> list[dict]:
        if depth > max_depth:
            return []
        children = []
        for child in sorted(node.iterdir(), key=lambda item: (not item.is_dir(), item.name)):
            relative = str(child.relative_to("/workspace"))
            if child.is_dir():
                children.append(
                    {
                        "path": relative + "/",
                        "type": "dir",
                        "children": walk(child, depth + 1),
                    }
                )
            else:
                children.append(
                    {
                        "path": relative,
                        "type": "file",
                        "size_bytes": child.stat().st_size,
                    }
                )
        return children

    return {"max_depth": max_depth, "tree": walk(root, 1)}


def _read_text(target: Path) -> str:
    data = target.read_bytes()
    encoding = _detect_encoding(data)
    if encoding is None:
        raise ValueError("file encoding could not be detected (binary?)")
    return data.decode(encoding)
