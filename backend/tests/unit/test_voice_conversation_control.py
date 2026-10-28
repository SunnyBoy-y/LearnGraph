"""P0 controls at the actual frame boundaries, with providers replaced by sinks."""
import asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    LLMContextFrame, LLMMessagesAppendFrame, LLMFullResponseStartFrame,
    LLMFullResponseEndFrame, TextFrame, TranscriptionFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from app.voice.conversation_control import VoiceConversationPolicy, VoiceConversationProcessor, conversation_intent
from app.voice.embedded_result_bridge import VoiceResultDeliveryProcessor
from app.voice.response_feedback import VoiceResponseFeedback
from app.voice.speech_floor import VoiceSpeechFloor

D = FrameDirection.DOWNSTREAM


@pytest.mark.parametrize("text,kind,payload", [
    ("别说呢个了 闭嘴！", "silence", ""),
    ("请你回复 疑问：嗯？？？？", "verbatim", "疑问：嗯？？？？"),
    ("请你说一遍：嗯？疑问的时候尾音挑上去，对吧？", "verbatim", "嗯？疑问的时候尾音挑上去，对吧？"),
    ("请你回复 嗯。。。。。。。。。。。（疑惑）", "verbatim", "嗯。。。。。。。。。。。（疑惑）"),
    ("请你回复：闭嘴！", "verbatim", "闭嘴！"),
    ("故事里的人为什么说闭嘴？", "answer", ""),
    ("说过就不要说了", "no_repeat", ""),
    ("给我看呢 我没看到你就不要播报", "display", ""),
    ("别担心我页面了 你好烦啊", "dismiss_scene", ""),
    ("针对我现在的问题！！", "current_only", ""),
    ("那继续做", "answer", ""),
])
def test_real_transcript_intents(text, kind, payload):
    intent = conversation_intent(text)
    assert (intent.kind, intent.text) == (kind, payload)


def test_silence_survives_backchannels_but_new_question_can_answer_without_old_results():
    policy = VoiceConversationPolicy()
    policy.accept("闭嘴！")
    policy.accept("嗯嗯")
    assert policy.quiet
    restored = VoiceConversationPolicy(policy.snapshot())
    assert restored.quiet and not restored.background_allowed
    restored.accept("什么是LearnGraph")
    assert not restored.quiet and not restored.background_allowed
    restored.accept("恢复自动播报")
    assert restored.background_allowed


def test_stop_blocks_late_output_before_any_model_and_does_not_cancel_task():
    async def run():
        policy, floor, stopped, frames = VoiceConversationPolicy(), VoiceSpeechFloor(), AsyncMock(), []
        control = VoiceConversationProcessor(policy, stage="input", interrupt=stopped, floor=floor)
        output = VoiceConversationProcessor(policy, stage="output")
        async def sink(frame, direction): frames.append(frame)
        control.push_frame = output.push_frame = sink
        await control.process_frame(TranscriptionFrame("闭嘴！", "user", "now"), D)
        await output.process_frame(TextFrame("迟到的答案"), D)
        stopped.assert_awaited_once()
        assert not any(isinstance(f, TextFrame) and not isinstance(f, TranscriptionFrame) for f in frames)
        assert not floor.closed  # the call and durable task execution stay alive
    asyncio.run(run())


def test_verbatim_bypasses_model_preserves_punctuation_and_suppresses_feedback():
    async def run():
        policy, texts, forwarded = VoiceConversationPolicy(), [], []
        journal = AsyncMock()
        journal.current_turn_origin = lambda: "user"
        journal.current_turn_id = lambda: "turn-1"
        feedback = VoiceResponseFeedback(journal, delay=0, policy=policy)
        output = VoiceConversationProcessor(policy, stage="output")
        async def sink(frame, direction):
            if isinstance(frame, TextFrame): texts.append(frame.text)
        feedback.push_frame = sink
        output.push_frame = feedback.process_frame
        async def speak(text):
            for f in (LLMFullResponseStartFrame(), TextFrame(text), LLMFullResponseEndFrame()):
                f.metadata["voice_verbatim"] = True
                await output.process_frame(f, D)
        context = VoiceConversationProcessor(policy, stage="context", emit_response=speak)
        async def downstream(frame, direction): forwarded.append(frame)
        context.push_frame = downstream
        await context.process_frame(LLMContextFrame(LLMContext([{"role": "user", "content": "请你回复 疑问：嗯？？？？"}])), D)
        await output.process_frame(TextFrame("嗯，我看看。"), D)
        assert texts == ["疑问：嗯？？？？"]
        assert forwarded == []
        assert feedback._timer is None
        await feedback.cleanup()
    asyncio.run(run())


