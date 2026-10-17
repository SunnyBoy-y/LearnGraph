"""离线验证语音管线接线。

真实通话需要凭据（LLM / DashScope ASR / 火山 TTS 三个 provider），本机数据库
只有一个被禁用的 local_mock，因此这里用真实的 Pipecat Pipeline 链接器与真实的
``VoiceJournalProcessor`` 覆盖两件不依赖凭据、却最容易接错的事：

1. **拓扑**：两个 journal 观察点的插入位置，以及「助手观察点的上游就是总线桥」
   —— 这是 ``LLMRunFrame`` 反向回送能到达 LLM worker 的全部依据。
2. **路由**：哪些帧会触发哪一类事件（用户转录、助手文本、LLM 结束、播报起停、
   LLM 阶段错误）。

覆盖范围与**未覆盖**范围（不夸大）：

* 覆盖：两个观察点的插入位置，以及「助手观察点的直接上游就是总线桥」——用
  Pipecat 自己的 ``Pipeline._link_processors`` 链接器验证，而不是断言一份拷贝
  的列表。这是 ``LLMRunFrame`` 反向回送能到达 LLM worker 的**结构前提**，也是
  最容易被后续改动破坏的地方。生产形态（含结果投递、生成闸门）单独再测一份。
* 覆盖：每一类帧触发哪一类事件（转录 / 助手文本 / LLM 结束 / 播报起停 /
  LLM 阶段错误的恢复路径 / 打断）。
* 覆盖：``run_bot`` **自己的接线**——真跑一遍 run_bot（只桩掉 worker 启动、会话
  读库、观察点向上游推帧所需的 task manager），断言重试钩子与事件发布确实挂上
  了、注册到 worker 上的 ``on_pipeline_error`` 确实按阶段分流、断开确实落一条
  ``session.reconnecting``。这些接线是纯属性赋值/事件注册，丢了不会报错；本仓库
  就发生过一次（embedded_bot.py 被重写，三处接线消失，而当时全部测试仍绿）。
* **未覆盖**：帧在运行中的管线里的**实际投递**，以及真实 provider 的失败注入。
  执行它需要跑起带 task manager 的 ``PipelineWorker``（帧在 ``StartFrame`` 之前
  会被丢弃），也就是需要真实通话那套夹具；本机没有可用的 provider 凭据，因此
  不在此断言，也不假装断言过。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


def _make_stubs():
    """Plain pass-through processors standing in for each pipeline slot."""
    from pipecat.processors.frame_processor import FrameProcessor

    class Stub(FrameProcessor):
        def __init__(self, label: str) -> None:
            super().__init__()
            self.label = label

    return Stub


# ------------------------------------------------------------------ 1. 拓扑


def test_pipeline_topology_places_taps_where_the_design_requires():
    """用真实 Pipeline 链接器验证插入位置与「上游就是总线桥」。"""
    from pipecat.pipeline.pipeline import Pipeline

    from app.voice.embedded_bot import build_pipeline_steps

    Stub = _make_stubs()
    transport_input = Stub("transport_input")
    stt = Stub("stt")
    user_journal = Stub("user_journal")
    user_aggregator = Stub("user_aggregator")
    bridge = Stub("bridge")
    assistant_journal = Stub("assistant_journal")
    tts = Stub("tts")
    latency = Stub("latency")
    transport_output = Stub("transport_output")
    assistant_aggregator = Stub("assistant_aggregator")
    response_feedback = Stub("response_feedback")

    steps = build_pipeline_steps(
        transport_input=transport_input,
        stt=stt,
        user_journal=user_journal,
        user_aggregator=user_aggregator,
        bridge=bridge,
        assistant_journal=assistant_journal,
        tts=tts,
        latency=latency,
        transport_output=transport_output,
        assistant_aggregator=assistant_aggregator,
        response_feedback=response_feedback,
    )
    labels = [step.label for step in steps]
    assert labels == [
        "transport_input",
        "stt",
        "user_journal",
        "user_aggregator",
        "bridge",
        "response_feedback",
        "assistant_journal",
        "tts",
        "latency",
        "transport_output",
        "assistant_aggregator",
    ]

    # Let the real linker wire _next/_prev exactly as it does in production.
    Pipeline(steps)

    # The user tap must sit between STT and the aggregator (the aggregator
    # consumes transcription frames *and* ``LLMMessagesAppendFrame``, so
    # downstream of it the tap sees neither). This is the structural reason the
    # typed-message observation lives on this tap and not on the assistant one:
    # observing it downstream silently never fires, which left every typed turn
    # un-journaled.
    assert user_journal._prev is stt
    assert user_journal._next is user_aggregator

    # The retry hook pushes LLMRunFrame UPSTREAM from the assistant tap; the
    # bridge must be its upstream neighbour for that frame to reach the LLM.
    assert assistant_journal._prev is response_feedback and response_feedback._prev is bridge, (
        "LLMRunFrame 反向回送的目标不是总线桥，LLM 重试将无法到达模型"
    )
    assert assistant_journal._next is tts


def test_taps_are_omitted_when_no_journal_is_available():
    """没有 journal（未持久化会话）时不得残留观察点。"""
    from app.voice.embedded_bot import build_pipeline_steps

    Stub = _make_stubs()
    steps = build_pipeline_steps(
        transport_input=Stub("i"),
        stt=Stub("stt"),
        user_journal=None,
        user_aggregator=Stub("ua"),
        bridge=Stub("bridge"),
        assistant_journal=None,
        tts=Stub("tts"),
        latency=Stub("latency"),
        transport_output=Stub("o"),
        assistant_aggregator=Stub("aa"),
    )
    assert [s.label for s in steps] == [
        "i", "stt", "ua", "bridge", "tts", "latency", "o", "aa",
    ]


# ------------------------------------------------------------------ 2. 路由


class _RecordingJournal:
    """Records every journal call the taps make."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def user_interim(self, text: str) -> None:
        self.calls.append(("user_interim", text))

    async def user_final(self, text: str) -> None:
        self.calls.append(("user_final", text))

    async def user_started(self) -> None:
        self.calls.append(("user_started", None))

    async def user_stopped(self) -> None:
        self.calls.append(("user_stopped", None))

    async def user_typed(self, text: str) -> None:
        self.calls.append(("user_typed", text))

    def note_assistant_text(self, text: str) -> None:
        self.calls.append(("note_assistant_text", text))

    async def assistant_delta(self, text: str) -> None:
        self.calls.append(("assistant_delta", text))

    def llm_closed(self) -> None:
        self.calls.append(("llm_closed", None))

    async def playback_started(self) -> None:
        self.calls.append(("playback_started", None))

    async def playback_ended(self) -> None:
        self.calls.append(("playback_ended", None))

    async def turn_interrupted(self, *, reason: str = "", **kwargs: Any) -> None:
        self.calls.append(("turn_interrupted", reason))

    async def llm_failed(self, message: str) -> None:
        self.calls.append(("llm_failed", message))

    async def processor_error(self, stage: str, message: str, **kwargs: Any) -> None:
        self.calls.append(("processor_error", (stage, message)))

    def kinds(self) -> list[str]:
        return [name for name, _ in self.calls]


