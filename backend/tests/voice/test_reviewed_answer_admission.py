import asyncio
import struct
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pipecat.frames.frames import TranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.turns.user_stop.base_user_turn_stop_strategy import UserTurnStoppedParams

from app.voice.embedded_reviewed_turn import ReviewedUserAggregator
from app.voice.journal import VoiceJournalProcessor
from app.voice.embedded_dashscope_stt import DashScopeSTTService
from pipecat.services.stt_service import STTService
from pipecat.frames.frames import VADUserStartedSpeakingFrame, UserStoppedSpeakingFrame, InterruptionFrame, EndFrame, CancelFrame, LLMRunFrame, LLMMessagesAppendFrame
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
from pipecat.processors.frame_processor import FrameDirection


class Audio:
    current_audio_turn = None
    audio_turn_started = None
    def begin(self, seq):
        self.current_audio_turn = seq
        self.future = asyncio.get_running_loop().create_future()
        self.audio_turn_started(seq)
    def seal_audio_turn(self):
        return self.current_audio_turn, self.future


def harness(wait=.2):
    audio = Audio()
    context = LLMContext(messages=[])
    journal = SimpleNamespace(user_final=AsyncMock())
    aggregator = ReviewedUserAggregator(context, stt=audio, journal=journal, review_wait_secs=wait)
    aggregator.broadcast_frame = AsyncMock()
    aggregator._user_idle_controller.process_frame = AsyncMock()
    aggregator._call_event_handler = AsyncMock()
    snapshots = []
    async def push():snapshots.append([dict(m) for m in context.messages])
    aggregator.push_context_frame = AsyncMock(side_effect=push)
    return audio, context, journal, aggregator, snapshots


async def text(aggregator, seq, value):
    await aggregator._handle_transcription(TranscriptionFrame(value,'u','now',result={'review_pending':True,'audio_turn':seq}))


async def stop(aggregator):
    await aggregator._on_user_turn_stopped(None,None,UserTurnStoppedParams(enable_user_speaking_frames=True))


def test_review_replaces_text_once_before_one_answer_and_never_early_inference():
    async def run():
        audio, context, journal, agg, snapshots = harness()
        audio.begin(1)
        await text(agg,1,'操作更多')
        await agg._on_user_turn_inference_triggered(None,None,object())
        assert not context.messages and not snapshots
        await stop(agg);await stop(agg)
        assert not snapshots
        audio.future.set_result({'sequence':1,'status':'ok','text':'稍作停顿'})
        await agg.drain_answers()
        assert context.messages == [{'role':'user','content':'稍作停顿'}]
        assert len(snapshots)==1
        journal.user_final.assert_awaited_once()
        assert journal.user_final.await_args.args == ('稍作停顿',)
        assert journal.user_final.await_args.kwargs['new_turn'] is True
    asyncio.run(run())


def test_timeout_falls_back_once_and_late_review_cannot_rerun_or_overwrite():
    async def run():
        audio, context, journal, agg, snapshots = harness(wait=.05)
        audio.begin(1);await text(agg,1,'原实时结果');await stop(agg)
        await agg.drain_answers()
        assert context.messages[-1]['content']=='原实时结果' and len(snapshots)==1
        assert not audio.future.cancelled()
        audio.future.set_result({'sequence':1,'status':'ok','text':'迟到复核'})
        await asyncio.sleep(0)
        assert context.messages[-1]['content']=='原实时结果' and len(snapshots)==1
    asyncio.run(run())


def test_new_onset_discards_previous_pending_answer_and_old_review_does_not_cross_turn():
    async def run():
        audio, context, journal, agg, snapshots = harness()
        audio.begin(1);old=audio.future
        await text(agg,1,'旧问题');await stop(agg)
        await asyncio.sleep(0)
        audio.begin(2)
        old.set_result({'sequence':1,'status':'ok','text':'旧复核'})
        await text(agg,2,'新问题');await stop(agg)
        audio.future.set_result({'sequence':2,'status':'ok','text':'新复核'})
        await agg.drain_answers()
        assert context.messages==[{'role':'user','content':'新复核'}]
        assert len(snapshots)==1 and journal.user_final.await_count==1
        assert agg.admissions[0]['sequence']==2
    asyncio.run(run())


