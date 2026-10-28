"""Bounded PCM/subtitle join. Provider timestamps, not text length, own boundaries.

TTS 2.0 subtitles arrive after their audio. Hold only the not-yet-aligned tail;
release complete spans in order, keeping all silence and every PCM sample.
Missing/invalid subtitles are a protocol error, never guessed timing.
"""
from collections import deque
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class AlignedSpeech:
    text: str
    prefix: bytes
    audio: bytes


class SubtitleAudioJoin:
    def __init__(self, sample_rate: int, *, max_pending_seconds: int = 30):
        self.sample_rate = sample_rate
        self.limit = sample_rate * 2 * max_pending_seconds
        self.audio = bytearray()
        self.offset = 0
        self.last_end = 0
        self.segments = deque()
        self.count = 0

    def add_audio(self, data: bytes):
        if len(data) % 2:
            raise ValueError("TTS returned incomplete PCM sample")
        self.audio.extend(data)
        if len(self.audio) > self.limit:
            raise ValueError("TTS subtitle lag exceeded audio buffer limit")

    def add_subtitle(self, data: dict):
        text = str(data.get("text") or "").strip()
        words = data.get("words") or []
        if not text or not words:
            raise ValueError("TTS subtitle has no text/timestamps")
        start, end = float(words[0]["startTime"]), float(words[-1]["endTime"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("TTS subtitle has invalid timestamps")
        start, end = round(start * self.sample_rate) * 2, round(end * self.sample_rate) * 2
        if start < self.last_end or end <= start:
            raise ValueError("TTS subtitles overlap or go backwards")
        self.last_end = end
        self.segments.append((text, start, end))

    def ready(self):
        while self.segments and self.segments[0][2] <= self.offset + len(self.audio):
            text, start, end = self.segments.popleft()
            a, b = start - self.offset, end - self.offset
            span = AlignedSpeech(text, bytes(self.audio[:a]), bytes(self.audio[a:b]))
            del self.audio[:b]
            self.offset = end
            self.count += 1
            yield span

    def finish(self) -> bytes:
        if self.segments or not self.count:
            raise ValueError("TTS session ended without complete subtitle alignment")
        tail = bytes(self.audio)
        self.audio.clear()
        return tail
