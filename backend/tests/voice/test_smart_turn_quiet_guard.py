import asyncio
from unittest.mock import AsyncMock
import struct
from types import SimpleNamespace
from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
from pipecat.frames.frames import InputAudioRawFrame,VADUserStartedSpeakingFrame,VADUserStoppedSpeakingFrame,TranscriptionFrame
from app.voice.embedded_turn_stop import GuardedSmartTurnStopStrategy

class Tasks:
    def create_task(self,coro,name):return asyncio.create_task(coro)
    async def cancel_task(self,task):
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass

class Analyzer:
    update_vad_start_secs=lambda *args:None
    clear=lambda *args:None
    cleanup=AsyncMock()
    append_audio=lambda *args:EndOfTurnState.INCOMPLETE
    async def analyze_end_of_turn(self):return EndOfTurnState.INCOMPLETE,None

def strategy():
    s=GuardedSmartTurnStopStrategy(turn_analyzer=Analyzer(),min_quiet_secs=.04,
        continuation_quiet_secs=.04,max_quiet_secs=.12)
    s._task_manager=Tasks()
    s._turn_complete=True
    s._model_complete=True
    s._vad_user_speaking=False
    s._text='已有转写'
    s._transcript_finalized=True
    s._physical_turn=True
    return s

def test_complete_verdict_waits_for_quiet_without_blocking_input():
    async def run():
        s=strategy();events=[]
        s.add_event_handler('on_user_turn_stopped',lambda *args:events.append('stopped'))
        await s.trigger_user_turn_stopped()
        assert not events
        await asyncio.sleep(.06)
        assert events==['stopped']
        await s.cleanup()
    asyncio.run(run())


def test_weak_pcm_continuation_without_vad_start_restarts_quiet_confirmation():
    async def run():
        s=strategy();events=[]
        s.add_event_handler('on_user_turn_stopped',lambda *args:events.append('stopped'))
        await s.process_frame(VADUserStartedSpeakingFrame())
        await s.process_frame(TranscriptionFrame('请从一数到二十','u','now',finalized=True))
        await s.process_frame(VADUserStoppedSpeakingFrame(stop_secs=.01))
        s._turn_complete=s._model_complete=True
        await s.trigger_user_turn_stopped()
        await asyncio.sleep(.025)
        await s.process_frame(InputAudioRawFrame(struct.pack('<h',55)*320,16000,1))
        await asyncio.sleep(.035)
        assert not events,'weak continuation must revoke the old complete verdict'
        assert not s._model_complete
        await asyncio.sleep(.11)
        assert events==['stopped'] and s.end_events[-1]['reason']=='inactivity-fallback'
        await s.cleanup()
    asyncio.run(run())


def test_incomplete_model_has_bounded_quiet_fallback_and_fires_once():
    async def run():
        s=strategy();events=[]
        s.add_event_handler('on_user_turn_stopped',lambda *args:events.append('stopped'))
        await s.process_frame(VADUserStartedSpeakingFrame())
        await s.process_frame(TranscriptionFrame('这句话已经说完','u','now',finalized=True))
        await s.process_frame(VADUserStoppedSpeakingFrame(stop_secs=.01))
        assert not s._turn_complete and not s._model_complete
        await asyncio.sleep(.15)
        assert events==['stopped']
        await s.process_frame(TranscriptionFrame('迟到的文本','u','now',finalized=True))
        await s.trigger_user_turn_stopped();await asyncio.sleep(.15)
        assert events==['stopped']
        await s.cleanup()
    asyncio.run(run())


def test_fallback_never_ends_while_weak_pcm_keeps_arriving():
    async def run():
        s=strategy();events=[]
        s.add_event_handler('on_user_turn_stopped',lambda *args:events.append('stopped'))
        await s.process_frame(VADUserStartedSpeakingFrame())
        await s.process_frame(TranscriptionFrame('我还在说','u','now',finalized=True))
        await s.process_frame(VADUserStoppedSpeakingFrame(stop_secs=.01))
        for _ in range(6):
            await asyncio.sleep(.03)
            await s.process_frame(InputAudioRawFrame(struct.pack('<h',55)*320,16000,1))
            assert not events
        await asyncio.sleep(.15);assert events==['stopped']
        await s.cleanup()
    asyncio.run(run())