def test_page_policy_is_persisted_before_rtvi_cancels_the_input_task():
    async def run():
        published = []
        async def publish(value): published.append(value)
        async def interrupt(): raise asyncio.CancelledError()
        processor = VoiceConversationProcessor(VoiceConversationPolicy(), stage="input", publish=publish, interrupt=interrupt)
        with pytest.raises(asyncio.CancelledError):
            await processor.process_frame(LLMMessagesAppendFrame(messages=[{"role":"user","content":"我要页面"}],run_llm=True),D)
        assert published[-1]["mode"] == "display"
        assert published[-1]["wait_for_scene"] and published[-1]["background_muted"]
    asyncio.run(run())


def test_silent_control_finishes_without_audio_error_and_policy_context_is_bounded():
    async def run():
        policy, journal, forwarded = VoiceConversationPolicy(), AsyncMock(), []
        journal.current_turn_origin = lambda: "user"
        processor = VoiceConversationProcessor(policy, stage="context", journal=journal)
        async def sink(frame, direction): forwarded.append(frame)
        processor.push_frame = sink
        await processor.process_frame(LLMContextFrame(LLMContext([{"role": "user", "content": "说过就不要说了"}])), D)
        journal.assistant_completed.assert_awaited_once_with(memory=False, silent_control=True)
        assert forwarded == []
        context = LLMContext([{"role": "user", "content": "那现在的问题呢？"}])
        await processor.process_frame(LLMContextFrame(context), D)
        await processor.process_frame(LLMContextFrame(context), D)
        assert sum(str(m.get("content", "")).startswith("[voice-conversation-policy]") for m in context.get_messages()) == 1
    asyncio.run(run())


class Port:
    def __init__(self):
        self.results = []
        self.acks = []
        self.focused = []
        self.claimed, self.proceed = asyncio.Event(), asyncio.Event()
        self.proceed.set()
    async def ready_results(self, **kwargs): return [dict(r) for r in self.results]
    async def acknowledge_result(self, task_id, *, delivery_state):
        self.acks.append((task_id, delivery_state))
        if delivery_state == "delivered":
            self.claimed.set()
            await self.proceed.wait()
        return {"created": True, "speech_id": "speech-1"}
    async def show_scene(self, task_id, anchor):
        self.focused.append((task_id, anchor))
        return {"status": "requested"}
    async def update_speech_delivery(self, *args): pass


def result():
    return {"task_id": "demo", "result_id": "r1", "result_version": 1, "requirement_version": 1,
            "status": "ready", "auto_delivery": True, "topic_current": True,
            "scene": {"status": "unconfirmed", "available_anchors": ["summary", "visual-chart"], "anchors": []}}


def test_show_page_opens_visual_without_claiming_speech_or_render_success():
    async def run():
        policy, port, agent = VoiceConversationPolicy(), Port(), AsyncMock()
        policy.accept("我要页面")
        port.results = [result()]
        processor = VoiceResultDeliveryProcessor(agent=agent, result_port=port, conversation_policy=policy)
        await processor.show_requested_scene()
        await processor.show_requested_scene()
        assert port.focused == [("demo", "visual-chart")]
        assert port.acks == []
        agent.queue_frame.assert_not_awaited()
        assert not policy.allows_result(result())
        policy.accept("别担心我页面了 你好烦啊")
        assert not policy.wait_for_scene and not policy.background_allowed
    asyncio.run(run())


def test_new_result_version_is_not_suppressed_by_a_previously_played_version():
    async def run():
        port, policy, received = Port(), VoiceConversationPolicy(), asyncio.Event()
        frames = []
        async def queue(frame):
            frames.append(frame)
            received.set()
        agent = SimpleNamespace(tool_call_active=False, queue_frame=queue)
        processor = VoiceResultDeliveryProcessor(agent=agent, result_port=port, conversation_policy=policy, poll_interval=.25)
        await processor.start()
        try:
            port.results = [result()]
            await asyncio.wait_for(received.wait(), 2)
            received.clear()
            await processor.playback_ended()
            processor._cooldown_until = 0
            port.results[0] = {**result(), "result_id": "r2", "result_version": 2}
            await asyncio.wait_for(received.wait(), 2)
            assert len(frames) == 2
        finally:
            await processor.stop()
    asyncio.run(run())


