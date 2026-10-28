import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    TextFrame, VADUserStartedSpeakingFrame, InterruptionFrame, LLMMessagesAppendFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregator
from pipecat.services.openai.llm import OpenAILLMService
from app.voice.embedded_bot import TutorAgent
from app.voice.embedded_result_bridge import VoiceResultDeliveryProcessor
from app.voice.speech_floor import VoiceSpeechFloor, VoiceInputFloorProcessor


def test_transport_gap_and_llm_end_do_not_release_audio_ownership():
    floor = VoiceSpeechFloor()
    floor.observe(LLMFullResponseStartFrame())
    floor.observe(TextFrame("回答的第一句。"))
    floor.observe(LLMFullResponseEndFrame())
    floor.observe(BotStoppedSpeakingFrame())
    assert floor.reserve("component") is None
    floor.playback_ended()
    assert floor.reserve("component") is not None


@pytest.mark.parametrize("stage", ["claim", "enqueued"])
@pytest.mark.parametrize("activity", [VADUserStartedSpeakingFrame, LLMFullResponseStartFrame])
def test_user_or_model_activity_during_durable_io_releases_claim(stage, activity):
    async def run():
        entered, resume, released = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Port:
            results = []
            async def ready_results(self, **kwargs): return self.results
            async def acknowledge_result(self, task_id, *, delivery_state):
                if delivery_state == "released":
                    released.set()
                    return {}
                if stage == "claim":
                    entered.set()
                    await resume.wait()
                return {"created": True, "speech_id": "speech-1"}
            async def update_speech_delivery(self, speech_id, state):
                if stage == "enqueued" and state == "enqueued":
                    entered.set()
                    await resume.wait()
        port = Port()
        agent = type("Agent", (), {"tool_call_active": False, "queue_frame": AsyncMock()})()
        processor = VoiceResultDeliveryProcessor(agent=agent, result_port=port, poll_interval=.25)
        processor.push_frame = AsyncMock()
        await processor.start()
        port.results = [{"task_id": "task-1", "status": "ready", "summary": "done"}]
        await asyncio.wait_for(entered.wait(), 2)
        await processor.process_frame(activity(), FrameDirection.DOWNSTREAM)
        resume.set()
        await asyncio.wait_for(released.wait(), 2)
        agent.queue_frame.assert_not_awaited()
        assert processor.floor.lease is None
        await processor.stop()
    asyncio.run(run())


def test_control_is_revalidated_at_input_consumer_and_deduplicated():
    async def run():
        floor = VoiceSpeechFloor()
        processor = VoiceInputFloorProcessor(floor)
        processor.push_frame = AsyncMock()
        lease = floor.reserve("control:1")
        future = asyncio.get_running_loop().create_future()
        frame = LLMMessagesAppendFrame(messages=[{"role": "user", "content": "讲解"}], run_llm=True)
        frame.metadata.update(voice_admission=future, voice_speech_lease=lease, voice_request_id="1")
        floor.observe(VADUserStartedSpeakingFrame())
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
        assert await future is False
        processor.push_frame.assert_not_awaited()
        scheduler = VoiceResultDeliveryProcessor(agent=None, result_port=None, control_dispatch=AsyncMock())
        assert scheduler.enqueue_control("1", "讲解")
        assert scheduler.enqueue_control("1", "讲解")
        assert len(scheduler._controls) == 1
        await scheduler.stop()
        assert not scheduler.enqueue_control("2", "讲解")
    asyncio.run(run())


def test_shutdown_during_claim_waits_for_write_then_releases_it():
    async def run():
        entered, resume, released = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Port:
            results = []
            async def ready_results(self, **kwargs): return self.results
            async def acknowledge_result(self, task_id, *, delivery_state):
                if delivery_state == "released":
                    released.set()
                    return {}
                entered.set()
                await resume.wait()
                return {"created": True, "speech_id": "speech"}
        port = Port()
        agent = type("Agent", (), {"queue_frame": AsyncMock()})()
        processor = VoiceResultDeliveryProcessor(agent=agent, result_port=port)
        await processor.start()
        port.results = [{"task_id": "task", "status": "ready"}]
        await asyncio.wait_for(entered.wait(), 2)
        stop = asyncio.create_task(processor.stop())
        await asyncio.sleep(0)
        resume.set()
        await asyncio.wait_for(stop, 2)
        assert released.is_set()
        agent.queue_frame.assert_not_awaited()
        assert processor.floor.lease is None
    asyncio.run(run())


def test_activation_and_reactivation_preserve_one_authorized_tool_catalog():
    async def run():
        required = {"search_web", "fetch_web_page", "get_current_time", "get_weather", "get_news"}
        schemas = [FunctionSchema(name=name, description=name, properties={}, required=[]) for name in required]
        llm = OpenAILLMService(api_key="unused", model="unused")
        tutor = TutorAgent(llm=llm, delegation_port=object(), foreground_tools=schemas)
        tutor.queue_frame = AsyncMock()
        context = LLMContext(tools=tutor.build_tools())
        aggregator = LLMAssistantAggregator(context)
        aggregator.push_frame = AsyncMock()
        for _ in range(3):
            tutor.queue_frame.reset_mock()
            await tutor.on_activated(None)
            for call in tutor.queue_frame.await_args_list:
                await aggregator.process_frame(call.args[0], FrameDirection.DOWNSTREAM)
            names = [tool.name if isinstance(tool, FunctionSchema) else tool.__name__ for tool in context.tools.standard_tools]
            assert required <= set(names)
            assert "delegate_research" in names
            assert len(names) == len(set(names))
        await aggregator.cleanup()
    asyncio.run(run())


def test_user_activity_during_delivery_turn_creation_rejects_input_and_discards_turn():
    async def run():
        entered, resume = asyncio.Event(), asyncio.Event()
        async def prepare(request_id):
            entered.set()
            await resume.wait()
            return "delivery-turn"
        journal = type("Journal", (), {})()
        journal.prepare_delivery = prepare
        journal.attach_delivery = Mock()
        journal.discard_delivery = AsyncMock()
        floor = VoiceSpeechFloor()
        processor = VoiceInputFloorProcessor(floor, journal)
        processor.push_frame = AsyncMock()
        lease = floor.reserve("result:1")
        admission = asyncio.get_running_loop().create_future()
        frame = LLMMessagesAppendFrame(messages=[{"role": "system", "content": "result"}], run_llm=True)
        frame.metadata.update(voice_admission=admission, voice_speech_lease=lease,
                              voice_request_id="1", voice_result_delivery=True)
        consume = asyncio.create_task(processor.process_frame(frame, FrameDirection.DOWNSTREAM))
        await entered.wait()
        floor.observe(VADUserStartedSpeakingFrame())
        resume.set()
        await consume
        assert await admission is False
        journal.discard_delivery.assert_awaited_once_with("delivery-turn")
        journal.attach_delivery.assert_not_called()
        processor.push_frame.assert_not_awaited()
        assert floor.lease is None
    asyncio.run(run())
