"""Unified-diff parser and fuzzy applier for the sandbox workspace.

Pure standard-library module: parses ``git diff``-style unified diffs and
applies them to in-memory text content.  The caller (sandbox toolkit) owns
durable-store persistence, authorization for deletions and path validation.

Supported syntax:
- per-file sections introduced by ``diff --git a/x b/y`` OR ``--- ``/``+++ ``
  header pairs;
- ``--- a/path`` / ``+++ b/path`` (with or without timestamps) and the
  ``/dev/null`` sentinel for create/delete;
- hunks ``@@ -a,b +c,d @@`` with `` `` (context), ``-`` and ``+`` lines;
- ``index ...`` / ``new file mode`` / ``deleted file mode`` metadata lines
  (ignored);
- ``\\ No newline at end of file`` markers (ignored; output is normalized to
  LF and a single trailing newline).

Paths are returned as POSIX-relative strings (leading ``a/``/``b/`` stripped);
the caller must still run them through ``validate_agent_workspace_path``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

FUZZ_DEFAULT = 3
_SCAN_WINDOW = 64


class DiffParseError(ValueError):
    pass


class DiffApplyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DiffHunk:
    old_start: int  # 1-based
    old_count: int
    new_start: int  # 1-based
    new_count: int
    lines: tuple[str, ...]  # op char + payload: " ", "-", "+"


@dataclass(slots=True)
class DiffFile:
    old_path: str
    new_path: str
    hunks: list[DiffHunk] = field(default_factory=list)
    is_create: bool = False
    is_delete: bool = False


@dataclass(frozen=True, slots=True)
class DiffResult:
    """Outcome of applying one file section."""

    old_path: str
    new_path: str
    action: str  # create | modify | delete
    new_content: str | None = None  # None for delete


def _strip_ab_prefix(path: str) -> str:
    stripped = path.strip()
    for prefix in ("a/", "b/"):
        if stripped.startswith(prefix) and stripped != prefix:
            stripped = stripped[len(prefix):]
            break
    return stripped


def _split_header_path(header: str) -> str:
    """Return the path part of a ``---``/``+++`` header, dropping timestamps
    and any leading ``a/``/``b/`` prefix."""
    if "\t" in header:
        header = header.split("\t", 1)[0]
    return _strip_ab_prefix(header.strip())


def parse_unified_diff(text: str) -> list[DiffFile]:
    """Parse a unified diff into file sections.  Raises DiffParseError on a
    malformed hunk body; benign metadata lines are skipped."""
    if not text:
        raise DiffParseError("empty patch")
    raw_lines = text.splitlines()
    files: list[DiffFile] = []
    current: DiffFile | None = None
    seen_headers = False
    index = 0
    n = len(raw_lines)

    def finish() -> None:
        nonlocal current
        if current is not None:
            if current.old_path == "/dev/null":
                current.is_create = True
            if current.new_path == "/dev/null":
                current.is_delete = True
            if not current.hunks and not (current.is_create or current.is_delete):
                raise DiffParseError(f"file section has no hunks: {current.old_path}")
            files.append(current)
            current = None

    while index < n:
        line = raw_lines[index]
        if line.startswith("diff --git "):
            finish()
            current = DiffFile(old_path="", new_path="")
            seen_headers = True
            index += 1
            continue
        if line.startswith("--- "):
            old = _split_header_path(line[4:])
            new_path = ""
            if index + 1 < n and raw_lines[index + 1].startswith("+++ "):
                new_path = _split_header_path(raw_lines[index + 1][4:])
                index += 1
            if current is not None and current.old_path == "" and current.new_path == "":
                # ``diff --git`` section header followed by the ---/+++ pair:
                # merge into the pending section.
                current.old_path = old
                current.new_path = new_path
            else:
                finish()
                current = DiffFile(old_path=old, new_path=new_path)
            seen_headers = True
            index += 1
            continue
        if current is None:
            # Metadata before the first file section (e.g. ``diff --git``
            # alone, or index lines): skip.
            if line.startswith(("index ", "new file ", "deleted file ", "similarity ", "rename ", "old mode ", "new mode ", "copy ")):
                index += 1
                continue
            if line.startswith("+++ "):
                raise DiffParseError("patch starts with an orphan +++ header")
            index += 1
            continue
        if line.startswith("@@ "):
            parts = line.split(" ", 3)
            if len(parts) < 3:
                raise DiffParseError(f"malformed hunk header: {line!r}")
            old_range = parts[1]
            new_range = parts[2]
            old_start, old_count = _parse_range(old_range, "old")
            new_start, new_count = _parse_range(new_range, "new")
            body: list[str] = []
            index += 1
            while index < n and not raw_lines[index].startswith(("@@ ", "diff --git ", "--- ")):
                body.append(raw_lines[index])
                index += 1
            hunks_lines: list[str] = []
            for body_line in body:
                if body_line == "\\ No newline at end of file":
                    continue
                if not body_line or body_line[0] not in (" ", "-", "+"):
                    raise DiffParseError(f"invalid hunk body line: {body_line!r}")
                hunks_lines.append(body_line)
            actual_old = sum(1 for entry in hunks_lines if entry[0] in (" ", "-"))
            actual_new = sum(1 for entry in hunks_lines if entry[0] in (" ", "+"))
            if old_count >= 0 and actual_old != old_count:
                raise DiffParseError(
                    f"hunk old count mismatch: header says {old_count}, body has {actual_old}"
                )
            if new_count >= 0 and actual_new != new_count:
                raise DiffParseError(
                    f"hunk new count mismatch: header says {new_count}, body has {actual_new}"
                )
            current.hunks.append(
                DiffHunk(
                    old_start=old_start,
                    old_count=actual_old,
                    new_start=new_start,
                    new_count=actual_new,
                    lines=tuple(hunks_lines),
                )
            )
            continue
        # Metadata / noise inside a section.
        if line.startswith(("index ", "new file ", "deleted file ", "old mode ", "new mode ", "similarity ", "rename ")):
            index += 1
            continue
        index += 1

    finish()
    if not files and not seen_headers:
        raise DiffParseError("no unified diff file sections found")
    return files


def _parse_range(spec: str, label: str) -> tuple[int, int]:
    # git hunk ranges are written "-1,3" / "+1,3"; the sign is a diff marker,
    # never a negative number.
    cleaned = spec.lstrip("+-")
    if "," in cleaned:
        start_text, count_text = cleaned.split(",", 1)
    else:
        start_text, count_text = cleaned, "1"
    try:
        start = int(start_text)
        count = int(count_text)
    except ValueError as exc:
        raise DiffParseError(f"invalid {label} range {spec!r}") from exc
    if count < 0 or start < 0 or (start == 0 and count > 0):
        raise DiffParseError(f"invalid {label} range {spec!r}")
    return start, count


def _split_content(content: str) -> tuple[list[str], bool]:
    """Split into line list (no terminators) and trailing-newline flag."""
    if content == "":
        return [], False
    lines = content.split("\n")
    trailing = lines[-1] == ""
    if trailing:
        lines = lines[:-1]
    return lines, trailing


def _join_content(lines: Iterable[str], trailing: bool) -> str:
    joined = "\n".join(lines)
    return joined + "\n" if trailing else joined


def _hunk_old_lines(hunk: DiffHunk) -> list[str]:
    return [line[1:] for line in hunk.lines if line[0] in (" ", "-")]


def _hunk_new_lines(hunk: DiffHunk) -> list[str]:
    return [line[1:] for line in hunk.lines if line[0] in (" ", "+")]


def _find_hunk_position(lines: list[str], hunk: DiffHunk, fuzz: int) -> int:
    """Locate the hunk's old-side lines within ``lines``.

    Searches outward from the hunk's declared old_start (1-based), tolerating
    up to ``fuzz`` mismatched lines per candidate window (git-apply style).
    Returns the 0-based insertion index of the first old line, or -1.
    """
    old_side = _hunk_old_lines(hunk)
    if not old_side:
        # Pure insertion hunk: position from the declared start (clamped).
        return max(0, min(hunk.old_start - 1, len(lines)))
    target_len = len(old_side)
    expected = max(0, hunk.old_start - 1)
    low = max(0, expected - _SCAN_WINDOW)
    high = min(len(lines) - target_len + 1, expected + _SCAN_WINDOW + 1)
    best: tuple[int, int, int] | None = None  # (mismatches, distance, start)
    for start in range(low, high):
        window = lines[start:start + target_len]
        mismatches = sum(
            1 for actual, wanted in zip(window, old_side) if actual != wanted
        )
        if mismatches > fuzz:
            continue
        distance = abs(start - expected)
        candidate = (mismatches, distance, start)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
            if mismatches == 0 and distance == 0:
                return start
    if best is None:
        return -1
    return best[2]


def apply_hunks(content: str, hunks: list[DiffHunk], *, fuzz: int = FUZZ_DEFAULT) -> str:
    """Apply hunks to a file's content.  Raises DiffApplyError when a hunk
    cannot be placed within the fuzz window."""
    lines, trailing = _split_content(content)
    for hunk in hunks:
        position = _find_hunk_position(lines, hunk, fuzz)
        if position < 0:
            raise DiffApplyError(
                f"hunk @ -{hunk.old_start},{hunk.old_count} "
                f"+{hunk.new_start},{hunk.new_count} does not match the file "
                f"within fuzz={fuzz}"
            )
        old_len = sum(1 for line in hunk.lines if line[0] in (" ", "-"))
        new_block = _hunk_new_lines(hunk)
        lines[position:position + old_len] = new_block
    return _join_content(lines, trailing)


def render_file_section(file: DiffFile) -> str:
    """Serialize one parsed section back to a unified diff (debug/echo)."""
    header_old = "/dev/null" if file.is_create else file.old_path
    header_new = "/dev/null" if file.is_delete else file.new_path
    lines = [f"--- {header_old}", f"+++ {header_new}"]
    for hunk in file.hunks:
        lines.append(
            f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@"
        )
        lines.extend(hunk.lines)
    return "\n".join(lines) + "\n"