def _frames():
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        ErrorFrame,
        InterimTranscriptionFrame,
        InterruptionFrame,
        LLMFullResponseEndFrame,
        LLMMessagesAppendFrame,
        TextFrame,
        TranscriptionFrame,
        UserStartedSpeakingFrame,
    )

    return {
        "BotStartedSpeakingFrame": BotStartedSpeakingFrame,
        "BotStoppedSpeakingFrame": BotStoppedSpeakingFrame,
        "ErrorFrame": ErrorFrame,
        "InterimTranscriptionFrame": InterimTranscriptionFrame,
        "InterruptionFrame": InterruptionFrame,
        "LLMFullResponseEndFrame": LLMFullResponseEndFrame,
        "LLMMessagesAppendFrame": LLMMessagesAppendFrame,
        "TextFrame": TextFrame,
        "TranscriptionFrame": TranscriptionFrame,
        "UserStartedSpeakingFrame": UserStartedSpeakingFrame,
    }


def test_user_tap_routes_transcription_frames():
    from pipecat.processors.frame_processor import FrameDirection

    from app.voice.journal import VoiceJournalProcessor

    F = _frames()
    journal = _RecordingJournal()
    tap = VoiceJournalProcessor(journal, role="user")

    async def _run():
        await tap._observe_user(
            F["InterimTranscriptionFrame"]("半句", "u", "t"), FrameDirection.DOWNSTREAM
        )
        await tap._observe_user(
            F["TranscriptionFrame"]("整句", "u", "t", finalized=True),
            FrameDirection.DOWNSTREAM,
        )
        await tap._observe_user(F["UserStartedSpeakingFrame"](), FrameDirection.DOWNSTREAM)

    asyncio.run(_run())
    assert journal.calls == [
        ("user_interim", "半句"),
        ("user_final", "整句"),
        ("user_started", None),
    ]


