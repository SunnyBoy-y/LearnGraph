"""Regression for ASR final -> aggregator start/interruption -> playback.

Uses the installed Pipecat aggregator's real start handler and LearnGraph's
real journal/relay. Only SQL and frame transport are replaced; no cloud keys.
Run with pytest or: python -m unittest discover -s tests/voice -p test_voice_turn_start_order.py
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import (
    InterruptionFrame, TranscriptionFrame, TTSAudioRawFrame,
    UserStartedSpeakingFrame, VADUserStartedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
from pipecat.processors.frame_processor import FrameDirection

from app.voice import journal as journal_mod
from app.voice.caption_ledger import VoiceLedgerFrame, VoiceLedgerRelay
from app.voice.embedded_bot import handle_learngraph_client_message, InterruptIdDeduplicator
from app.voice.embedded_generation import VoiceGenerationGate


class TurnStartOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.live = []
        self.accepted = []
        self.interrupted = []
        self.finalized = []

        def emit(session_id, event_type, **kwargs):
            event = {"type": event_type, "event_id": f"e-{len(self.events)}", **kwargs}
            self.events.append(event)
            return event

        def accept(session_id, text, **kwargs):
            result = {"turn_id": f"turn-{len(self.accepted) + 1}", "user_text": text}
            self.accepted.append(result)
            return result

        def interrupt(session_id, **kwargs):
            self.interrupted.append(kwargs)
            return kwargs

        def finalize(session_id, turn_id, text, **kwargs):
            result = {"turn_id": turn_id, "text": text, **kwargs}
            self.finalized.append(result)
            return result

        for target, replacement in (
            ("emit_event", emit), ("turn_api.open_turn", lambda *args: None),
            ("turn_api.accept_turn", accept), ("turn_api.interrupt_turn", interrupt),
            ("turn_api.finalize_turn", finalize),
        ):
            patcher = patch(f"app.voice.journal.{target}", replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        async def publish(event):
            self.live.append(event)

        self.journal = journal_mod.VoiceTurnJournal("vs-start-order", publish=publish)
        self.user = journal_mod.VoiceJournalProcessor(self.journal, role="user")
        self.assistant = journal_mod.VoiceJournalProcessor(self.journal, role="assistant")
        self.relay = VoiceLedgerRelay(journal=self.journal)
        self.relay.push_frame = AsyncMock()

    async def asyncTearDown(self):
        self.journal._cancel_recovery_timers()
        await self.journal.drain_persistence()

    async def final(self, text):
        frame = TranscriptionFrame(text=text, user_id="user", timestamp="now")
        await self.user._observe_user(frame, FrameDirection.DOWNSTREAM)

    async def aggregator_start(self):
        # Run Pipecat's real handler, including its automatic flush interruption.
        async def broadcast_frame(frame_type):
            await self.user._observe_user(frame_type(), FrameDirection.UPSTREAM)
            await self.assistant._observe_assistant(frame_type(), FrameDirection.DOWNSTREAM)

        async def broadcast_interruption():
            await self.assistant._observe_assistant(InterruptionFrame(), FrameDirection.DOWNSTREAM)

        aggregator = SimpleNamespace(
            broadcast_frame=broadcast_frame,
            broadcast_interruption=broadcast_interruption,
            _user_idle_controller=SimpleNamespace(process_frame=AsyncMock()),
            _call_event_handler=AsyncMock(),
        )
        await LLMUserAggregator._on_user_turn_started(
            aggregator, None, None,
            SimpleNamespace(enable_user_speaking_frames=True, enable_interruptions=True),
        )

    async def play(self, text, token="s1"):
        self.journal.note_assistant_text(text)
        await self.relay.process_frame(VoiceLedgerFrame(
            kind="start", token=token, turn_id=self.journal.current_turn_id(),
            text=text, context_id="ctx", audio_cursor_ms=0,
        ), FrameDirection.DOWNSTREAM)
        # No text is authorized before a successful output audio write.
        self.assertFalse(self.journal.sentence_recorded(1))
        await self.relay.process_frame(TTSAudioRawFrame(
            audio=b"\0" * 960, sample_rate=24000, num_channels=1,
        ), FrameDirection.DOWNSTREAM)
        await self.relay.process_frame(VoiceLedgerFrame(
            kind="end", token=token, audio_end_cursor_ms=20,
        ), FrameDirection.DOWNSTREAM)

    async def test_initial_final_survives_aggregator_flush_and_emits_captions(self):
        await self.final("请解释一下")
        await self.aggregator_start()
        self.assertEqual(self.journal.current_turn_id(), "turn-1")
        await self.play("这是回答。")
        self.assertEqual(self.journal.heard_text(), "这是回答。")
        kinds = [event["type"] for event in self.live]
        self.assertIn("assistant.sentence.queued", kinds)
        self.assertIn("assistant.sentence.ended", kinds)
        self.assertNotIn("turn.interrupted", kinds)

    async def test_barge_in_closes_old_turn_but_flush_preserves_replacement(self):
        await self.final("第一个问题")
        await self.play("第一轮回答。")
        await self.final("第二个问题")
        await self.aggregator_start()
        self.assertEqual(self.journal.current_turn_id(), "turn-2")
        await self.play("第二轮回答。", "s2")
        await self.journal.drain_persistence()
        self.assertEqual([item["turn_id"] for item in self.interrupted], ["turn-1"])
        self.assertEqual(self.journal.heard_text(), "第二轮回答。")

    async def test_vad_onset_precedes_final_and_confirmed_start_does_not_duplicate_it(self):
        await self.user._observe_user(VADUserStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await self.final("测试延迟")
        await self.aggregator_start()
        kinds = [event["type"] for event in self.live]
        self.assertEqual(kinds.count("user.started"), 1)
        self.assertLess(kinds.index("user.started"), kinds.index("user.final"))
        self.assertEqual(self.journal.current_turn_id(), "turn-1")

    async def test_vad_pause_inside_merged_question_does_not_split_latency_round(self):
        await self.user._observe_user(VADUserStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await self.final("请用两句话解释。")
        await self.aggregator_start()
        await self.user._observe_user(VADUserStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await self.final("为什么天空是蓝色的？")
        await self.play("这是回答。")
        kinds = [event["type"] for event in self.live]
        self.assertEqual(kinds.count("user.started"), 1)
        self.assertEqual(self.journal.current_turn_id(), "turn-1")
        self.assertIn("assistant.sentence.queued", kinds)
        self.assertEqual(self.journal._turn.user_text, "请用两句话解释。为什么天空是蓝色的？")

    async def test_vad_onset_during_answer_is_still_reported_for_barge_in(self):
        await self.user._observe_user(VADUserStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await self.final("第一个问题")
        await self.play("第一轮回答。")
        await self.user._observe_user(VADUserStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await self.final("第二个问题")
        kinds = [event["type"] for event in self.live]
        self.assertEqual(kinds.count("user.started"), 2)
        self.assertEqual(self.journal.current_turn_id(), "turn-2")

    async def test_explicit_button_interrupt_still_closes_pre_token_turn(self):
        await self.final("还在等待模型")

        async def stop():
            await self.assistant._observe_assistant(InterruptionFrame(), FrameDirection.DOWNSTREAM)

        await handle_learngraph_client_message(
            message=SimpleNamespace(type="learngraph-interrupt", data={"interruptId": "stop-1"}),
            journal=self.journal, generation_gate=VoiceGenerationGate(),
            interrupt_bot=stop, interrupts=InterruptIdDeduplicator(),
        )
        self.assertIsNone(self.journal.current_turn_id())
        await self.journal.drain_persistence()
        self.assertEqual(len(self.interrupted), 1)

    async def test_delayed_control_interrupt_cannot_close_replacement_turn(self):
        await self.final("上一轮")
        self.journal.note_assistant_text("正在生成")
        await self.final("新问题")
        await self.journal.turn_interrupted(reason="client_interrupt", turn_id="turn-1")
        self.assertEqual(self.journal.current_turn_id(), "turn-2")

    async def test_real_pipeline_interrupt_still_closes_generated_answer(self):
        await self.final("已经开始回答")
        self.journal.note_assistant_text("生成中的回答")
        await self.assistant._observe_assistant(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        self.assertIsNone(self.journal.current_turn_id())
        self.assertEqual(self.journal.heard_text(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
