"""Generation isolation for assistant audio."""

from __future__ import annotations

from typing import Any

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class VoiceGenerationGate(FrameProcessor):
    """Drop TTS audio whose context belongs to an invalidated generation."""

    def __init__(self, *, name: str = "VoiceGenerationGate") -> None:
        super().__init__(name=name)
        self._generation = 0
        self._context_generation: dict[str, int] = {}

    @property
    def generation(self) -> int:
        return self._generation

    def invalidate(self) -> int:
        self._generation += 1
        return self._generation

    def note_tts_started(self, context_id: str) -> int:
        key = str(context_id or "")
        if key:
            self._context_generation[key] = self._generation
        return self._generation

    def accepts_audio(self, context_id: str) -> bool:
        key = str(context_id or "")
        frame_generation = self._context_generation.get(key)
        if frame_generation is None:
            frame_generation = self._generation
            if key:
                self._context_generation[key] = frame_generation
        return frame_generation == self._generation

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            self.invalidate()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSStartedFrame):
            self.note_tts_started(str(getattr(frame, "context_id", "") or ""))
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSAudioRawFrame):
            context_id = str(getattr(frame, "context_id", "") or "")
            if not self.accepts_audio(context_id):
                return

        await self.push_frame(frame, direction)

    def state(self) -> dict[str, Any]:
        return {
            "generation": self._generation,
            "contexts": dict(self._context_generation),
        }