def test_rejected_or_replayed_ui_request_cannot_unmute_a_stop():
    policy = VoiceConversationPolicy()
    policy.accept("闭嘴")
    processor = VoiceResultDeliveryProcessor(agent=None, result_port=None, conversation_policy=policy,
                                            control_dispatch=AsyncMock())
    assert not processor.enqueue_control("", "解释一下")
    assert policy.quiet
    assert processor.enqueue_control("click-1", "解释一下")
    assert not policy.quiet
    policy.accept("闭嘴", force=True)
    assert processor.enqueue_control("click-1", "解释一下")
    assert policy.quiet


def test_late_verbatim_start_and_end_cannot_close_the_replacement_response():
    async def run():
        policy, floor, frames = VoiceConversationPolicy(), VoiceSpeechFloor(), []
        policy.accept("请你回复：嗯？")
        old_revision = policy.revision
        policy.accept("什么是LearnGraph")
        floor.generating = True
        processor = VoiceResultDeliveryProcessor(agent=None, result_port=None, conversation_policy=policy, floor=floor)
        async def sink(frame, direction): frames.append(frame)
        processor.push_frame = sink
        for frame in (LLMFullResponseStartFrame(), TextFrame("嗯？"), LLMFullResponseEndFrame()):
            frame.metadata["voice_revision"] = old_revision
            await processor.process_frame(frame, D)
        assert frames == []
        assert floor.generating
    asyncio.run(run())


@pytest.mark.parametrize("change", ["stop", "new_question", "version", "topic"])
def test_ready_claim_race_releases_without_speaking(change):
    async def run():
        policy, port, agent, floor = VoiceConversationPolicy(), Port(), SimpleNamespace(tool_call_active=False, queue_frame=AsyncMock()), VoiceSpeechFloor()
        port.proceed.clear()
        processor = VoiceResultDeliveryProcessor(agent=agent, result_port=port, conversation_policy=policy, floor=floor, poll_interval=.25)
        await processor.start()
        try:
            port.results = [result()]
            await asyncio.wait_for(port.claimed.wait(), 2)
            if change == "stop": policy.accept("闭嘴")
            elif change == "new_question": floor.observe(LLMMessagesAppendFrame([{"role": "user", "content": "新问题"}]), input_side=True)
            elif change == "version": port.results[0]["result_version"] = 2
            else: port.results[0]["topic_current"] = False
            released = asyncio.Event()
            original = port.acknowledge_result
            async def ack(task_id, *, delivery_state):
                response = await original(task_id, delivery_state=delivery_state)
                if delivery_state == "released": released.set()
                return response
            port.acknowledge_result = ack
            port.proceed.set()
            await asyncio.wait_for(released.wait(), 2)
            agent.queue_frame.assert_not_awaited()
            assert ("demo", "released") in port.acks
        finally: await processor.stop()
    asyncio.run(run())


def test_admitted_result_does_not_reexecute_historical_silent_control():
    async def run():
        from app.voice.journal import VoiceTurnJournal
        policy, journal, forwarded = VoiceConversationPolicy(), VoiceTurnJournal("test"), []
        policy.accept("恢复自动播报")
        journal.attach_delivery("delivery-turn")
        journal.assistant_completed = AsyncMock()
        processor = VoiceConversationProcessor(policy, stage="context", journal=journal)
        async def sink(frame, direction): forwarded.append(frame)
        processor.push_frame = sink
        frame = LLMContextFrame(LLMContext([
            {"role": "system", "content": "Tutor"},
            {"role": "user", "content": "恢复自动播报"},
            {"role": "system", "content": "后台结果已就绪：测试颜色是蓝色。"},
        ]))
        await processor.process_frame(frame, D)
        assert forwarded == [frame]
        assert journal.current_turn_origin() == "delivery"
        journal.assistant_completed.assert_not_awaited()
        journal._cancel_recovery_timers()
    asyncio.run(run())
