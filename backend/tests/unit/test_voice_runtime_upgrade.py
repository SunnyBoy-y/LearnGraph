from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)

from app.voice.embedded_context import VoiceContextAdapter
from app.voice.embedded_dashscope_stt import DashScopeSTTService
from app.voice.embedded_delegation import (
    DelegationKind,
    DelegationRequest,
    dispatch_delegation,
)
from app.voice.embedded_generation import VoiceGenerationGate
from app.voice.embedded_turn_intent import (
    LanguageProfile,
    TurnIntent,
    TurnIntentClassifier,
    is_backchannel,
    merge_transcript_segments,
)
from app.voice.embedded_turn_strategy import AdaptiveUserTurnStartStrategy
from app.voice.runner_registry import (
    VoiceRunnerHandle,
    clear_registry,
    close_runner,
    register_runner,
)


class _StrategyHarness(AdaptiveUserTurnStartStrategy):
    def __init__(self) -> None:
        super().__init__(decision_window=0.03)
        self.started: list[bool] = []
        self.fast_interrupts = 0

    def create_task(self, coroutine, name=None, context=None):
        return asyncio.create_task(coroutine)

    async def cancel_task(self, task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def trigger_user_turn_started(self, **kwargs):
        self.started.append(bool(kwargs.get("enable_interruptions")))

    async def _fast_interrupt(self):
        self.fast_interrupts += 1


class _Port:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.requests: list[DelegationRequest] = []

    async def delegate(self, request: DelegationRequest):
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        return {"task_id": "task-1", "status": "accepted"}

    async def task_status(self, request):
        return {"task_id": request.task_id, "status": "running"}

    async def cancel_task(self, request):
        return {"task_id": request.task_id, "status": "cancel_requested"}

    async def revise_task(self, request):
        return {"task_id": request.task_id, "status": "accepted", "revision": 2}


class _STT(DashScopeSTTService):
    async def emit_stt_usage_metrics(self) -> None:
        return None


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send(self, value: str) -> None:
        self.sent.append(value)


@dataclass
class _Handle:
    context_snapshot: dict[str, Any]


def test_multilingual_classifier_and_merge():
    classifier = TurnIntentClassifier()
    assert classifier.classify("嗯嗯") is TurnIntent.BACKCHANNEL
    assert classifier.classify("好的") is TurnIntent.BACKCHANNEL
    assert classifier.classify("好的，继续") is TurnIntent.UNCERTAIN
    assert classifier.classify("等等，先停一下") is TurnIntent.INTERRUPTION
    assert classifier.classify("wait, stop") is TurnIntent.INTERRUPTION
    assert classifier.classify("okay") is TurnIntent.BACKCHANNEL
    assert classifier.classify("knowledge") is TurnIntent.UNCERTAIN

    classifier.register(
        LanguageProfile(
            code="de",
            backchannels=frozenset({"ja"}),
            interruptions=frozenset({"halt"}),
        )
    )
    assert classifier.classify("ja") is TurnIntent.BACKCHANNEL
    assert classifier.classify("halt") is TurnIntent.INTERRUPTION
    assert merge_transcript_segments("你好", "你好吗") == "你好吗"
    assert merge_transcript_segments("你好", "吗") == "你好吗"
    assert merge_transcript_segments("hello", "world") == "hello world"


def test_decision_timeout_really_waits_and_treats_silence_as_noise():
    """窗口确实会等待；窗口内一个词都没识别出来 = 噪声，不算抢断。

    修复前这里把「无可识别文本」直接当成真抢断：0.5s 窗口一到期就打断，而当时的
    回合往往还没产出任何音频，于是那条问题被结算成「无回答」，紧接着 LLM 对它的
    回答被记到了下一句（实测两次误断间隔 0.65s / 0.7s，均无任何识别文本）。
    """
    async def run() -> None:
        strategy = _StrategyHarness()
        strategy.set_interrupt_callback(strategy._fast_interrupt)
        strategy._pending = True
        strategy._pending_text = ""
        started = time.monotonic()
        await strategy._decision_timeout(0)
        assert time.monotonic() - started >= 0.025
        assert strategy.started == []
        assert strategy.fast_interrupts == 0

    asyncio.run(run())


def test_backchannel_does_not_interrupt_and_explicit_word_does():
    async def run() -> None:
        strategy = _StrategyHarness()
        strategy.set_interrupt_callback(strategy._fast_interrupt)
        await strategy.process_frame(BotStartedSpeakingFrame())
        await strategy.process_frame(VADUserStartedSpeakingFrame())
        await strategy.process_frame(
            InterimTranscriptionFrame("嗯嗯", "user", "2026-01-01T00:00:00Z")
        )
        await strategy.process_frame(VADUserStoppedSpeakingFrame())
        assert strategy.started == []
        assert strategy.fast_interrupts == 0

        strategy = _StrategyHarness()
        strategy.set_interrupt_callback(strategy._fast_interrupt)
        await strategy.process_frame(BotStartedSpeakingFrame())
        await strategy.process_frame(VADUserStartedSpeakingFrame())
        await strategy.process_frame(
            InterimTranscriptionFrame("等等", "user", "2026-01-01T00:00:00Z")
        )
        assert strategy.started == []
        assert strategy.fast_interrupts == 0
        await strategy.process_frame(
            TranscriptionFrame("等等", "user", "2026-01-01T00:00:00Z", finalized=True)
        )
        assert strategy.started == [True]
        assert strategy.fast_interrupts == 1

    asyncio.run(run())


def test_noise_burst_does_not_cut_off_an_unanswered_turn():
    """VAD 起止之间什么都没识别出来时，不得当成抢断。

    这正是实测里把「还没作答的回合」切走的那条路径：噪声触发 VAD，静音判定在
    ASR 出字之前就结束，于是回合被结算成无回答，而它的回答随后挂到了下一句上。
    """
    async def run() -> None:
        strategy = _StrategyHarness()
        strategy.set_interrupt_callback(strategy._fast_interrupt)
        await strategy.process_frame(BotStartedSpeakingFrame())
        await strategy.process_frame(VADUserStartedSpeakingFrame())
        await strategy.process_frame(VADUserStoppedSpeakingFrame())
        assert strategy.started == []
        assert strategy.fast_interrupts == 0

    asyncio.run(run())


def test_backchannel_outside_decision_window_does_not_interrupt():
    """背声词在"决策窗口之外"到达时同样不许打断。

    实测路径：ASR 的 partial 比本机 VAD 帧先到（``嗯`` 的 partial 早 7ms），于是
    ``_pending`` 还是 False；旧实现在这条路上**无条件**起一个带打断的回合——13ms
    后 turn.interrupted，正在播的回答被切掉，紧接着这句背声词还被当成一个问题。
    """
    async def run() -> None:
        strategy = _StrategyHarness()
        strategy.set_interrupt_callback(strategy._fast_interrupt)
        await strategy.process_frame(BotStartedSpeakingFrame())

        # 没有任何 VAD 帧：partial 先到。
        await strategy.process_frame(
            InterimTranscriptionFrame("嗯", "user", "2026-01-01T00:00:00Z")
        )
        await strategy.process_frame(
            InterimTranscriptionFrame("嗯。", "user", "2026-01-01T00:00:00Z")
        )
        # 迟到的 final 落在窗口关闭之后，同样必须被吞掉。
        await strategy.process_frame(
            TranscriptionFrame("嗯。", "user", "2026-01-01T00:00:00Z", finalized=True)
        )
        assert strategy.started == []
        assert strategy.fast_interrupts == 0

        # 同一个位置上换成真正的内容：回合照旧起、TTS 照旧快停。
        await strategy.process_frame(
            InterimTranscriptionFrame("延迟有点大", "user", "2026-01-01T00:00:00Z")
        )
        assert strategy.started == []
        assert strategy.fast_interrupts == 0
        await strategy.process_frame(
            TranscriptionFrame("延迟有点大", "user", "2026-01-01T00:00:00Z", finalized=True)
        )
        assert strategy.started == [True]
        assert strategy.fast_interrupts == 1

    asyncio.run(run())


def test_backchannel_after_window_closed_does_not_interrupt():
    """窗口已按"什么都没听清"关掉之后，回来的 final 若是背声词仍不得起回合。"""
    async def run() -> None:
        strategy = _StrategyHarness()
        strategy.set_interrupt_callback(strategy._fast_interrupt)
        await strategy.process_frame(BotStartedSpeakingFrame())
        await strategy.process_frame(VADUserStartedSpeakingFrame())
        await strategy.process_frame(VADUserStoppedSpeakingFrame())
        assert strategy.started == []

        # VAD 停止时还没识别出任何字，窗口判定为噪声并关闭；final 随后才到。
        await strategy.process_frame(
            TranscriptionFrame("哦。", "user", "2026-01-01T00:00:00Z", finalized=True)
        )
        assert strategy.started == []
        assert strategy.fast_interrupts == 0

    asyncio.run(run())


def test_backchannel_judgement_is_whole_utterance_only():
    assert is_backchannel("嗯。") is True
    assert is_backchannel("嗯") is True
    assert is_backchannel("嗯嗯") is True
    assert is_backchannel("哦。") is True
    assert is_backchannel("对。") is True
    assert is_backchannel("啊") is True
    assert is_backchannel("uh huh") is True
    # 带实义的发言不是背声词——它是一次真实抢断。
    assert is_backchannel("嗯，我想问一下三秒复制") is False
    assert is_backchannel("延迟有点大。") is False
    assert is_backchannel("不是") is False


def test_stt_drops_backchannel_while_assistant_is_replying():
    """机器人在作答时，整句背声词的 interim/final 不入管线。"""
    async def run() -> None:
        from pipecat.processors.frame_processor import FrameDirection

        stt = _STT(api_key="test")
        stt._ws = _FakeWS()

        frames: list[Any] = []

        async def push_frame(frame, direction=None):
            if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
                frames.append(frame)

        stt.push_frame = push_frame

        # 机器人没在作答：「嗯」可能就是在回答，必须放行。
        assert stt._is_backchannel_filler("好的。") is False

        await stt.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert stt._bot_speaking is True
        assert stt._is_backchannel_filler("好的。") is True
        assert stt._is_backchannel_filler("嗯，我想问一下") is False

        stt._speech_seen = True
        await stt._send_commit("vad-stop")
        suppressed_before = stt._suppressed_backchannels
        await stt._handle_final({"text": "好的。"})
        assert frames == []
        assert stt._suppressed_backchannels == suppressed_before + 1

        # 同一条路上真正的发言照旧下发。
        stt._speech_seen = True
        await stt._send_commit("vad-stop-2")
        await stt._handle_final({"text": "延迟有点大。"})
        assert [frame.text for frame in frames] == ["延迟有点大。"]

        await stt.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert stt._bot_speaking is False
        stt._speech_seen = True
        await stt._send_commit("vad-stop-3")
        await stt._handle_final({"text": "好的。"})
        assert [frame.text for frame in frames] == ["延迟有点大。", "好的。"]

    asyncio.run(run())


def test_stt_drops_backchannel_while_assistant_reply_is_in_flight():
    """音频还没出去、但助手已经开口：这段空档里的背声词同样要被滤掉。"""
    async def run() -> None:
        from app.voice.journal import VoiceTurnJournal, _TurnState

        journal = VoiceTurnJournal("vs-test")
        stt = _STT(api_key="test")
        stt._journal = journal

        # 没有回合 → 不过滤（「嗯」可能就是在回答）。
        assert journal.assistant_reply_in_flight() is False
        assert stt._is_backchannel_filler("好的。") is False

        # 回合已开（用户刚问完，助手欠一个回答）→ 得到这一回合收尾为止都过滤，
        # 包括"文本已出、音频还没写出去"的空档。
        journal._turn = _TurnState(turn_id="turn-1", user_text="延迟有点大。")
        assert journal.assistant_reply_in_flight() is True
        assert stt._is_backchannel_filler("好的。") is True
        assert stt._is_backchannel_filler("嗯，我想问一下") is False

        # 回合已落定 → 放行：此刻的「嗯」很可能就是回答。
        journal._turn.finalized = True
        assert journal.assistant_reply_in_flight() is False
        assert stt._is_backchannel_filler("好的。") is False

    asyncio.run(run())


def test_stt_multiple_commits_and_late_final_isolation():
    async def run() -> None:
        stt = _STT(api_key="test")
        stt._ws = _FakeWS()
        stt._speech_seen = True
        await stt._send_commit("first")
        stt._speech_seen = True
        await stt._send_commit("second")
        assert len(stt._pending_commits) == 2

        frames = []

        async def push_frame(frame, direction=None):
            frames.append(frame)

        stt.push_frame = push_frame
        await stt._handle_final({"text": "one"})
        await stt._handle_final({"text": "two"})
        assert [frame.text for frame in frames] == ["one", "two"]
        assert frames[0].result["commit_trigger"] == "first"
        assert frames[1].result["commit_trigger"] == "second"

        stt._speech_seen = True
        await stt._send_commit("late")
        stt._utterance_generation += 1
        await stt._handle_final({"text": "must-drop"})
        # 2026-09-18 起（"说两轮后哑"修复）：迟到的 final **仍然变成文本**，只是被标
        # superseded —— 文本不许丢，但也不许它结束/重开回合。旧断言（整条丢弃）正是
        # 那次故障的成因，不能再作为契约。
        assert [frame.text for frame in frames] == ["one", "two", "must-drop"]
        assert frames[2].finalized is False
        assert frames[2].result["superseded"] is True

    asyncio.run(run())


def test_stt_resumed_pause_keeps_text_without_finalizing_turn():
    async def run() -> None:
        from pipecat.processors.frame_processor import FrameDirection

        stt = _STT(api_key="test")
        stt._ws = _FakeWS()
        stt._speech_seen = True
        await stt._send_commit("vad-stop")

        # The user resumed before DashScope's first final arrived. The final is
        # still part of this turn and must reach aggregation, but it is not the
        # transcript that may end the turn.
        frames = []

        async def push_frame(frame, direction=None):
            frames.append(frame)

        stt.push_frame = push_frame
        await stt.process_frame(
            VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        await stt._handle_final({"text": "前半句"})

        final = next(frame for frame in frames if isinstance(frame, TranscriptionFrame))
        assert final.text == "前半句"
        assert final.finalized is False
        assert final.result["superseded"] is True

    asyncio.run(run())


def test_generation_gate_drops_audio_after_interruption():
    gate = VoiceGenerationGate()
    gate.note_tts_started("ctx-1")
    assert gate.accepts_audio("ctx-1")
    gate.invalidate()
    assert not gate.accepts_audio("ctx-1")
    gate.note_tts_started("ctx-2")
    assert gate.accepts_audio("ctx-2")


def test_generation_gate_context_history_is_bounded():
    gate = VoiceGenerationGate()
    limit = gate.MAX_CONTEXT_GENERATIONS
    for index in range(limit + 40):
        gate.note_tts_started(f"ctx-{index}")

    contexts = gate.state()["contexts"]
    assert len(contexts) == limit
    assert "ctx-0" not in contexts
    assert f"ctx-{limit + 39}" in contexts

    # Audio can reach the gate before TTSStartedFrame. That implicit-insertion
    # path must be bounded too, otherwise it can grow without limit.
    for index in range(limit + 40):
        gate.accepts_audio(f"implicit-{index}")
    assert len(gate.state()["contexts"]) == limit


def test_delegation_port_returns_receipt_and_times_out():
    async def run() -> None:
        port = _Port()
        receipt = await dispatch_delegation(
            port,
            DelegationRequest(kind=DelegationKind.RESEARCH, query="q"),
        )
        assert receipt == {"task_id": "task-1", "status": "accepted"}
        assert port.requests[0].kind is DelegationKind.RESEARCH

        slow = _Port(delay=0.1)
        timed_out = await dispatch_delegation(
            slow,
            DelegationRequest(kind=DelegationKind.REASONING, query="q"),
            timeout_secs=0.01,
        )
        assert timed_out["status"] == "unknown"
        assert timed_out["reason"] == "delegation_acceptance_timeout"

    asyncio.run(run())


def test_context_adapter_reconstructs_snapshot_and_turns():
    handle = _Handle(
        context_snapshot={
            "context_build_id": "ctx-7",
            "prompt_block": "remember this",
            "history": [{"role": "user", "content": "older question"}],
        }
    )
    adapter = VoiceContextAdapter(
        "vs-1",
        "system",
        session_loader=lambda _: handle,
        turn_loader=lambda *_args, **_kwargs: [
            {"user_text": "new question", "assistant_text": "new answer"}
        ],
    )
    state = adapter.load()
    assert state.version == "ctx-7"
    messages = [dict(item) for item in state.messages]
    assert messages[0] == {"role": "system", "content": "system"}
    assert any("remember this" in item["content"] for item in messages)
    assert {"role": "user", "content": "new question"} in messages
    assert {"role": "assistant", "content": "new answer"} in messages


def test_runner_close_stops_task_and_cleans_processors():
    async def run() -> None:
        clear_registry()
        calls: list[str] = []

        class Processor:
            async def cleanup(self):
                calls.append("cleanup")

        async def stop(reason: str):
            calls.append(f"stop:{reason}")

        task = asyncio.create_task(asyncio.sleep(60))
        handle = VoiceRunnerHandle(
            voice_session_id="vs-close",
            chat_session_id="chat",
            owner_user_id="user",
            stop=stop,
            task=task,
            processors=[Processor()],
        )
        register_runner(handle)
        assert await close_runner("vs-close", reason="test") is True
        assert calls == ["stop:test", "cleanup"]
        assert task.cancelled()

    asyncio.run(run())
