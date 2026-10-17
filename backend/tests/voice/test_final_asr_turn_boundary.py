import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pipecat.frames.frames import BotStartedSpeakingFrame, TranscriptionFrame, VADUserStoppedSpeakingFrame
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
from pipecat.turns.user_stop.base_user_turn_stop_strategy import BaseUserTurnStopStrategy, UserTurnStoppedParams
from pipecat.turns.user_turn_controller import UserTurnController
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from app.voice.embedded_turn_strategy import AdaptiveUserTurnStartStrategy


def test_final_after_vad_stop_does_not_reopen_physical_speech_and_block_inference():
    async def exercise():
        start=AdaptiveUserTurnStartStrategy()
        controller=UserTurnController(user_turn_strategies=UserTurnStrategies(
            start=[start],stop=[BaseUserTurnStopStrategy()]))
        start.add_event_handler('on_user_turn_started',controller._on_user_turn_started)
        async def broadcast(frame_type):
            await controller.process_frame(frame_type())
        aggregator=SimpleNamespace(broadcast_frame=broadcast,broadcast_interruption=AsyncMock(),
            _user_idle_controller=SimpleNamespace(process_frame=AsyncMock()),_call_event_handler=AsyncMock())
        async def started(owner,strategy,params):
            await LLMUserAggregator._on_user_turn_started(aggregator,owner,strategy,params)
        controller.add_event_handler('on_user_turn_started',started)
        stopped=[]
        async def ended(*args):stopped.append(True)
        controller.add_event_handler('on_user_turn_stopped',ended)
        await controller._handle_vad_user_stopped_speaking(VADUserStoppedSpeakingFrame())
        await start.process_frame(TranscriptionFrame('后台生成交互表格','user','now',finalized=True))
        assert controller._user_turn
        assert not controller._user_speaking, 'Late final must not invent another physical speech onset'
        await controller._trigger_user_turn_stop(None,UserTurnStoppedParams(enable_user_speaking_frames=True))
        assert stopped and not controller._user_turn
        assert aggregator.broadcast_interruption.await_count==1
    asyncio.run(exercise())


def test_segmented_barge_in_stops_playback_only_once_and_ignores_superseded_commit():
    async def exercise():
        fast=AsyncMock()
        start=AdaptiveUserTurnStartStrategy(interrupt_callback=fast)
        start.trigger_user_turn_started=AsyncMock()
        await start.process_frame(BotStartedSpeakingFrame())
        await start.process_frame(TranscriptionFrame('先回答收到','u','now',finalized=True))
        await start.process_frame(TranscriptionFrame('后台任务继续','u','now',finalized=True))
        assert fast.await_count==1
        count=start.trigger_user_turn_started.await_count
        await start.process_frame(TranscriptionFrame('迟到片段','u','now',result={'superseded':True},finalized=False))
        assert start.trigger_user_turn_started.await_count==count
    asyncio.run(exercise())
