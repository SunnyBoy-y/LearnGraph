"""Shared rules for session titles before model naming completes."""

from __future__ import annotations

import re
from typing import Final


# Keep the fallback compact enough for the sidebar while retaining the actual
# wording of the user's question.  The frontend uses the same value so its
# optimistic title and the persisted title never flicker between two strings.
SESSION_TITLE_FALLBACK_MAX_CHARS: Final[int] = 24
DEFAULT_SESSION_TITLES: Final[frozenset[str]] = frozenset(
    {"新会话", "新学习会话"}
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_title_seed(value: str | None) -> str:
    """Return a one-line, user-visible form of a question."""

    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()


def fallback_session_title(question: str | None) -> str:
    """Use the first fixed-size slice of a question as a temporary title."""

    return normalize_title_seed(question)[:SESSION_TITLE_FALLBACK_MAX_CHARS]


def is_default_session_title(title: str | None) -> bool:
    return normalize_title_seed(title) in DEFAULT_SESSION_TITLES


def is_question_fallback_title(title: str | None, question: str | None) -> bool:
    """Whether ``title`` is the deterministic fallback for ``question``."""

    candidate = fallback_session_title(question)
    return bool(candidate) and normalize_title_seed(title) == candidate


def apply_session_title_fallback(session: object, question: str | None) -> str:
    """Set a draft session's title, returning the title that was selected.

    The caller decides whether the message is the first local user message.
    Keeping that check outside this small pure rule lets all message paths
    (text, image, and voice) share exactly the same normalization and limit.
    """

    current_title = getattr(session, "title", None)
    candidate = fallback_session_title(question)
    if not candidate or not is_default_session_title(current_title):
        return ""
    setattr(session, "title", candidate)
    return candidate
