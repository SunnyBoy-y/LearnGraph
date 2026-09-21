"""Generation isolation for assistant audio."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from app.voice.caption_ledger import VoiceLedgerFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class VoiceGenerationGate(FrameProcessor):
    """Drop TTS audio whose context belongs to an invalidated generation."""

    # Context IDs are unique per TTS turn. Bound the history so a long call
    # cannot retain every context forever; 512 turns is far beyond the useful
    # stale-audio window while still keeping the map small.
    MAX_CONTEXT_GENERATIONS = 512

    def __init__(self, *, name: str = "VoiceGenerationGate") -> None:
        super().__init__(name=name)
        self._generation = 0
        self._context_generation: OrderedDict[str, int] = OrderedDict()

    @property
    def generation(self) -> int:
        return self._generation

    def invalidate(self) -> int:
        self._generation += 1
        return self._generation

    def _remember_context(self, key: str, generation: int) -> None:
        if not key:
            return
        self._context_generation.pop(key, None)
        self._context_generation[key] = generation
        while len(self._context_generation) > self.MAX_CONTEXT_GENERATIONS:
            self._context_generation.popitem(last=False)

    def note_tts_started(self, context_id: str) -> int:
        key = str(context_id or "")
        self._remember_context(key, self._generation)
        return self._generation

    def accepts_audio(self, context_id: str) -> bool:
        key = str(context_id or "")
        frame_generation = self._context_generation.get(key)
        if frame_generation is None:
            frame_generation = self._generation
            self._remember_context(key, frame_generation)
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

        if isinstance(frame, TTSStoppedFrame):
            context_id = str(getattr(frame, "context_id", "") or "")
            if context_id:
                self._context_generation.pop(context_id, None)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, VoiceLedgerFrame):
            if frame.generation_id is not None and frame.generation_id != self._generation:
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
