"""Pipeline-level silent-drop filter for ASR noise and bystander fragments."""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.voice.embedded_turn_intent import classify_noise_utterance


class VoiceNoiseFilterProcessor(FrameProcessor):
    """Drop non-meaningful ASR fragments before journal, floor, and context."""

    def __init__(self) -> None:
        super().__init__()
        self.dropped_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, InterimTranscriptionFrame):
                reason = classify_noise_utterance(frame.text, is_interim=True)
                if reason is not None:
                    self.dropped_count += 1
                    return
            elif isinstance(frame, TranscriptionFrame):
                reason = classify_noise_utterance(frame.text, is_interim=False)
                if reason is not None:
                    self.dropped_count += 1
                    logger.info(
                        "voice_noise_dropped reason={} text_len={}",
                        reason,
                        len(frame.text or ""),
                    )
                    return
        await self.push_frame(frame, direction)