def test_two_completed_turns_write_separate_messages_and_one_answer_each():
    async def run():
        audio, context, journal, agg, snapshots = harness()
        for seq in [1,2]:
            audio.begin(seq);await text(agg,seq,f'原文{seq}');await stop(agg)
            audio.future.set_result({'sequence':seq,'status':'ok','text':f'复核{seq}'})
            await agg.drain_answers()
        assert [m['content'] for m in context.messages]==['复核1','复核2']
        assert len(snapshots)==2
        assert len({c.kwargs['source_id'] for c in journal.user_final.await_args_list})==2
    asyncio.run(run())


def test_failed_empty_or_mismatched_review_uses_own_realtime_fallback():
    async def run():
        for result in [{'sequence':1,'status':'error'},{'sequence':1,'status':'ok','text':''},{'sequence':2,'status':'ok','text':'别轮文本'}]:
            audio, context, journal, agg, snapshots = harness()
            audio.begin(1);await text(agg,1,'自己的实时文本');await stop(agg)
            audio.future.set_result(result);await agg.drain_answers()
            assert context.messages[-1]['content']=='自己的实时文本' and len(snapshots)==1
    asyncio.run(run())


def test_raw_finals_are_provisional_to_journal_and_do_not_accept_a_turn():
    async def run():
        journal=SimpleNamespace(user_interim=AsyncMock(),user_final=AsyncMock())
        tap=VoiceJournalProcessor(journal,role='user')
        frame=TranscriptionFrame('实时字幕','u','now',result={'review_pending':True})
        await tap._observe_user(frame,None)
        journal.user_final.assert_not_awaited()
        journal.user_interim.assert_awaited_once_with('实时字幕')
        frame.result['superseded']=True
        await tap._observe_user(frame,None)
        assert journal.user_interim.await_count==1
    asyncio.run(run())


def test_unconfirmed_energy_pulse_does_not_cancel_pending_answer_and_late_stop_cannot_seal_next_turn(monkeypatch):
    async def run():
        monkeypatch.setenv('DASHSCOPE_ASR_REVIEW_FOR_ANSWERS','1')
        class Review:
            def submit(self,turn):
                self.future=asyncio.get_running_loop().create_future()
                return self.future
        review=Review()
        stt=DashScopeSTTService(api_key='test',turn_reviewer=review)
        agg=ReviewedUserAggregator(LLMContext(messages=[]),stt=stt)
        agg.broadcast_frame=AsyncMock();agg._user_idle_controller.process_frame=AsyncMock()
        agg.push_context_frame=AsyncMock();agg._call_event_handler=AsyncMock()
        stt._upstream_alive=lambda:True
        stt._ws=SimpleNamespace(send=AsyncMock())
        monkeypatch.setattr(STTService,'process_frame',AsyncMock())
        await stt.process_frame(VADUserStartedSpeakingFrame(),FrameDirection.DOWNSTREAM)
        stt._turn_audio.append(b'\0\0'*320)
        await text(agg,1,'第一轮');await stop(agg)
        original=review.future
        # Energy alone starts a bounded recording, not irreversible cancellation.
        async for _ in stt.run_stt(struct.pack('<h',2000)*320):pass
        assert stt.current_audio_turn==2 and agg._audio_sequence==1
        await stt.process_frame(UserStoppedSpeakingFrame(),FrameDirection.UPSTREAM)
        assert stt.current_audio_turn==2,'Late queued old stop must not cut next-turn PCM'
        original.set_result({'sequence':1,'status':'ok','text':'第一轮复核'})
        await agg.drain_answers()
        agg.push_context_frame.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize('frame',[InterruptionFrame(),EndFrame(),CancelFrame(),LLMMessagesAppendFrame(messages=[{'role':'user','content':'改成文字输入'}])])
def test_explicit_interrupt_cancels_pending_answer(monkeypatch,frame):
    async def run():
        audio, context, journal, agg, snapshots=harness()
        audio.begin(1);await text(agg,1,'旧问题');await stop(agg)
        monkeypatch.setattr(LLMUserAggregator,'process_frame',AsyncMock())
        await agg.process_frame(frame,FrameDirection.DOWNSTREAM)
        audio.future.set_result({'sequence':1,'status':'ok','text':'迟到文本'})
        await agg.drain_answers()
        assert not snapshots and not context.messages
    asyncio.run(run())


