"""Bounded sentence-level repetition checks, before synthesis, never on tokens."""
from __future__ import annotations

from collections import deque
from difflib import SequenceMatcher
import re

from loguru import logger


class SpeechContentGuard:
    """Hard event/within-response dedup; semantic matches are observation only.

    Released audio is deliberately labelled server_released. Without a client
    playback receipt it cannot prove that a human heard a complete sentence.
    """

    def __init__(self) -> None:
        self._response: object = None
        self._submitted: dict[str, None] = {}
        self._released: deque[tuple[str, str, str]] = deque(maxlen=32)
        self.blocked = 0
        self.semantic_candidates = 0

    def observe_released(self, turn_id: str, text: str) -> None:
        if self._released and self._released[-1][0] == turn_id:
            self._released[-1] = (turn_id, text[-4000:], "server_released")
        else:
            self._released.append((turn_id, text[-4000:], "server_released"))

    def admit(self, text: str, response: object, *, no_repeat: bool = False, allow_repeat: bool = False) -> bool:
        if response != self._response:
            self._response = response
            self._submitted.clear()
        normalized = " ".join(text.split())
        # Do not suppress intentionally repeated short sounds, numbers, stage
        # dialogue, or verbatim punctuation tests. Text chunks are not events.
        if allow_repeat or len(normalized) < 12:
            return True
        if no_repeat and normalized in self._submitted:
            self.blocked += 1
            logger.info("voice.repeat_guard blocked=within_response chars={}", len(normalized))
            return False
        self._submitted[normalized] = None
        if len(self._submitted) > 128:
            self._submitted.pop(next(iter(self._submitted)))
        # Numbers and negations must agree even to flag a candidate. A match
        # never blocks new questions or regenerates speech after playback.
        facts = lambda value: (re.findall(r"\d+(?:\.\d+)?", value), re.findall(r"不|没|无|未|非|not|never", value))
        if len(normalized) >= 20:
            for _, previous, evidence in list(self._released)[-4:]:
                if facts(previous) == facts(normalized) and SequenceMatcher(None, previous[-256:], normalized[:256], autojunk=False).ratio() >= .85:
                    self.semantic_candidates += 1
                    logger.info("voice.repeat_guard semantic_candidate=true blocked=false evidence={} chars={}", evidence, len(normalized))
                    break
        return True
