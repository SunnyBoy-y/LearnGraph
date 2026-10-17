"""Opt-in answer endpoint protection, independent of fast barge-in."""
from __future__ import annotations

import asyncio
import time
from collections import deque

import numpy as np
from loguru import logger
from pipecat.frames.frames import InputAudioRawFrame, TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import TurnAnalyzerUserTurnStopStrategy


class GuardedSmartTurnStopStrategy(TurnAnalyzerUserTurnStopStrategy):
    def __init__(self, *, min_quiet_secs: float = .9, continuation_quiet_secs: float = 1.5,
                 max_quiet_secs: float = 2.5, signal_rms: float = .0015, **kwargs):
        super().__init__(**kwargs)
        self._min_quiet_secs = max(0., min(5., max(min_quiet_secs, continuation_quiet_secs)))
        self._max_quiet_secs = max(self._min_quiet_secs, min(10., max_quiet_secs))
        # Match review invalidation: weak PCM must postpone admission even
        # when Silero never emits another VAD-start frame.
        self._signal_rms = max(0., signal_rms)
        self._last_signal_at = time.monotonic()
        self._guard_task: asyncio.Task | None = None
        self._guard_epoch = 0
        self._activity_revision = 0
        self._model_complete = False
        self._physical_turn = False
        self._turn_released = False
        self.end_events: deque[dict] = deque(maxlen=32)

    async def _cancel_guard(self):
        self._guard_epoch += 1
        task, self._guard_task = self._guard_task, None
        if task and task is not asyncio.current_task():
            await self.task_manager.cancel_task(task)

    async def handle_user_turn_started(self):
        await self._cancel_guard()
        self._turn_released = False
        self._physical_turn = self._physical_turn or self._vad_user_speaking
        self._model_complete = False
        await super().handle_user_turn_started()

    async def handle_user_turn_stopped(self):
        await self._cancel_guard()
        self._model_complete = False
        self._physical_turn = False
        self._turn_released = True
        await super().handle_user_turn_stopped()

    async def cleanup(self):
        await self._cancel_guard()
        await super().cleanup()

    async def process_frame(self, frame):
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._last_signal_at = time.monotonic()
            self._activity_revision += 1
            self._physical_turn = True
            self._turn_released = False
            self._model_complete = False
            await self._cancel_guard()
        elif isinstance(frame, InputAudioRawFrame) and frame.audio:
            pcm = np.frombuffer(frame.audio, dtype='<i2').astype(np.float32)
            if len(pcm) and float(np.sqrt(np.mean(pcm * pcm))) / 32768 >= self._signal_rms:
                self._last_signal_at = time.monotonic()
                self._activity_revision += 1
                if self._vad_stopped:
                    # This revokes an old endpoint, never opens a turn or
                    # interrupts an answer. Barge-in stays on its own path.
                    self._model_complete = False
                    self._turn_complete = False
        revision = self._activity_revision
        result = await super().process_frame(frame)
        if revision != self._activity_revision and isinstance(frame, (VADUserStoppedSpeakingFrame, InputAudioRawFrame)):
            # A model inference awaited in a worker may finish after new PCM.
            self._model_complete = False
            self._turn_complete = False
        if isinstance(frame, (VADUserStoppedSpeakingFrame, TranscriptionFrame)):
            # Upstream's safety net requires COMPLETE; arm our bounded quiet
            # fallback even when the semantic model keeps saying INCOMPLETE.
            if self._physical_turn and not self._vad_user_speaking:
                self._arm_guard()
        return result

    async def _handle_prediction_result(self, result):
        if result is not None:
            self._model_complete = bool(result.is_complete)
            logger.info('SmartTurn guard prediction complete={} probability={:.4f} inference_ms={:.1f}',
                        result.is_complete, result.probability, result.e2e_processing_time_ms)
        await super()._handle_prediction_result(result)

    async def trigger_user_turn_stopped(self, *, enable_user_speaking_frames=None):
        self._arm_guard(enable_user_speaking_frames=enable_user_speaking_frames)

    def _arm_guard(self, *, enable_user_speaking_frames=None):
        if self._guard_task is not None or self._turn_released:
            return
        # A late ASR final alone must not manufacture a physical speech turn.
        if not self._physical_turn:
            return
        epoch = self._guard_epoch

        async def confirm():
            try:
                while epoch == self._guard_epoch and not self._turn_released:
                    quiet = time.monotonic() - self._last_signal_at
                    complete = self._model_complete and self._turn_complete
                    required = self._min_quiet_secs if complete else self._max_quiet_secs
                    if quiet < required:
                        await asyncio.sleep(required - quiet)
                        continue
                    if self._vad_user_speaking:
                        return
                    if self._wait_for_transcript and not self._text.strip():
                        return  # A later transcription can re-arm the guard.
                    transcript_ready = (not self._wait_for_transcript or self._transcript_finalized
                                        or self._timeout_expired or self._timeout_task is None)
                    if not transcript_ready:
                        await asyncio.sleep(.02)
                        continue
                    if not self._physical_turn:
                        return
                    reason = 'semantic-complete' if complete else 'inactivity-fallback'
                    self._turn_released = True
                    self._guard_task = None
                    self.end_events.append({'reason': reason, 'quietMs': quiet * 1000,
                                            'activityRevision': self._activity_revision})
                    await super(GuardedSmartTurnStopStrategy, self).trigger_user_turn_stopped(
                        enable_user_speaking_frames=enable_user_speaking_frames)
                    return
            finally:
                if self._guard_task is asyncio.current_task():
                    self._guard_task = None
        self._guard_task = self.task_manager.create_task(confirm(), f'{self}::quiet_confirmation')
