from __future__ import annotations

from typing import Literal

ThinkingMode = Literal["off", "low", "medium", "high", "xhigh"]
_ORDER: tuple[str, ...] = ("off", "low", "medium", "high", "xhigh")

def clip_thinking_mode(requested: str | None, maximum: str | None) -> ThinkingMode:
    """Clamp a requested mode to the voice session's user-selected ceiling."""
    req = requested if requested in _ORDER else "off"
    cap = maximum if maximum in _ORDER else "high"
    return _ORDER[min(_ORDER.index(req), _ORDER.index(cap))]  # type: ignore[return-value]

def next_event_seq(current: int) -> int:
    return max(0, int(current)) + 1
