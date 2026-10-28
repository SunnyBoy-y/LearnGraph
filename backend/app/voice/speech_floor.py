"""One event-loop-owned speech floor, independent of tasks and persistence.

Reservations are optimistic tokens, never locks held across provider/database IO.
User activity invalidates a token synchronously. Callers must validate after IO
and immediately before dispatch. Audio stays busy until the transport ledger
drains, not until a model end frame or a gap between audio chunks.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from pipecat.frames.frames import (
    BotStartedSpeakingFrame, CancelFrame, EndFrame, ErrorFrame, Frame,
    FunctionCallsStartedFrame, FunctionCallResultFrame, FunctionCallCancelFrame,
    InterruptionFrame, LLMContextFrame, LLMFullResponseStartFrame,
    LLMFullResponseEndFrame, LLMMessagesAppendFrame, TextFrame, TranscriptionFrame,
    VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection


@dataclass(frozen=True)
class SpeechLease:
    owner: str
    revision: int


class VoiceSpeechFloor:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.revision = 0
        self.lease: SpeechLease | None = None
        self.user_speaking = False
        self.generating = False
        self.response_pending = False
        self.audio_pending = False
        self.tools: set[str] = set()
        self.quiet_until = 0.0
        self.closed = False

    @property
    def busy(self) -> bool:
        return (self.closed or self.user_speaking or self.generating or
                self.response_pending or self.audio_pending or bool(self.tools) or
                self.clock() < self.quiet_until)

    def reserve(self, owner: str) -> SpeechLease | None:
        if self.busy or self.lease is not None:
            return None
        self.lease = SpeechLease(owner, self.revision)
        return self.lease

    def valid(self, lease: SpeechLease) -> bool:
        return self.lease == lease and lease.revision == self.revision and not self.busy

    def release(self, lease: SpeechLease) -> None:
        if self.lease == lease:
            self.lease = None

    def interrupt(self) -> None:
        self.revision += 1
        self.lease = None
        self.generating = self.response_pending = self.audio_pending = False
        self.tools.clear()

    def playback_ended(self) -> None:
        self.audio_pending = False
        if not self.generating and not self.tools and not self.response_pending:
            self.lease = None

    def observe(self, frame: Frame, *, input_side: bool = False) -> None:
        if isinstance(frame, (CancelFrame, EndFrame)):
            self.interrupt()
            self.closed = True
        elif isinstance(frame, InterruptionFrame):
            self.interrupt()
        elif isinstance(frame, ErrorFrame):
            self.generating = self.response_pending = False
            self.tools.clear()
            if not self.audio_pending:
                self.lease = None
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self.revision += 1
            self.user_speaking = True
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self.revision += 1
            self.user_speaking = False
            self.quiet_until = self.clock() + 1.0
        elif input_side:
            if isinstance(frame, LLMContextFrame):
                self.revision += 1
                self.response_pending = True
            elif isinstance(frame, (TranscriptionFrame, LLMMessagesAppendFrame)):
                self.revision += 1
                self.quiet_until = self.clock() + 1.0
                if (isinstance(frame, TranscriptionFrame) and frame.text.strip()
                        and not (isinstance(getattr(frame, "result", None), dict) and frame.result.get("review_pending"))):
                    self.response_pending = True
        elif isinstance(frame, LLMFullResponseStartFrame):
            self.revision += 1
            self.generating, self.response_pending = True, False
        elif isinstance(frame, FunctionCallsStartedFrame):
            self.tools.update(call.tool_call_id for call in frame.function_calls)
        elif isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
            self.tools.discard(frame.tool_call_id)
            if frame.run_llm is not False:
                self.response_pending = True
        elif isinstance(frame, TextFrame) and frame.text.strip():
            self.audio_pending = True
        elif isinstance(frame, BotStartedSpeakingFrame):
            self.audio_pending = True
        elif isinstance(frame, LLMFullResponseEndFrame):
            self.generating = False
            if not self.audio_pending and not self.tools and not self.response_pending:
                self.lease = None


class VoiceInputFloorProcessor(FrameProcessor):
    """Observe admitted user context before it crosses the worker bus."""
    def __init__(self, floor: VoiceSpeechFloor, journal=None):
        super().__init__()
        self.floor = floor
        self.journal = journal

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            admission = frame.metadata.get("voice_admission")
            if admission is not None:
                lease = frame.metadata.pop("voice_speech_lease")
                frame.metadata.pop("voice_admission")
                admitted = not admission.cancelled() and self.floor.valid(lease)
                request_id = frame.metadata.pop("voice_request_id")
                delivery_turn = None
                if admitted and frame.metadata.pop("voice_result_delivery", False) and self.journal is not None:
                    try:
                        delivery_turn = await self.journal.prepare_delivery(request_id)
                    except Exception as exc:
                        self.floor.release(lease)
                        if not admission.done():
                            admission.set_exception(exc)
                        return
                    admitted = not admission.cancelled() and self.floor.valid(lease)
                if not admitted:
                    self.floor.release(lease)
                    if delivery_turn is not None:
                        await self.journal.discard_delivery(delivery_turn)
                    if not admission.done():
                        admission.set_result(False)
                    return
                self.floor.response_pending = True
                if self.journal is not None:
                    if delivery_turn is not None:
                        self.journal.attach_delivery(delivery_turn)
                    else:
                        self.journal.expect_typed_turn(request_id, frame.messages[0]["content"])
                if not admission.done():
                    admission.set_result(True)
            self.floor.observe(frame, input_side=True)
        await self.push_frame(frame, direction)