def test_final_without_a_physical_speech_turn_cannot_start_fallback():
    async def run():
        s=strategy();s._model_complete=False;s._physical_turn=False;events=[]
        s.add_event_handler('on_user_turn_stopped',lambda *args:events.append('stopped'))
        await s.process_frame(TranscriptionFrame('迟到的旧回合文本','u','now',finalized=True))
        await asyncio.sleep(.16);assert not events
        await s.cleanup()
    asyncio.run(run())

def test_resumed_speech_cancels_a_pending_smart_turn_release():
    async def run():
        s=strategy();events=[]
        s.add_event_handler('on_user_turn_stopped',lambda *args:events.append('stopped'))
        await s.trigger_user_turn_stopped()
        await s.process_frame(VADUserStartedSpeakingFrame())
        await asyncio.sleep(.06)
        assert not events
        assert s._guard_task is None
        await s.cleanup()
    asyncio.run(run())

def test_asr_final_cannot_supply_a_missing_complete_verdict():
    async def run():
        s=strategy();s._turn_complete=False;s._model_complete=False;s._physical_turn=False;events=[]
        s.add_event_handler('on_user_turn_stopped',lambda *args:events.append('stopped'))
        await s.process_frame(TranscriptionFrame('还没有说完','u','now',finalized=True));await asyncio.sleep(.06)
        assert not events
        await s.cleanup()
    asyncio.run(run())


def test_model_result_finishing_after_weak_resume_is_stale():
    async def run():
        s=strategy();events=[];entered=asyncio.Event();release=asyncio.Event()
        s.add_event_handler('on_user_turn_stopped',lambda *args:events.append('stopped'))
        s.push_frame=AsyncMock()
        async def predict():
            entered.set();await release.wait()
            return EndOfTurnState.COMPLETE,SimpleNamespace(is_complete=True,probability=.99,e2e_processing_time_ms=1)
        s._turn_analyzer.analyze_end_of_turn=predict
        await s.process_frame(VADUserStartedSpeakingFrame())
        await s.process_frame(TranscriptionFrame('完整回合','u','now',finalized=True))
        stop=asyncio.create_task(s.process_frame(VADUserStoppedSpeakingFrame(stop_secs=.01)))
        await entered.wait()
        await s.process_frame(InputAudioRawFrame(struct.pack('<h',55)*320,16000,1))
        release.set();await stop
        assert not s._model_complete and not s._turn_complete
        await asyncio.sleep(.06);assert not events
        await s.cleanup()
        await asyncio.sleep(.15);assert not events,'cleanup must cancel the fallback timer'
    asyncio.run(run())


def test_fast_interrupt_proceeds_while_incomplete_quiet_guard_is_pending():
    from app.voice.embedded_turn_strategy import AdaptiveUserTurnStartStrategy
    from pipecat.frames.frames import BotStartedSpeakingFrame
    async def run():
        s=strategy();events=[]
        s.add_event_handler('on_user_turn_stopped',lambda *args:events.append('stopped'))
        await s.process_frame(VADUserStartedSpeakingFrame())
        await s.process_frame(TranscriptionFrame('旧半句','u','now',finalized=True))
        await s.process_frame(VADUserStoppedSpeakingFrame(stop_secs=.01))
        callback=AsyncMock();fast=AdaptiveUserTurnStartStrategy(interrupt_callback=callback)
        fast.trigger_user_turn_started=AsyncMock()
        await fast.process_frame(BotStartedSpeakingFrame())
        await asyncio.wait_for(fast.process_frame(TranscriptionFrame('停一下','u','now',finalized=True)),.05)
        callback.assert_awaited_once();assert not events
        await s.cleanup()
    asyncio.run(run())
