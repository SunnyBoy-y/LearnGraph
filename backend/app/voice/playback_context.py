"""In-memory assistant context owned by released audio markers, not TTS input."""

from typing import Any

from pipecat.frames.frames import Frame, TextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class VoicePlaybackContext(FrameProcessor):
    """Keep Pipecat's tools, but give the journal ownership of spoken text.

    Place before the assistant aggregator. A background run_tts returns before
    synthesis, so its framework TTSTextFrames cannot prove playback. Disable
    their context writes and update one message per turn from the output relay.
    """

    def __init__(self, context: Any):
        super().__init__()
        self.context = context
        self._turn_id: str | None = None
        self._message: dict | None = None

    def update_spoken(self, turn_id: str, text: str) -> None:
        if turn_id != self._turn_id:
            self._turn_id, self._message = turn_id, None
        if not text:
            return
        if self._message is None:
            self._message = {"role": "assistant", "content": text}
            self.context.add_message(self._message)
        else:
            self._message["content"] = text

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TextFrame):
            frame.append_to_context = False
        await self.push_frame(frame, direction)
