import asyncio
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    FunctionCallFromLLM, FunctionCallsStartedFrame, InterruptionFrame,
    LLMFullResponseStartFrame, TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from app.voice.conversation_control import VoiceConversationPolicy
from app.voice.low_latency import complex_request_acknowledgement
from app.voice.response_feedback import VoiceResponseFeedback

D = FrameDirection.DOWNSTREAM


@pytest.mark.parametrize("query", [
    "你好", "今天几号", "什么是二分查找？", "请你回复：嗯？？？？", "闭嘴。",
    "不要重复了", "恢复自动播报", "二加二等于几",
])
def test_simple_verbatim_and_control_turns_do_not_get_fillers(query):
    assert complex_request_acknowledgement(query) is None


@pytest.mark.parametrize("query,expected", [
    ("帮我分析这三种学习方案的优缺点", "嗯……我来分析一下。"),
    ("请研究一下记忆系统相关文献", "嗯……我来梳理一下相关资料。"),
    ("帮我生成一个每周学习计划", "嗯……我来整理一下。"),
    ("请排查这段程序为什么会死锁", "嗯……我来看看怎么处理。"),
])
def test_complex_turns_get_a_short_request_specific_cue(query, expected):
    assert complex_request_acknowledgement(query) == expected


def feedback_for(query="帮我分析三个方案的优缺点", *, enabled=True, origin="user"):
    policy = VoiceConversationPolicy({"low_latency_mode": enabled})
    policy.accept(query)
    journal = SimpleNamespace(
        turn_id="turn-1", current_turn_id=lambda: journal.turn_id,
        current_turn_origin=lambda: origin,
    )
    async def activity(*args, **kwargs):
        pass
    journal.assistant_activity = activity
    floor = SimpleNamespace(user_speaking=False)
    feedback = VoiceResponseFeedback(journal, policy=policy, floor=floor, delay=60)
    frames = []
    async def push(frame, direction):
        frames.append(frame)
    feedback.push_frame = push
    return feedback, policy, journal, floor, frames


def cues(frames):
    return [frame for frame in frames if isinstance(frame, TextFrame)
            and frame.metadata.get("voice_acknowledgement")]


def test_mode_is_off_by_default_and_persisted_in_the_policy_snapshot():
    policy = VoiceConversationPolicy()
    policy.accept("帮我分析一下这个方案")
    assert not policy.feedback_allowed
    assert policy.snapshot()["low_latency_mode"] is False
    assert VoiceConversationPolicy({"low_latency_mode": True}).snapshot()["low_latency_mode"] is True


def test_complex_request_acknowledges_once_across_model_and_tool_rounds():
    async def run():
        feedback, policy, journal, _, frames = feedback_for()
        try:
            await feedback.process_frame(LLMFullResponseStartFrame(), D)
            await feedback._acknowledge(feedback._generation)
            await feedback.process_frame(FunctionCallsStartedFrame([
                FunctionCallFromLLM("delegate_tool_task", "tool-1", {}, None),
            ]), D)
            await feedback.process_frame(LLMFullResponseStartFrame(), D)
            await feedback._acknowledge(feedback._generation)
            assert [frame.text for frame in cues(frames)] == ["嗯……我来分析一下。"]
            assert cues(frames)[0].metadata["voice_revision"] == policy.revision
            journal.turn_id = "turn-2"
            policy.accept("请生成一份学习计划")
            await feedback.process_frame(LLMFullResponseStartFrame(), D)
            await feedback._acknowledge(feedback._generation)
            assert [frame.text for frame in cues(frames)] == [
                "嗯……我来分析一下。", "嗯……我来整理一下。",
            ]
        finally:
            await feedback.cleanup()
    asyncio.run(run())


@pytest.mark.parametrize("query,enabled,origin", [
    ("帮我分析一下这个方案", False, "user"),
    ("今天几号", True, "user"),
    ("请你回复：嗯？？？？", True, "user"),
    ("闭嘴", True, "user"),
    ("帮我分析一下这个方案", True, "delivery"),
])
def test_disabled_simple_control_and_background_turns_are_silent(query, enabled, origin):
    async def run():
        feedback, _, _, _, frames = feedback_for(query, enabled=enabled, origin=origin)
        try:
            await feedback.process_frame(LLMFullResponseStartFrame(), D)
            assert feedback._timer is None
            await feedback._acknowledge(feedback._generation)
            assert not cues(frames)
        finally:
            await feedback.cleanup()
    asyncio.run(run())


@pytest.mark.parametrize("change", ["interrupt", "disable", "new_input", "answer", "user_speaking"])
def test_pending_cue_cannot_outlive_an_interruption_setting_or_user_turn(change):
    async def run():
        feedback, policy, _, floor, frames = feedback_for()
        try:
            await feedback.process_frame(LLMFullResponseStartFrame(), D)
            generation = feedback._generation
            if change == "interrupt":
                await feedback.process_frame(InterruptionFrame(), D)
                await feedback.process_frame(LLMFullResponseStartFrame(), D)
            elif change == "disable":
                policy.low_latency_mode = False
                feedback.cancel_pending()
            elif change == "new_input":
                policy.accept("请重新分析另一份方案")
            elif change == "answer":
                await feedback.process_frame(TextFrame("直接给你结论。"), D)
            else:
                floor.user_speaking = True
            await feedback._acknowledge(generation)
            assert not cues(frames)
        finally:
            await feedback.cleanup()
    asyncio.run(run())


def test_default_grace_actually_emits_a_cue_before_a_slow_answer():
    async def run():
        feedback, policy, journal, floor, frames = feedback_for()
        await feedback.cleanup()
        feedback = VoiceResponseFeedback(journal, policy=policy, floor=floor)
        emitted = asyncio.Event()
        async def push(frame, direction):
            frames.append(frame)
            if isinstance(frame, TextFrame) and frame.metadata.get("voice_acknowledgement"):
                emitted.set()
        feedback.push_frame = push
        try:
            await feedback.process_frame(LLMFullResponseStartFrame(), D)
            await asyncio.wait_for(emitted.wait(), timeout=2)
            assert [frame.text for frame in cues(frames)] == ["嗯……我来分析一下。"]
            await feedback.process_frame(TextFrame("三个方案各有优缺点。"), D)
            assert [frame.text for frame in frames if isinstance(frame, TextFrame)] == [
                "嗯……我来分析一下。", "三个方案各有优缺点。",
            ]
        finally:
            await feedback.cleanup()
    asyncio.run(run())


def test_an_observed_heavy_tool_can_supply_a_cue_without_a_second_dispatch():
    async def run():
        feedback, _, _, _, frames = feedback_for("帮我处理一下")
        try:
            await feedback.process_frame(LLMFullResponseStartFrame(), D)
            assert feedback._timer is None
            call = FunctionCallsStartedFrame([
                FunctionCallFromLLM("delegate_tool_task", "tool-1", {}, None),
            ])
            await feedback.process_frame(call, D)
            await feedback._acknowledge(feedback._generation)
            assert [frame.text for frame in cues(frames)] == ["嗯……我来处理这个任务。"]
            assert sum(frame is call for frame in frames) == 1
        finally:
            await feedback.cleanup()
    asyncio.run(run())
