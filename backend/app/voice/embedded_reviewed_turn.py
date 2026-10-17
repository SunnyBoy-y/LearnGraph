"""Opt-in final text/answer admission after whole-turn ASR review.

Original realtime finals still reach the turn controller (and fast interrupt).
Only this aggregator writes the admitted user text and triggers one answer.
"""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4
from collections import OrderedDict, deque

from pipecat.frames.frames import CancelFrame, EndFrame, InterruptionFrame, LLMRunFrame, LLMMessagesAppendFrame, TranscriptionFrame, UserStoppedSpeakingFrame
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator, UserTurnMessageAddedMessage, UserTurnStoppedMessage
from loguru import logger
from pipecat.turns.user_stop.base_user_turn_stop_strategy import UserTurnStoppedParams

from app.voice.embedded_turn_intent import is_asr_filler


class ReviewedUserAggregator(LLMUserAggregator):
    def __init__(self, context, *, stt, journal=None, review_wait_secs: float = 1.5, **kwargs):
        super().__init__(context, **kwargs)
        self._review_stt, self._review_journal = stt, journal
        self._review_wait = max(.05, min(5., review_wait_secs))
        self._audio_sequence = stt.current_audio_turn
        self._review_epoch = 0
        self._drafts: dict[int, list[str]] = {}
        self._sealed: OrderedDict[int, None] = OrderedDict()
        self._pending_answers: set[asyncio.Task] = set()
        self._committing_answers: set[asyncio.Task] = set()
        self._admission_lock = asyncio.Lock()
        self._review_closed = False
        self._review_identity = uuid4().hex
        self.admissions: deque[dict] = deque(maxlen=32)
        stt.audio_turn_started = self.audio_turn_started

    def audio_turn_started(self, sequence):
        if sequence == self._audio_sequence:
            return
        self._audio_sequence = sequence
        self.invalidate_pending()
        self._drafts = {sequence: []}

    def invalidate_pending(self):
        self._review_epoch += 1
        for task in list(self._pending_answers):
            # SQL offloaded to a thread cannot be cancelled atomically. Finish
            # journal admission, then apply the epoch fence before any answer.
            if task not in self._committing_answers:
                task.cancel()

    async def process_frame(self, frame, direction):
        if isinstance(frame, (CancelFrame, EndFrame)):
            self._review_closed = True
            self.invalidate_pending()
        elif isinstance(frame, InterruptionFrame):
            self.invalidate_pending()
        elif isinstance(frame, LLMMessagesAppendFrame) and any(
            isinstance(m, dict) and m.get('role') == 'user' for m in frame.messages or []
        ):
            self.invalidate_pending()
        if isinstance(frame, LLMRunFrame) and self._pending_answers:
            return
        if isinstance(frame, TranscriptionFrame) and isinstance(frame.result, dict) and frame.result.get('review_pending'):
            sequence = frame.result.get('audio_turn')
            if sequence is not None and sequence == self._review_stt.current_audio_turn and sequence != self._audio_sequence:
                self.audio_turn_started(sequence)
            if sequence != self._audio_sequence or sequence in self._sealed:
                # A late final may be a fallback for its own pending review, but
                # must never reach the controller as a fresh physical turn.
                if sequence == self._audio_sequence:
                    self._remember(frame)
                return
        await super().process_frame(frame, direction)

    def _remember(self, frame):
        sequence = (frame.result or {}).get('audio_turn')
        text = str(frame.text or '').strip()
        if sequence == self._audio_sequence and text and not is_asr_filler(text):
            self._drafts.setdefault(sequence, []).append(text)

    async def _handle_transcription(self, frame):
        if isinstance(frame.result, dict) and frame.result.get('review_pending'):
            sequence = frame.result.get('audio_turn')
            if sequence is not None and sequence == self._review_stt.current_audio_turn and sequence != self._audio_sequence:
                self.audio_turn_started(sequence)
            self._remember(frame)
            return
        await super()._handle_transcription(frame)

    async def _on_user_turn_inference_triggered(self, controller, strategy, speculation=None):
        # Do not generate speculatively from unreviewed text. This event is not
        # needed for fast interruption; the unchanged start strategy owns that.
        return

    async def _on_user_turn_stopped(self, controller, strategy, params: UserTurnStoppedParams):
        if self._audio_sequence is None or self._audio_sequence in self._sealed:
            return
        sealed = self._review_stt.seal_audio_turn()
        if sealed is None:
            return
        sequence, future = sealed
        if sequence in self._sealed or sequence != self._audio_sequence:
            return
        self._sealed[sequence] = None
        while len(self._sealed) > 64:
            self._sealed.popitem(last=False)
        epoch = self._review_epoch
        boundary = time.perf_counter()
        timestamp = self._user_turn_start_timestamp
        if params.enable_user_speaking_frames:
            await self.broadcast_frame(UserStoppedSpeakingFrame)
        await self._user_idle_controller.process_frame(UserStoppedSpeakingFrame())
        task = asyncio.create_task(self._admit(sequence, future, epoch, boundary, timestamp, strategy), name=f'reviewed-answer-{sequence}')
        self._pending_answers.add(task)
        task.add_done_callback(self._pending_answers.discard)

    async def _admit(self, sequence, future, epoch, boundary, timestamp, strategy):
        try:
            result = None
            quality = {}
            quality_future = getattr(future, 'review_quality', None)
            if future is not None:
                try:
                    deadline = boundary + self._review_wait
                    if quality_future is not None:
                        quality = await asyncio.wait_for(asyncio.shield(quality_future), max(.001, deadline-time.perf_counter()))
                        if quality.get('canonicalWeakAudio'):
                            deadline = boundary + max(self._review_wait, getattr(self._review_stt, 'review_weak_answer_wait_secs', 3.))
                    result = await asyncio.wait_for(asyncio.shield(future), max(.001, deadline-time.perf_counter()))
                except Exception as exc:
                    # A failed review is a bounded fallback, never a second run.
                    result = {'status': 'timeout' if isinstance(exc, TimeoutError) else 'error'}
            valid = isinstance(result, dict) and result.get('sequence') == sequence and result.get('status') == 'ok'
            review_ready = time.perf_counter()
            reviewed = str(result.get('text') or '').strip() if valid else ''
            if reviewed and is_asr_filler(reviewed):
                reviewed = ''
            # The weak-audio experiment must not turn a late/failed review into
            # an authoritative command from the known-unreliable realtime ASR.
            # Fail closed with the existing non-degrading error notification.
            if not reviewed and quality_future is not None and quality.get('canonicalWeakAudio', True):
                if not self._review_closed and epoch == self._review_epoch and sequence == self._audio_sequence:
                    self.admissions.append({'sequence': sequence, 'text': '', 'source': 'review_rejected',
                        'waitMs': (time.perf_counter()-boundary)*1000, 'reviewStatus': (result or {}).get('status')})
                    if self._review_journal is not None:
                        await self._review_journal.processor_error('asr_review',
                            '声音较轻，整句识别未完成，请再说一遍。', retryable=True, degraded=False)
                return
            text = reviewed or ''.join(self._drafts.get(sequence, []))
            lock_started = time.perf_counter()
            async with self._admission_lock:
                lock_ms = (time.perf_counter() - lock_started) * 1000
                if self._review_closed or epoch != self._review_epoch or sequence != self._audio_sequence or not text:
                    return
                self._committing_answers.add(asyncio.current_task())
                source = 'review' if reviewed else 'realtime_fallback'
                journal_started = time.perf_counter()
                try:
                    if self._review_journal is not None:
                        await self._review_journal.user_final(text, source_id=f'review:{self._review_identity}:{sequence}', new_turn=True)
                finally:
                    self._committing_answers.discard(asyncio.current_task())
                if self._review_closed or epoch != self._review_epoch:
                    return
                self._context.add_message({'role': 'user', 'content': text})
                self.admissions.append({'sequence': sequence, 'text': text, 'source': source,
                    'waitMs': (time.perf_counter()-boundary)*1000,
                    'reviewWaitMs': (review_ready-boundary)*1000,
                    'admissionLockMs': lock_ms, 'journalMs': (time.perf_counter()-journal_started)*1000,
                    'reviewStatus': (result or {}).get('status')})
                # No transcription frame is re-injected: one context push only.
                await self.push_context_frame()
                await self._call_event_handler('on_user_turn_message_added', UserTurnMessageAddedMessage(content=text, timestamp=timestamp))
                await self._call_event_handler('on_user_turn_stopped', strategy, UserTurnStoppedMessage(content=text, timestamp=timestamp))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('Reviewed user admission failed sequence={} error={}', sequence, type(exc).__name__)
        finally:
            self._committing_answers.discard(asyncio.current_task())
            self._drafts.pop(sequence, None)

    async def drain_answers(self):
        if self._pending_answers:
            await asyncio.gather(*list(self._pending_answers), return_exceptions=True)

    async def cleanup(self):
        self._review_closed = True
        self.invalidate_pending()
        await self.drain_answers()
        self._review_stt.audio_turn_started = None
        await super().cleanup()
