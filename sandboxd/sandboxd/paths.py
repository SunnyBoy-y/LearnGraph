"""Safe relative POSIX path handling for sandboxd file operations."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass


class InvalidPathError(ValueError):
    """Raised when a client-supplied path violates containment rules."""


@dataclass(frozen=True, slots=True)
class SafePath:
    """A validated relative POSIX path guaranteed to stay inside the workspace."""

    value: str

    def __str__(self) -> str:
        return self.value


def validate_relative_path(path: str, *, allow_dot: bool = False) -> SafePath:
    """Validate and normalize a relative POSIX workspace path.

    Rejects: absolute paths, ``..`` segments (including after normalization),
    NUL bytes, empty paths (unless ``allow_dot``), and paths that normalize to
    ``..``/root. Returns the normalized relative path.
    """
    if not isinstance(path, str):
        raise InvalidPathError("path must be a string")
    if "\x00" in path:
        raise InvalidPathError("path contains a NUL byte")
    if not path:
        if allow_dot:
            return SafePath(".")
        raise InvalidPathError("path must not be empty")
    if path.startswith("/"):
        raise InvalidPathError("absolute paths are not allowed")
    # Reject Windows-style separators/backslash traversal attempts.
    if "\\" in path:
        raise InvalidPathError("backslash is not allowed in workspace paths")
    normalized = posixpath.normpath(path)
    if normalized in {"..", ".", ""}:
        if normalized == "." and allow_dot:
            return SafePath(".")
        raise InvalidPathError("path must stay inside the workspace")
    if normalized.startswith("../") or normalized == "..":
        raise InvalidPathError("path must stay inside the workspace")
    if normalized.startswith("/"):
        raise InvalidPathError("path must stay inside the workspace")
    return SafePath(normalized)


def join_workspace(*segments: str) -> SafePath:
    """Join normalized segments and validate the result stays inside /workspace."""
    raw = "/".join(segments)
    return validate_relative_path(raw)


def scope_key(deployment_id: str, session_id: str) -> str:
    """Canonical ownership scope string used for binding and idempotency.

    ``session_id`` is globally unique (created by LearnGraph), so the scope is
    ``deployment|session``; the workspace id is recorded on the sandbox record
    for audit but is not part of the binding key. This keeps the Port-layer
    adapter free of workspace context while preserving per-sandbox isolation.
    """
    return f"{deployment_id}|{session_id}"
