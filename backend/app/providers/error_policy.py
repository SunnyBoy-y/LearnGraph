"""Shared classification of provider failures (retryable or terminal).

Every place that retries a provider call needs the same answer to one question:
*is this failure worth repeating?* A rejected request — unknown model, bad
payload, revoked credential — cannot start succeeding by being sent again; the
repeat only spends another round trip and buries the actionable error under a
generic "failed after N attempts".

Kept next to the provider layer (rather than inside one service) so the chat
stream backoff loop, the structured-generation retry loops and any future
caller can share one policy instead of growing near-duplicates.
"""

from __future__ import annotations

from app.providers.local.model import ModelProviderUnavailableError

# Upstream statuses that signal a transient gateway/overload condition:
# 502/503/504 are relay (Cloudflare) failures, 500 covers flaky origins,
# 408/429 are explicit try-again signals, 529 is Anthropic "overloaded".
# Mirrors the chat stream policy in ``services/chat.py``.
RETRYABLE_PROVIDER_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})


def provider_failure_is_transient(error: BaseException | None) -> bool:
    """Whether repeating the identical provider request could plausibly work."""

    if error is None:
        return True
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status in RETRYABLE_PROVIDER_HTTP_STATUSES
    # No HTTP status at all (connection reset, timeout, truncated SSE): these
    # describe transport trouble rather than a rejected request, so they stay
    # retryable. A provider we resolved as unusable, however, is a
    # configuration fact that a retry cannot change.
    return not isinstance(error, ModelProviderUnavailableError)
