import asyncio
from unittest.mock import AsyncMock

from pipecat.frames.frames import (
    FunctionCallFromLLM, FunctionCallResultFrame, FunctionCallsStartedFrame,
    InterruptionFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame, TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from app.voice.journal import VoiceTurnJournal, VoiceJournalProcessor, _TurnState
from app.voice.response_feedback import VoiceResponseFeedback
from app.voice.caption_ledger import VoiceLedgerFrame, VoiceLedgerRelay


class Journal:
    turn_id = "turn-1"
    def __init__(self): self.activities = []
    def current_turn_id(self): return self.turn_id
    async def assistant_activity(self, status, *, turn_id): self.activities.append((turn_id, status))


def setup_feedback(delay=.005):
    journal, frames = Journal(), []
    feedback = VoiceResponseFeedback(journal, delay=delay)
    async def push(frame, direction): frames.append(frame)
    feedback.push_frame = push
    return feedback, journal, frames


def tools(*ids):
    return FunctionCallsStartedFrame([FunctionCallFromLLM("delegate_tool_task", id, {}, None) for id in ids])


def test_search_progress_waits_then_names_the_actual_query():
    async def run():
        feedback, journal, frames = setup_feedback(delay=.02)
        await feedback.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await feedback.process_frame(FunctionCallsStartedFrame([
            FunctionCallFromLLM("search_web", "weather", {"query": "北京天气"}, None)
        ]), FrameDirection.DOWNSTREAM)
        await feedback.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        assert not [frame for frame in frames if isinstance(frame, TextFrame)]
        await asyncio.sleep(.04)
        assert [frame.text for frame in frames if isinstance(frame, TextFrame)] == ["我正在查询北京天气。"]
        await feedback.cleanup()
    asyncio.run(run())


def test_submitted_tts_cannot_be_failed_by_the_old_llm_grace_timer():
    async def run():
        journal = VoiceTurnJournal("offline")
        journal._turn = _TurnState(turn_id="tts-in-flight", llm_closed=True)
        journal.assistant_completed = AsyncMock()
        journal.tts_submitted()
        await journal._fallback_finalize()
        journal.assistant_completed.assert_not_awaited()
        await journal.tts_failed("tts-in-flight")
        journal.assistant_completed.assert_awaited_once()
    asyncio.run(run())


def test_slow_inference_gets_one_short_cue_before_its_answer():
    async def run():
        feedback, journal, frames = setup_feedback()
        await feedback.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(.02)
        await feedback.process_frame(TextFrame("答案是十四。"), FrameDirection.DOWNSTREAM)
        await feedback.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        assert [f.text for f in frames if isinstance(f, TextFrame)] == ["让我思考一下。", "答案是十四。"]
        assert frames[1].metadata["voice_acknowledgement"]
        assert journal.activities == [("turn-1", "thinking"), ("turn-1", "idle")]
        await feedback.cleanup()
    asyncio.run(run())


def test_fast_answer_does_not_get_a_late_filler():
    async def run():
        feedback, _, frames = setup_feedback()
        await feedback.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await feedback.process_frame(TextFrame("二。"), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(.02)
        assert [f.text for f in frames if isinstance(f, TextFrame)] == ["二。"]
        await feedback.cleanup()
    asyncio.run(run())


def test_parallel_tools_and_followup_rounds_do_not_repeat_the_cue_or_hide_work():
    async def run():
        feedback, journal, frames = setup_feedback(delay=1)
        await feedback.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await feedback.process_frame(tools("a", "b"), FrameDirection.DOWNSTREAM)
        await feedback.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        assert journal.activities[-1] == ("turn-1", "delegating")
        for id in ("a", "b"):
            await feedback.process_frame(FunctionCallResultFrame("delegate_tool_task", id, {}, {"status": "accepted"}), FrameDirection.DOWNSTREAM)
        assert journal.activities[-1] == ("turn-1", "thinking")
        await feedback.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await feedback.process_frame(TextFrame("任务已受理。"), FrameDirection.DOWNSTREAM)
        await feedback.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        assert [f.text for f in frames if isinstance(f, TextFrame)] == ["任务已受理。"]
        assert journal.activities[-1] == ("turn-1", "idle")
        await feedback.cleanup()
    asyncio.run(run())


def test_interruption_and_cleanup_cancel_pending_audio():
    async def run():
        feedback, journal, frames = setup_feedback()
        await feedback.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await feedback.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        journal.turn_id = None
        await asyncio.sleep(.02)
        assert not any(isinstance(f, TextFrame) for f in frames)
        journal.turn_id = "turn-2"
        await feedback.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        await feedback.cleanup()
        await asyncio.sleep(.02)
        assert not any(isinstance(f, TextFrame) for f in frames)
    asyncio.run(run())


def test_ack_audio_ending_does_not_finalize_the_unfinished_answer():
    async def run():
        journal = VoiceTurnJournal("vs-feedback")
        journal._turn = _TurnState(turn_id="turn-1")
        journal._touch_turn = lambda: None
        journal._emit = AsyncMock()
        journal.assistant_completed = AsyncMock()
        await journal.assistant_activity("thinking", turn_id="turn-1")
        await journal.playback_ended()
        journal.assistant_completed.assert_not_called()
        assert not journal._turn.finalized
        await journal.assistant_activity("idle", turn_id="turn-1")
        await journal.playback_ended()
        journal.assistant_completed.assert_awaited_once()
        await journal.assistant_activity("thinking", turn_id="old-turn")
        assert not journal._turn.foreground_busy
    asyncio.run(run())


def test_acknowledgement_is_not_a_generated_answer_draft():
    async def run():
        journal = VoiceTurnJournal("vs-feedback")
        journal._turn = _TurnState(turn_id="turn-1")
        journal._touch_turn = lambda: None
        tap = VoiceJournalProcessor(journal)
        ack = TextFrame("嗯，我看看。")
        ack.metadata["voice_acknowledgement"] = True
        await tap._observe_assistant(ack, FrameDirection.DOWNSTREAM)
        assert journal._turn.assistant_text == ""
        await tap._observe_assistant(TextFrame("真实答案。"), FrameDirection.DOWNSTREAM)
        assert journal._turn.assistant_text == "真实答案。"
        await tap.cleanup()
    asyncio.run(run())


def test_ack_context_end_remains_nonterminal_even_if_the_model_has_already_finished():
    async def run():
        journal = VoiceTurnJournal("vs-feedback")
        journal._turn = _TurnState(turn_id="turn-1", llm_closed=True)
        journal.assistant_completed = AsyncMock()
        finished = AsyncMock()
        relay = VoiceLedgerRelay(journal=journal, on_playback_ended=finished)
        await relay._release(VoiceLedgerFrame(kind="turn-end", token="ack-end", turn_id="turn-1", turn_final=False))
        journal.assistant_completed.assert_not_called()
        finished.assert_not_called()
        await relay.cleanup()
    asyncio.run(run())