def test_assistant_tap_routes_text_and_lifecycle_frames():
    from pipecat.processors.frame_processor import FrameDirection

    from app.voice.journal import VoiceJournalProcessor

    F = _frames()
    journal = _RecordingJournal()
    tap = VoiceJournalProcessor(journal, role="assistant")

    async def _run():
        await tap._observe_assistant(
            F["TextFrame"]("你好"), FrameDirection.DOWNSTREAM
        )
        await tap._observe_assistant(
            F["LLMFullResponseEndFrame"](), FrameDirection.DOWNSTREAM
        )
        await tap._observe_assistant(
            F["BotStartedSpeakingFrame"](), FrameDirection.DOWNSTREAM
        )
        await tap._observe_assistant(
            F["BotStoppedSpeakingFrame"](), FrameDirection.DOWNSTREAM
        )

    asyncio.run(_run())
    assert journal.calls == [
        ("note_assistant_text", "你好"),
        ("llm_closed", None),
        ("playback_started", None),
        ("playback_ended", None),
    ]


def test_assistant_tap_ignores_the_upstream_text_echo():
    """上游的 TextFrame 是已播报内容的回声，重复计入会让权威文本翻倍。"""
    from pipecat.processors.frame_processor import FrameDirection

    from app.voice.journal import VoiceJournalProcessor

    F = _frames()
    journal = _RecordingJournal()
    tap = VoiceJournalProcessor(journal, role="assistant")

    asyncio.run(
        tap._observe_assistant(F["TextFrame"]("回声"), FrameDirection.UPSTREAM)
    )
    assert [name for name, _ in journal.calls] == [] or journal.calls == []