def test_old_llm_retry_cannot_generate_while_new_turn_is_waiting():
    async def run():
        audio, context, journal, agg, snapshots=harness()
        audio.begin(1);await text(agg,1,'当前问题');await stop(agg)
        await agg.process_frame(LLMRunFrame(),FrameDirection.UPSTREAM)
        assert not snapshots
        audio.future.set_result({'sequence':1,'status':'ok','text':'当前复核'})
        await agg.drain_answers()
        assert len(snapshots)==1
    asyncio.run(run())


def test_new_onset_during_journal_sql_finishes_admission_but_fences_old_answer():
    async def run():
        audio, context, journal, agg, snapshots=harness()
        entered,release=asyncio.Event(),asyncio.Event()
        accepted=[]
        async def persist(value,**kwargs):
            if value=='旧复核':
                entered.set();await release.wait()
            accepted.append(value)
        journal.user_final=AsyncMock(side_effect=persist)
        audio.begin(1);await text(agg,1,'旧问题');await stop(agg)
        audio.future.set_result({'sequence':1,'status':'ok','text':'旧复核'})
        await entered.wait()
        audio.begin(2);await text(agg,2,'新问题');await stop(agg)
        audio.future.set_result({'sequence':2,'status':'ok','text':'新复核'})
        release.set();await agg.drain_answers()
        assert accepted==['旧复核','新复核']
        assert context.messages==[{'role':'user','content':'新复核'}]
        assert len(snapshots)==1
    asyncio.run(run())


def test_weak_review_can_arrive_after_normal_budget_without_using_bad_realtime():
    async def run():
        audio,context,journal,agg,snapshots=harness(wait=.05)
        audio.review_weak_answer_wait_secs=.2
        audio.begin(1)
        quality=asyncio.get_running_loop().create_future();quality.set_result({'canonicalWeakAudio':True})
        audio.future.review_quality=quality
        await text(agg,1,'水之间流动');await stop(agg)
        await asyncio.sleep(.08)
        assert not snapshots
        audio.future.set_result({'sequence':1,'status':'ok','text':'请从一数到二十'})
        await agg.drain_answers()
        assert context.messages==[{'role':'user','content':'请从一数到二十'}] and len(snapshots)==1
    asyncio.run(run())


@pytest.mark.parametrize('weak',[True,False])
def test_failed_weak_review_notifies_without_executing_bad_draft_but_normal_fallback_remains(weak):
    async def run():
        audio,context,journal,agg,snapshots=harness(wait=.05)
        audio.review_weak_answer_wait_secs=.05
        journal.processor_error=AsyncMock()
        audio.begin(1)
        quality=asyncio.get_running_loop().create_future();quality.set_result({'canonicalWeakAudio':weak})
        audio.future.review_quality=quality
        await text(agg,1,'水之间流动');await stop(agg);await agg.drain_answers()
        if weak:
            assert not snapshots and not context.messages
            journal.user_final.assert_not_awaited()
            journal.processor_error.assert_awaited_once()
            assert journal.processor_error.await_args.kwargs['degraded'] is False
            assert agg.admissions[0]['source']=='review_rejected'
        else:
            assert len(snapshots)==1 and context.messages[-1]['content']=='水之间流动'
            journal.processor_error.assert_not_awaited()
        audio.future.set_result({'sequence':1,'status':'ok','text':'迟到正确结果'})
        await asyncio.sleep(0)
        assert len(snapshots)==(0 if weak else 1)
        if weak:
            audio.begin(2)
            await text(agg,2,'新回合');await stop(agg)
            audio.future.set_result({'sequence':2,'status':'ok','text':'新回合正确复核'})
            await agg.drain_answers()
            assert context.messages==[{'role':'user','content':'新回合正确复核'}]
            assert len(snapshots)==1
    asyncio.run(run())


def test_unknown_review_quality_from_capacity_skip_does_not_execute_provisional_text():
    async def run():
        audio,context,journal,agg,snapshots=harness()
        journal.processor_error=AsyncMock()
        audio.begin(1)
        quality=asyncio.get_running_loop().create_future();quality.set_result({'status':'unavailable'})
        audio.future.review_quality=quality
        audio.future.set_result({'sequence':1,'status':'capacity'})
        await text(agg,1,'不可靠口令');await stop(agg);await agg.drain_answers()
        assert not snapshots and not context.messages
        journal.user_final.assert_not_awaited();journal.processor_error.assert_awaited_once()
    asyncio.run(run())
