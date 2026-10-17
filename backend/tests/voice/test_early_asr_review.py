import asyncio
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pipecat.frames.frames import VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.frames.frames import BotStartedSpeakingFrame, TranscriptionFrame
from app.voice.embedded_turn_strategy import AdaptiveUserTurnStartStrategy
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.stt_service import STTService

from app.voice.embedded_asr_review import TurnAudioBuffer
from app.voice.embedded_dashscope_stt import DashScopeSTTService
from test_reviewed_answer_admission import text, stop
from app.voice.embedded_reviewed_turn import ReviewedUserAggregator
from pipecat.processors.aggregators.llm_context import LLMContext


class Review:
    def __init__(self): self.calls = []
    def submit(self, turn):
        future = asyncio.get_running_loop().create_future()
        self.calls.append((turn, future))
        return future


def setup(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_ASR_REVIEW_FOR_ANSWERS', '1')
    monkeypatch.setenv('DASHSCOPE_ASR_REVIEW_ON_VAD_STOP', '1')
    monkeypatch.setattr(STTService, 'process_frame', AsyncMock())
    reviewer = Review()
    stt = DashScopeSTTService(api_key='test', turn_reviewer=reviewer)
    stt._upstream_alive = lambda: True
    stt._finish_segment = AsyncMock()
    stt._ws = SimpleNamespace(send=AsyncMock())
    journal = SimpleNamespace(user_final=AsyncMock())
    agg = ReviewedUserAggregator(LLMContext(messages=[]), stt=stt, journal=journal)
    agg.broadcast_frame = AsyncMock()
    agg._user_idle_controller.process_frame = AsyncMock()
    agg._call_event_handler = AsyncMock()
    agg.push_context_frame = AsyncMock()
    return stt, reviewer, agg, journal


async def vad(stt, cls):
    await stt.process_frame(cls(), FrameDirection.UPSTREAM)


def test_snapshot_does_not_close_or_drop_quiet_tail():
    buffer = TurnAudioBuffer(); buffer.begin(); buffer.append(b'ab')
    prefix = buffer.snapshot(revision=3)
    buffer.append(b'cd')
    assert prefix.pcm == b'ab' and prefix.revision == 3
    assert buffer.active and buffer.finish().pcm == b'abcd'


def test_ready_early_review_waits_for_smart_turn_and_reuses_once(monkeypatch):
    async def run():
        stt, reviewer, agg, journal = setup(monkeypatch)
        await vad(stt, VADUserStartedSpeakingFrame)
        stt._turn_audio.append(b'\1\0'*320)
        await text(agg, 1, '实时文本')
        await vad(stt, VADUserStoppedSpeakingFrame)
        turn, future = reviewer.calls[0]
        future.set_result({'sequence':1,'revision':turn.revision,'status':'ok','text':'完整复核'})
        async for _ in stt.run_stt(b'\0\0'*320): pass
        await asyncio.sleep(0)
        assert stt.current_audio_turn == 1
        journal.user_final.assert_not_awaited(); agg.push_context_frame.assert_not_awaited()
        await stop(agg); await stop(agg); await agg.drain_answers()
        assert len(reviewer.calls) == 1
        journal.user_final.assert_awaited_once_with('完整复核', source_id=journal.user_final.await_args.kwargs['source_id'], new_turn=True)
        agg.push_context_frame.assert_awaited_once()
        assert stt.review_events[-1]['fullAudioMs'] == 40
    asyncio.run(run())


def test_same_turn_resumption_discards_completed_old_result(monkeypatch):
    async def run():
        stt, reviewer, agg, journal = setup(monkeypatch)
        await vad(stt, VADUserStartedSpeakingFrame)
        stt._turn_audio.append(b'\1\0'*320)
        await vad(stt, VADUserStoppedSpeakingFrame)
        reviewer.calls[0][1].set_result({'sequence':1,'status':'ok','text':'旧半句'})
        await vad(stt, VADUserStartedSpeakingFrame)
        stt._turn_audio.append(b'\2\0'*320)
        assert stt.current_audio_turn == 1
        await vad(stt, VADUserStoppedSpeakingFrame)
        turn, future = reviewer.calls[1]
        assert turn.pcm == b'\1\0'*320 + b'\2\0'*320 and turn.revision == 1
        future.set_result({'sequence':1,'status':'ok','text':'完整新句'})
        await stop(agg); await agg.drain_answers()
        assert agg._context.messages[-1]['content'] == '完整新句'
        agg.push_context_frame.assert_awaited_once(); journal.user_final.assert_awaited_once()
    asyncio.run(run())


def test_pcm_resume_before_vad_cancels_pending_prefix_and_seal_reviews_full_turn(monkeypatch):
    async def run():
        stt, reviewer, agg, journal = setup(monkeypatch)
        await vad(stt, VADUserStartedSpeakingFrame)
        prefix = b'\1\0'*320; stt._turn_audio.append(prefix)
        await vad(stt, VADUserStoppedSpeakingFrame)
        resumed = struct.pack('<h', 100)*320  # weaker than speech RMS, above signal RMS
        async for _ in stt.run_stt(resumed): pass
        assert reviewer.calls[0][1].cancelled()
        await stop(agg)
        turn, future = reviewer.calls[1]
        assert turn.pcm == prefix + resumed
        future.set_result({'sequence':1,'status':'ok','text':'续说后全文'})
        await agg.drain_answers()
        agg.push_context_frame.assert_awaited_once()
    asyncio.run(run())


def test_early_review_disabled_by_default(monkeypatch):
    monkeypatch.delenv('DASHSCOPE_ASR_REVIEW_ON_VAD_STOP', raising=False)
    monkeypatch.setenv('DASHSCOPE_ASR_REVIEW_FOR_ANSWERS','1')
    stt = DashScopeSTTService(api_key='test', turn_reviewer=Review())
    assert not stt.review_on_vad_stop


def test_uncancellable_late_result_cannot_replace_resumed_turn_and_fast_interrupt_proceeds(monkeypatch):
    async def run():
        stt, reviewer, agg, journal = setup(monkeypatch)
        class LateDelivery(asyncio.Future):
            def cancel(self, *args, **kwargs): return False
        old = LateDelivery()
        original = reviewer.submit
        def submit(turn):
            if not reviewer.calls:
                reviewer.calls.append((turn, old)); return old
            return original(turn)
        reviewer.submit = submit
        await vad(stt, VADUserStartedSpeakingFrame)
        stt._turn_audio.append(b'\1\0'*320)
        await vad(stt, VADUserStoppedSpeakingFrame)
        interrupt = AsyncMock()
        strategy = AdaptiveUserTurnStartStrategy(interrupt_callback=interrupt)
        strategy.trigger_user_turn_started = AsyncMock()
        await strategy.process_frame(BotStartedSpeakingFrame())
        await asyncio.wait_for(strategy.process_frame(TranscriptionFrame('停一下','u','now',finalized=True)), .1)
        interrupt.assert_awaited_once()
        assert not old.done()
        await vad(stt, VADUserStartedSpeakingFrame)
        stt._turn_audio.append(b'\2\0'*320)
        await vad(stt, VADUserStoppedSpeakingFrame)
        await stop(agg)
        reviewer.calls[1][1].set_result({'sequence':1,'status':'ok','text':'续说全文'})
        await agg.drain_answers()
        old.set_result({'sequence':1,'status':'ok','text':'迟到半句'})
        await asyncio.sleep(0)
        assert agg._context.messages == [{'role':'user','content':'续说全文'}]
        agg.push_context_frame.assert_awaited_once(); journal.user_final.assert_awaited_once()
    asyncio.run(run())


def test_weak_grace_collects_real_tail_but_normal_speech_keeps_original_prefix(monkeypatch):
    async def run():
        for amplitude in [40,4000]:
            stt, reviewer, agg, journal=setup(monkeypatch)
            stt._review_canonical_weak=True;stt.review_grace_secs=.04
            await vad(stt,VADUserStartedSpeakingFrame)
            first=struct.pack('<h',amplitude)*320
            stt._turn_audio.append(first)
            await vad(stt,VADUserStoppedSpeakingFrame)
            tail=b'\1\0'*320;stt._turn_audio.append(tail)
            await asyncio.sleep(.07)
            assert len(reviewer.calls)==1
            assert reviewer.calls[0][0].pcm==first+(tail if amplitude==40 else b'')
            assert stt.current_audio_turn==1 and not agg.admissions
            stt._invalidate_early_review('test complete')
    asyncio.run(run())


def test_resumption_or_seal_cancels_grace_without_late_cross_turn_snapshot(monkeypatch):
    async def run():
        for resumed in [True,False]:
            stt, reviewer, agg, journal=setup(monkeypatch)
            stt._review_canonical_weak=True;stt.review_grace_secs=.04
            await vad(stt,VADUserStartedSpeakingFrame)
            stt._turn_audio.append(b'\1\0'*320)
            await vad(stt,VADUserStoppedSpeakingFrame)
            if resumed:
                await vad(stt,VADUserStartedSpeakingFrame)
                await asyncio.sleep(.07)
                assert not reviewer.calls and stt.current_audio_turn==1
            else:
                sealed=stt.seal_audio_turn()
                await vad(stt,VADUserStartedSpeakingFrame)
                stt._turn_audio.append(b'\2\0'*320)
                await asyncio.sleep(.07)
                assert len(reviewer.calls)==1 and reviewer.calls[0][0].pcm==b'\1\0'*320
                assert sealed[0]==1 and stt.current_audio_turn==2
                reviewer.calls[0][1].cancel()
    asyncio.run(run())


def test_fast_interrupt_does_not_wait_for_weak_tail_grace(monkeypatch):
    async def run():
        stt,reviewer,agg,journal=setup(monkeypatch)
        stt._review_canonical_weak=True;stt.review_grace_secs=.4
        await vad(stt,VADUserStartedSpeakingFrame)
        stt._turn_audio.append(b'\1\0'*320)
        await vad(stt,VADUserStoppedSpeakingFrame)
        interrupt=AsyncMock()
        strategy=AdaptiveUserTurnStartStrategy(interrupt_callback=interrupt)
        strategy.trigger_user_turn_started=AsyncMock()
        await strategy.process_frame(BotStartedSpeakingFrame())
        await asyncio.wait_for(strategy.process_frame(TranscriptionFrame('停一下','u','now',finalized=True)),.1)
        interrupt.assert_awaited_once()
        assert not reviewer.calls
        delay=stt._early_review_delay
        stt._invalidate_early_review('test complete')
        await asyncio.gather(delay,return_exceptions=True)
    asyncio.run(run())