def test_user_tap_routes_typed_text_from_the_append_frame():
    """打字消息必须由 **用户侧** 观察点记账。

    ``LLMMessagesAppendFrame``（RTVI ``send-text`` 从管线顶端推下来）会被
    ``LLMUserAggregator`` 消费且不再下发，用户侧观察点正好在它上游；挂在助手侧
    则永远看不到该帧（这正是「打字回合从不进账本」的根因）。
    """
    from pipecat.processors.frame_processor import FrameDirection

    from app.voice.journal import VoiceJournalProcessor

    F = _frames()
    journal = _RecordingJournal()
    tap = VoiceJournalProcessor(journal, role="user")

    async def _run():
        await tap._observe_user(
            F["LLMMessagesAppendFrame"](
                messages=[{"role": "user", "content": "打字的问题"}], run_llm=True
            ),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(_run())
    assert journal.calls == [("user_typed", "打字的问题")]


def test_assistant_tap_routes_typed_text_from_the_append_frame():
    """助手侧保留幂等兜底：万一将来该帧继续下发，也不能漏记或重复开回合。"""
    from pipecat.processors.frame_processor import FrameDirection

    from app.voice.journal import VoiceJournalProcessor

    F = _frames()
    journal = _RecordingJournal()
    tap = VoiceJournalProcessor(journal, role="assistant")

    async def _run():
        await tap._observe_assistant(
            F["LLMMessagesAppendFrame"](
                messages=[{"role": "user", "content": "打字的问题"}], run_llm=True
            ),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(_run())
    assert journal.calls == [("user_typed", "打字的问题")]


def test_llm_errors_take_the_recovery_path_not_the_plain_error_path():
    """LLM 阶段错误必须走重试/失败收尾，否则回合会悬挂、问题会丢。"""
    from pipecat.processors.frame_processor import FrameDirection

    from app.voice.journal import VoiceJournalProcessor

    F = _frames()

    class _FakeLLM:
        pass

    journal = _RecordingJournal()
    tap = VoiceJournalProcessor(journal, role="assistant")

    async def _run():
        await tap._observe_assistant(
            F["ErrorFrame"](error="upstream 503", processor=_FakeLLM()),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(_run())
    assert journal.kinds() == ["llm_failed"], journal.calls


def test_non_llm_errors_are_reported_per_stage():
    from pipecat.processors.frame_processor import FrameDirection

    from app.voice.journal import VoiceJournalProcessor

    F = _frames()

    class DashScopeSTTService:
        pass

    class VolcengineTTSService:
        pass

    journal = _RecordingJournal()
    tap = VoiceJournalProcessor(journal, role="assistant")

    async def _run():
        await tap._observe_assistant(
            F["ErrorFrame"](error="asr reset", processor=DashScopeSTTService()),
            FrameDirection.DOWNSTREAM,
        )
        await tap._observe_assistant(
            F["ErrorFrame"](error="tts reset", processor=VolcengineTTSService()),
            FrameDirection.DOWNSTREAM,
        )

    asyncio.run(_run())
    assert journal.calls == [
        ("processor_error", ("asr", "asr reset")),
        ("processor_error", ("tts", "tts reset")),
    ]


def test_interruption_frame_interrupts_the_turn():
    from pipecat.processors.frame_processor import FrameDirection

    from app.voice.journal import VoiceJournalProcessor

    F = _frames()
    journal = _RecordingJournal()
    tap = VoiceJournalProcessor(journal, role="assistant")

    asyncio.run(
        tap._observe_assistant(
            F["InterruptionFrame"](), FrameDirection.DOWNSTREAM
        )
    )
    assert journal.kinds() == ["turn_interrupted"]


# ------------------------------------------- 3. run_bot 的真实接线（非拷贝）

# 为什么要有这一段：上面两组测的是「观察点/路由的语义」，而接线（把 _publish、
# _retry_hook 挂到 journal 上、把 on_pipeline_error 注册到 worker 上）是纯属性
# 赋值，写错或漏写都不会报错，只有真正需要它的那一刻才会暴露。本仓库就发生过
# 一次：embedded_bot.py 被重写，三处接线消失，而上面 9 条测试全绿。
# 因此这里真跑一遍 run_bot，把「外部世界」桩掉，只验证接线本身。


class _StopPipeline(Exception):
    """哨兵：让 run_bot 停在 runner.run()，从而只走到接线为止。"""


class _FakeBus:
    async def subscribe(self, *_args, **_kwargs):
        return None

    async def unsubscribe(self, *_args, **_kwargs):
        return None

    async def send(self, *_args, **_kwargs):
        return None


class _StubTransport:
    """最小 transport：run_bot 只用到 input()/output() 与事件注册。"""

    def __init__(self, stub_cls) -> None:
        self._stub_cls = stub_cls
        self.handlers: dict[str, list] = {}

    def input(self):
        return self._stub_cls("transport_input")

    def output(self):
        return self._stub_cls("transport_output")

    def event_handler(self, name: str):
        def _register(fn):
            self.handlers.setdefault(name, []).append(fn)
            return fn

        return _register


def _run_bot_without_a_live_pipeline(monkeypatch):
    """真跑 run_bot，但把 provider/DB/管线启动全部换成桩。

    桩掉的只有「本机跑不动的东西」：worker runner 的启动、会话上下文读库、
    观察点向上游推帧所需的 task manager。被验证的是 run_bot 自己的接线代码。
    """
    from types import SimpleNamespace

    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    import app.voice.embedded_bot as bot_mod
    import app.voice.events as voice_events
    from app.voice.embedded_context import VoiceContextState
    from app.voice.journal import VoiceTurnJournal

    class _EmptyProviders:
        """没有可用 provider：服务对象退回环境变量占位值。"""

        asr = None
        llm = None
        tts = None
        session = None

    record: dict[str, object] = {"journals": [], "taps": [], "workers": []}

    class _StubProcessor(FrameProcessor):
        def __init__(self, label: str = "stub") -> None:
            super().__init__()
            self.label = label

    class _StubContextAdapter:
        """不读库：run_bot 只要求 adapter 能 load()/apply()。"""

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def load(self):
            return VoiceContextState(
                None, ({"role": "system", "content": "stub"},)
            )

        def apply(self, context, state) -> None:
            return None

    class _SpyJournal(VoiceTurnJournal):
        """记录 run_bot 把哪些事件送进了哪条恢复路径。"""

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.calls: list[tuple[str, str]] = []
            record["journals"].append(self)  # type: ignore[union-attr]

        async def llm_failed(self, message: str) -> None:
            self.calls.append(("llm_failed", message))

        async def processor_error(self, stage: str, message: str, **kwargs) -> None:
            self.calls.append(("processor_error", stage))

        async def session_ready(self, *, epoch=None) -> None:
            self.calls.append(("session_ready", epoch))

        async def session_reconnecting(self, *, reason="transport_rebuilt", attempt=0, delay_ms=0) -> None:
            self.calls.append(("session_reconnecting", reason))

    class _SpyTap(bot_mod.VoiceJournalProcessor):
        """记录 push_frame 而不真的转发：没有 task manager 就没有下游。"""

        def __init__(self, journal, *, role: str = "assistant") -> None:
            super().__init__(journal, role=role)
            self.pushed: list[tuple[object, object]] = []
            record["taps"].append(self)  # type: ignore[union-attr]

        async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
            self.pushed.append((frame, direction))

    class _FakeRunner:
        def __init__(self, **kwargs) -> None:
            self.bus = _FakeBus()
            self.cancel_calls: list[str] = []

        async def add_workers(self, *workers) -> None:
            record["workers"].extend(workers)  # type: ignore[union-attr]

        async def cancel(self, reason: str = "") -> None:
            self.cancel_calls.append(reason)

        async def run(self) -> None:
            raise _StopPipeline("接线已完成，测试在管线启动前停止")

    monkeypatch.setattr(bot_mod, "VoiceTurnJournal", _SpyJournal)
    monkeypatch.setattr(bot_mod, "VoiceJournalProcessor", _SpyTap)
    monkeypatch.setattr(bot_mod, "WorkerRunner", _FakeRunner)
    monkeypatch.setattr(bot_mod, "VoiceContextAdapter", _StubContextAdapter)
    monkeypatch.setattr(bot_mod, "load_session_row", lambda _session_id: None)
    # Provider 解析要读库，而本测试不碰库；桩成空配置，服务对象本身仍是真的。
    monkeypatch.setattr(
        bot_mod, "_resolve_voice_provider_config", lambda _session_id: _EmptyProviders()
    )
    # ``run_bot`` normally claims a DB-backed runtime owner.  This wiring test
    # deliberately does not initialize the application schema, so keep the
    # assertion focused on pipeline wiring rather than ownership persistence.
    monkeypatch.setattr(voice_events, "claim_runtime_owner", lambda *args, **kwargs: True)
    monkeypatch.setattr(voice_events, "release_runtime_owner", lambda *args, **kwargs: None)
    # 本机没有 provider 凭据：给出占位值，只为让服务对象能构造（管线不会启动，
    # 因此不会有任何出网请求）。
    for name, value in (
        ("VOICE_LLM_API_KEY", "wiring-probe-key"),
        ("VOICE_LLM_BASE_URL", "https://wiring-probe.invalid/v1"),
        ("DASHSCOPE_API_KEY", "wiring-probe-key"),
        ("VOLC_TTS_API_KEY", "wiring-probe-key"),
    ):
        monkeypatch.setenv(name, value)

    runner_args = SimpleNamespace(session_id="voice-wiring-probe", handle_sigint=False)
    transport = _StubTransport(_StubProcessor)
    record["transport"] = transport
    with pytest.raises(_StopPipeline):
        asyncio.run(bot_mod.run_bot(transport, runner_args))
    return record


def test_run_bot_binds_the_retry_hook_and_publisher_to_the_assistant_tap(monkeypatch):
    """run_bot 必须把重试钩子与事件发布都挂到助手观察点上。"""
    from pipecat.frames.frames import LLMRunFrame
    from pipecat.processors.frame_processor import FrameDirection

    record = _run_bot_without_a_live_pipeline(monkeypatch)
    journals = record["journals"]
    assert len(journals) == 1
    journal = journals[0]
    assert journal._retry_hook is not None, (
        "重试钩子未接线：模型一失败就只能终结回合，问题会丢"
    )
    assert journal._publish is not None, (
        "事件发布未接线：事件落库了但前端收不到实时字幕"
    )

    taps = record["taps"]
    assistant_taps = [tap for tap in taps if tap._role == "assistant"]
    assert len(assistant_taps) == 1, "助手观察点必须且只能有一个"
    tap = assistant_taps[0]

    asyncio.run(journal._retry_hook())
    assert [
        (type(frame).__name__, direction) for frame, direction in tap.pushed
    ] == [(LLMRunFrame.__name__, FrameDirection.UPSTREAM)], (
        "重试必须把 LLMRunFrame 反向推给上游（总线桥方向）"
    )

    tap.pushed.clear()
    asyncio.run(journal._publish({"type": "turn.accepted"}))
    assert len(tap.pushed) == 1
    frame, direction = tap.pushed[0]
    assert type(frame).__name__ == "RTVIServerMessageFrame"
    assert direction == FrameDirection.DOWNSTREAM
    assert frame.data == {
        "type": "voice-event",
        "event": {"type": "turn.accepted"},
    }


def test_run_bot_routes_llm_stage_errors_into_the_recovery_path(monkeypatch):
    """触发 run_bot 注册的 on_pipeline_error，验证「按阶段分流」这条接线。

    这里断言的是 worker 上真正注册的处理器，不是模块里的一份拷贝：上一次接线
    丢失正是因为没有任何测试碰过这个注册点。
    """
    from pipecat.frames.frames import ErrorFrame

    record = _run_bot_without_a_live_pipeline(monkeypatch)
    journals = record["journals"]
    assert len(journals) == 1
    journal = journals[0]

    workers = record["workers"]
    main_workers = [
        worker for worker in workers if type(worker).__name__ == "PipelineWorker"
    ]
    assert len(main_workers) == 1, "主 worker 必须已加进 runner"
    worker = main_workers[0]

    class _FakeLLM:
        pass

    class DashScopeSTTService:
        pass

    class VolcengineTTSService:
        pass

    async def _run():
        await worker._call_event_handler(
            "on_pipeline_error",
            ErrorFrame(error="upstream 503", processor=_FakeLLM()),
        )
        await worker._call_event_handler(
            "on_pipeline_error",
            ErrorFrame(error="asr reset", processor=DashScopeSTTService()),
        )
        await worker._call_event_handler(
            "on_pipeline_error",
            ErrorFrame(error="tts reset", processor=VolcengineTTSService()),
        )

    asyncio.run(_run())
    assert journal.calls == [
        ("llm_failed", "upstream 503"),
        ("processor_error", "asr"),
        ("processor_error", "tts"),
    ], "LLM 阶段必须走恢复路径，其余阶段按阶段上报"


def test_run_bot_announces_reconnecting_exactly_once_per_disconnect(monkeypatch):
    """客户端断开必须落一条 session.reconnecting，且同一次断开只落一条。

    这条接线负责「刷新后还能分清是重新拨号还是断线恢复」，并让对端知道通话
    正在重建；它同样是纯赋值式接线，漏了不会报错。
    """
    record = _run_bot_without_a_live_pipeline(monkeypatch)
    journals = record["journals"]
    assert len(journals) == 1
    journal = journals[0]
    transport = record["transport"]

    import app.voice.events as events_mod

    marked: list[str] = []
    monkeypatch.setattr(
        events_mod,
        "mark_session_reconnecting",
        lambda session_id, *_args, **_kwargs: marked.append(str(session_id)) or 1,
    )

    async def _drive():
        for handler in list(transport.handlers["on_client_connected"]):
            await handler(transport, object())
        for handler in list(transport.handlers["on_client_disconnected"]):
            # 断开事件可能重入：第二次不得再落一条重连事件。
            await handler(transport, object())
            await handler(transport, object())

    asyncio.run(_drive())
    assert journal.calls == [
        ("session_ready", None),
        ("session_reconnecting", "transport_rebuilt"),
    ], "断开必须落且只落一条 session.reconnecting"
    assert marked == ["voice-wiring-probe"], "断开必须把会话标记为可重连"


def test_production_step_list_keeps_the_bridge_two_hops_upstream_of_the_tap():
    """生产形态（含结果投递与生成闸门）下，助手观察点上游必须仍能走到总线桥。

    真实 run_bot 传的是带 result_delivery/generation_gate 的形态，而上面那条
    拓扑断言用的是精简形态。两者顺序不同，必须各测一份，否则「新插一个处理器
    挡在重试路径上」这类改动不会被发现。
    """
    from pipecat.pipeline.pipeline import Pipeline

    from app.voice.embedded_bot import build_pipeline_steps

    Stub = _make_stubs()
    bridge = Stub("bridge")
    assistant_journal = Stub("assistant_journal")
    result_delivery = Stub("result_delivery")
    generation_gate = Stub("generation_gate")
    tts = Stub("tts")

    steps = build_pipeline_steps(
        transport_input=Stub("transport_input"),
        stt=Stub("stt"),
        user_journal=Stub("user_journal"),
        user_aggregator=Stub("user_aggregator"),
        bridge=bridge,
        assistant_journal=assistant_journal,
        tts=tts,
        result_delivery=result_delivery,
        generation_gate=generation_gate,
        latency=Stub("latency"),
        transport_output=Stub("transport_output"),
        assistant_aggregator=Stub("assistant_aggregator"),
    )
    assert [step.label for step in steps] == [
        "transport_input",
        "stt",
        "user_journal",
        "user_aggregator",
        "bridge",
        "result_delivery",
        "assistant_journal",
        "tts",
        "generation_gate",
        "latency",
        "transport_output",
        "assistant_aggregator",
    ]
    Pipeline(steps)

    # 重试帧从观察点反向推：链上只允许夹带「原样透传上游帧」的处理器
    # （结果投递就是这么写的：除自己的播报之外一律 push_frame(frame, direction)）。
    assert assistant_journal._prev is result_delivery
    assert result_delivery._prev is bridge, (
        "助手观察点上游到总线桥之间多了处理器，LLM 重试可能被吞掉"
    )
    assert assistant_journal._next is tts
