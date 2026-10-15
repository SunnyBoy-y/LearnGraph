"""ICE URL normalization for the deployment relay config.

Pure functions only (no DB), so this lives in `tests/unit`. The behaviour that
matters: the URL list is what both the browser and aiortc receive, and aiortc
honours **only the first** TURN entry -- so a list that silently repeats a
transport is a list whose real preference cannot be read.
"""

from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.services.voice_relay import DEFAULT_URLS, VoiceRelayService, _dedupe_urls

DUPLICATED = (
    "stun:stun.cloudflare.com:3478",
    "turn:turn.cloudflare.com:3478?transport=tcp",
    "turn:turn.cloudflare.com:3478?transport=udp",
    "turns:turn.cloudflare.com:5349?transport=tcp",
    "turns:turn.cloudflare.com:5349?transport=tcp",
)


def test_dedupe_keeps_first_occurrence_order() -> None:
    assert _dedupe_urls(DUPLICATED) == (
        "stun:stun.cloudflare.com:3478",
        "turn:turn.cloudflare.com:3478?transport=tcp",
        "turn:turn.cloudflare.com:3478?transport=udp",
        "turns:turn.cloudflare.com:5349?transport=tcp",
    )


def test_dedupe_drops_blanks_and_handles_empty() -> None:
    assert _dedupe_urls(["", "  ", "stun:a:3478", "stun:a:3478"]) == ("stun:a:3478",)
    assert _dedupe_urls(None) == ()
    assert _dedupe_urls([]) == ()


def test_validate_urls_normalizes_on_write() -> None:
    """Saving the duplicated list must store it once."""
    validated = VoiceRelayService._validate_urls(list(DUPLICATED))
    assert len(validated) == 4
    assert validated.count("turns:turn.cloudflare.com:5349?transport=tcp") == 1
    # Order is the administrator's transport preference: unchanged.
    assert validated[1] == "turn:turn.cloudflare.com:3478?transport=tcp"


def test_validate_urls_accepts_comma_separated_and_defaults() -> None:
    assert VoiceRelayService._validate_urls("stun:a:3478, stun:a:3478") == ("stun:a:3478",)
    assert VoiceRelayService._validate_urls(None) == DEFAULT_URLS


@pytest.mark.parametrize("bad", ["http://relay:3478", "relay:3478", "turn:"])
def test_validate_urls_rejects_unsupported_schemes(bad: str) -> None:
    with pytest.raises(AppError):
        VoiceRelayService._validate_urls([bad])


def test_validate_urls_requires_at_least_one() -> None:
    with pytest.raises(AppError):
        VoiceRelayService._validate_urls(["", "   "])
