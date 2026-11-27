from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Any, Callable, Iterator

LIVE_OUTPUT_MAX_CHARS = 8_000
LIVE_OUTPUT_MAX_LINES = 200

_state = threading.local()


def _sinks() -> list[Callable[[dict[str, Any]], None]]:
    stack = getattr(_state, "sinks", None)
    if stack is None:
        stack = []
        _state.sinks = stack
    return stack


@contextmanager
def live_output_sink(
    callback: Callable[[dict[str, Any]], None],
) -> Iterator[None]:
    """Register a thread-local sink for live sandbox command output snapshots."""
    stack = _sinks()
    stack.append(callback)
    try:
        yield
    finally:
        if stack and stack[-1] is callback:
            stack.pop()
        elif callback in stack:
            stack.remove(callback)


def emit_live_output(snapshot: dict[str, Any]) -> None:
    """Deliver a bounded live output snapshot to the active thread-local sink."""
    stack = getattr(_state, "sinks", None)
    if not stack:
        return
    try:
        stack[-1](snapshot)
    except Exception:
        # Live output previews are best-effort and must never fail tool execution.
        return


def combine_live_shell_output(
    stdout_tail: str,
    stderr_tail: str,
    *,
    truncated: bool = False,
    max_chars: int = LIVE_OUTPUT_MAX_CHARS,
    max_lines: int = LIVE_OUTPUT_MAX_LINES,
) -> tuple[str, bool]:
    """Merge stdout/stderr tails into a single bounded display string."""
    parts: list[str] = []
    if stdout_tail:
        parts.append(stdout_tail)
    if stderr_tail:
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")
        parts.append(stderr_tail)
    combined = "".join(parts)
    if not combined:
        return "", bool(truncated)

    is_truncated = bool(truncated)
    lines = combined.splitlines(keepends=True)
    if len(lines) > max_lines:
        is_truncated = True
        combined = "".join(lines[-max_lines:])
    if len(combined) > max_chars:
        is_truncated = True
        combined = combined[-max_chars:]
    return combined, is_truncated
