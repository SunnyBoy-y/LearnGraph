"""离线验证全双工 ASR 的连接生命周期硬化。

真实通话需要 DashScope 凭据，本机没有；但审计（2026-09-16）里那几条"连接会一直
挂着 / 连接活着但永远不出文本"的路径全都不依赖凭据——它们只取决于我们自己的状态
机与 websockets 的关闭语义，因此可以用一个最小上游替身离线钉住：

1. **静音帧不再灌进上游**，也不再把"每一帧都重连一轮"压在音频路径上；
2. **本机 VAD 帧缺席时能量兜底**仍会打开 commit 与 partial 的门闩（修复前这种
   情况等于"用户说什么都没反应，且没有任何报错"）；
3. **上游正常关闭**（``websockets`` 的 ``__aiter__`` 会吞掉 ``ConnectionClosedOK``
   并让迭代器正常返回）会被识别并登记，而不是留下一个"已关闭但引用还在"的 handle；
4. **发送失败**会真的关掉旧 socket（旧实现只置空引用，旧连接靠着 20s 一次的
   keepalive ping 一直活着，cleanup 也再关不到它）；
5. **空闲退役**：没人说话就主动 session.finish 断开，下一次语音再懒重连；
6. **收尾与重连的竞态**不会留下孤儿会话（收尾之后建出来的连接就地关掉）。

``FakeUpstream`` 刻意复刻真实语义：迭代结束时不抛异常（只有调用抛错的路径才走
except），而 ``close()`` 之后 ``closed`` 为真、``send()`` 抛 ``ConnectionClosed``。

异步一律按本仓库既有写法用 ``asyncio.run(...)`` 驱动（本机没有 pytest-asyncio）。
"""

from __future__ import annotations

import array
import asyncio
import contextlib
import json

from app.voice import embedded_dashscope_stt as mod
from app.voice.embedded_dashscope_stt import (
    DashScopeSTTService,
    DashScopeSTTSettings,
)


# --------------------------------------------------------------------- 替身


class FakeJournal:
    """只记录调用，不落库。"""

    def __init__(self) -> None:
        self.notices: list[tuple[str, str, str]] = []
        self.errors: list[tuple[str, str, dict]] = []
        self.retries: list[tuple[str, dict]] = []

    async def notice(self, stage: str, code: str, message: str) -> None:
        self.notices.append((stage, code, message))

    async def processor_error(self, stage: str, message: str, **kwargs) -> None:
        self.errors.append((stage, message, kwargs))

    async def retry_scheduled(self, stage: str, **kwargs) -> None:
        self.retries.append((stage, kwargs))


# DashScope 的 final 事件类型（partial 见各测试内联）。
_FINAL_EVT = "conversation.item.input_audio_transcription.completed"


class FakeUpstream:
    """最小 websockets 连接替身（见模块 docstring 的语义说明）。"""

    def __init__(self, *, fail_send: bool = False) -> None:
        self.received: list[dict] = []
        self.close_calls = 0
        self.closed = False
        self.fail_send = fail_send
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._eof = asyncio.Event()

    # ---- websockets 表面
    async def send(self, raw: str) -> None:
        from websockets.exceptions import ConnectionClosedOK

        if self.fail_send or self.closed:
            raise ConnectionClosedOK(None, None)
        self.received.append(json.loads(raw))

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True
        self._eof.set()

    def push(self, payload: dict) -> None:
        self._queue.put_nowait(json.dumps(payload))

    def finish(self) -> None:
        """服务端优雅关会话：后续迭代正常结束（不抛异常）。"""
        self.closed = True
        self._eof.set()

    def __aiter__(self) -> "FakeUpstream":
        return self

    async def __anext__(self) -> str:
        if not self._queue.empty():
            return self._queue.get_nowait()
        if self._eof.is_set():
            raise StopAsyncIteration
        getter = asyncio.ensure_future(self._queue.get())
        waiter = asyncio.ensure_future(self._eof.wait())
        try:
            done, _pending = await asyncio.wait(
                {getter, waiter}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for future in (getter, waiter):
                if not future.done():
                    future.cancel()
        if getter in done:
            return getter.result()
        raise StopAsyncIteration

    @property
    def event_types(self) -> list[str]:
        return [item.get("type", "") for item in self.received]

    @property
    def appended_audio(self) -> int:
        return sum(
            1
            for item in self.received
            if item.get("type") == "input_audio_buffer.append"
        )

    @property
    def commits(self) -> int:
        return sum(
            1 for item in self.received if item.get("type") == "input_audio_buffer.commit"
        )

    @property
    def appended_payloads(self) -> list[bytes]:
        """每条 ``input_audio_buffer.append`` 解出来的原始 PCM（按发送顺序）。"""
        import base64

        return [
            base64.b64decode(str(item.get("audio") or ""))
            for item in self.received
            if item.get("type") == "input_audio_buffer.append"
        ]

    @property
    def appended_ms(self) -> float:
        """累计送上去的音频时长（毫秒），16k 单声道 PCM16。"""
        return sum(len(payload) / 2 / 16000.0 for payload in self.appended_payloads) * 1000.0


def _service(
    *,
    journal: FakeJournal | None = None,
    idle_timeout: float = 60.0,
    energy_rms: float = 0.006,
    energy_stop: float = 0.4,
    turn_detection: str = "none",
) -> DashScopeSTTService:
    """构造被测服务。

    默认 ``turn_detection="none"``（Manual）：本文件里的大多数用例是**Manual 模式的
    回归**（commit 是 finalize 手段）。生产默认值是 ``server_vad``（云端 VAD + 补静音），
    那部分的用例显式传 ``turn_detection="server_vad"``。
    """
    service = DashScopeSTTService(
        api_key="test-key",
        settings=DashScopeSTTSettings(
            model="qwen3-asr-flash-realtime", turn_detection=turn_detection
        ),
        journal=journal,
    )
    service._idle_timeout_secs = idle_timeout
    service._energy_rms = energy_rms
    service._energy_stop_secs = energy_stop
    return service


def _pcm(seconds: float, amplitude: float = 0.0) -> bytes:
    """16k 单声道 PCM16。``amplitude`` 是满量程比例（0.2 = 20%）。"""
    samples = array.array("h", [int(32767 * amplitude)] * int(16000 * seconds))
    return samples.tobytes()


async def _feed(service: DashScopeSTTService, audio: bytes) -> None:
    generator = service.run_stt(audio)
    async for _frame in generator:
        pass


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met in time")


async def _stop_task(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ------------------------------------------------------------------ 语音门闩


def test_silence_never_reaches_upstream_and_never_reconnects():
    """静音帧：既不 append 到云端，也不触发重连（旧行为：每帧一轮 4 次退避重连）。"""

    async def exercise() -> None:
        service = _service()
        upstream = FakeUpstream()
        service._ws = upstream
        ensure_calls: list[str] = []

        async def _record_ensure(*, reason: str = "lost") -> bool:
            ensure_calls.append(reason)
            return False

        service._ensure_upstream = _record_ensure  # type: ignore[method-assign]

        for _ in range(20):
            await _feed(service, _pcm(0.02))

        assert upstream.appended_audio == 0, "静音帧不得进入上游音频缓冲"
        assert ensure_calls == [], "静音帧不得触发重连"

    asyncio.run(exercise())


def test_energy_fallback_carries_the_turn_when_vad_frames_are_absent():
    """VAD 一帧都不出现时，能量兜底仍要能 commit 并放出 partial。

    修复前：``_speech_seen`` 只由 ``VADUserStartedSpeakingFrame`` 置位，于是 commit
    与 partial 被自己的门闩全部丢掉 —— 连接活着、音频在灌、云端其实在识别，但用户
    说什么都没反应，而且没有任何报错。
    """

    async def exercise() -> None:
        journal = FakeJournal()
        service = _service(journal=journal)
        upstream = FakeUpstream()
        service._ws = upstream
        pushed: list[object] = []

        async def _record_push(frame, *_args, **_kwargs) -> None:
            pushed.append(frame)

        service.push_frame = _record_push  # type: ignore[method-assign]

        reader = asyncio.create_task(service._read_loop(upstream))
        try:
            for _cycle in range(3):
                await _feed(service, _pcm(0.1, 0.2))
                upstream.push(
                    {
                        "type": "conversation.item.input_audio_transcription.text",
                        "text": "你好世界",
                    }
                )
                # 让读循环真的把这条 partial 取走：生产环境里音频帧之间本来就有
                # 20ms 的 await，读循环有的是机会跑。
                await asyncio.sleep(0.02)
                await _feed(service, _pcm(0.6))
            await _wait_until(lambda: upstream.commits >= 3)
            await asyncio.sleep(0.05)
        finally:
            await _stop_task(reader)

        assert upstream.commits == 3, "每段语音收口都应提交一次"
        assert upstream.appended_audio >= 3
        assert any(
            type(frame).__name__ == "InterimTranscriptionFrame" for frame in pushed
        ), "能量兜底期间 partial 必须仍然可达前端"
        assert journal.notices[0][:2] == ("asr", "vad_frames_missing")
        assert journal.errors == [], "能量兜底不是错误，不得降级"

    asyncio.run(exercise())


def test_vad_frames_alone_still_drive_commit_and_speech_gate():
    """VAD 正常工作时的旧契约不变：开口置门闩、停止即 commit。"""

    async def exercise() -> None:
        from pipecat.frames.frames import (
            VADUserStartedSpeakingFrame,
            VADUserStoppedSpeakingFrame,
        )
        from pipecat.processors.frame_processor import FrameDirection

        service = _service()
        upstream = FakeUpstream()
        service._ws = upstream
        service.create_task = lambda coro, *a, **kw: asyncio.ensure_future(  # type: ignore[method-assign]
            coro
        )

        await service.process_frame(
            VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        assert service._speech_seen is True
        assert service._vad_seen is True

        await service.process_frame(
            VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        assert upstream.commits == 1
        assert service._speech_seen is False
        # VAD 正常时不需要能量兜底：不该出现"VAD 缺席"的告警，也不该有能量开口。
        assert service._energy_speech_episodes == 0

    asyncio.run(exercise())


# ------------------------------------------------------------------ 连接死亡


def test_clean_upstream_close_is_noticed_instead_of_leaving_a_stale_handle():
    """正常关闭要留下痕迹并清空 handle（旧实现只处理抛异常的路径）。

    ``websockets`` 的 ``__aiter__`` 会吞掉 ``ConnectionClosedOK`` 并正常返回，所以
    "服务端优雅关会话"在旧实现里完全静默：静音期间没有音频就不会重连，UI 也没有提示。
    """

    async def exercise() -> None:
        journal = FakeJournal()
        service = _service(journal=journal)
        upstream = FakeUpstream()
        service._ws = upstream

        # 直接 await 读取循环：它就是"自己发现自己死了"的那条任务，因此拆卸路径
        # 必须跳过 cancel 自己（真实场景里 `_reader_task` 就是它）。
        reader = asyncio.create_task(service._read_loop(upstream))
        service._reader_task = reader
        await asyncio.sleep(0)
        upstream.finish()
        await asyncio.wait_for(reader, timeout=2.0)

        assert service._ws is None, "死连接不得继续被当成活连接"
        assert service._reader_task is None
        assert journal.notices and journal.notices[0][1] == "upstream_closed"
        assert journal.errors == [], "正常关闭不是故障，不该写成 ASR 错误"

    asyncio.run(exercise())


def test_send_failure_closes_the_old_socket_instead_of_orphaning_it():
    """发送失败必须真的关掉旧 socket（否则它会被 keepalive ping 保活到进程结束）。"""

    async def exercise() -> None:
        service = _service()
        upstream = FakeUpstream(fail_send=True)
        service._ws = upstream

        await _feed(service, _pcm(0.1, 0.2))

        assert upstream.close_calls >= 1, "旧 socket 必须被关闭，而不是只丢引用"
        assert service._ws is None

    asyncio.run(exercise())


# ------------------------------------------------------------------ 空闲退役


def test_idle_watchdog_retires_upstream_and_next_speech_reconnects(monkeypatch):
    """没人说话就退役上游；下一次语音自动重连并把音频送出去。"""

    async def exercise() -> None:
        monkeypatch.setattr(mod, "IDLE_CHECK_INTERVAL_SECS", 0.01)
        service = _service(idle_timeout=0.05)
        upstream = FakeUpstream()
        service._ws = upstream
        # 让"最后一次活动"落在过去，避免测试真的等一个空闲窗口。
        now = asyncio.get_running_loop().time()
        service._last_speech_at = now - 10
        service._last_upstream_event_at = now - 10

        watchdog = asyncio.create_task(service._idle_watchdog())
        try:
            await _wait_until(lambda: service._ws is None, timeout=2.0)
        finally:
            await _stop_task(watchdog)

        assert upstream.close_calls >= 1
        assert "session.finish" in upstream.event_types, "退役前必须发 session.finish"

        # 退役之后：静音帧不再把它叫回来，说话则重连。
        replacement = FakeUpstream()
        created: list[FakeUpstream] = []

        async def _connect(*_args, **_kwargs):
            created.append(replacement)
            return replacement

        monkeypatch.setattr(mod.websockets, "connect", _connect)
        for _ in range(5):
            await _feed(service, _pcm(0.02))
        assert created == [], "静音帧不得把连接叫回来"

        await _feed(service, _pcm(0.1, 0.2))
        assert created, "语音必须触发重连"
        assert replacement.appended_audio >= 1, "重连后的这一帧音频不能被丢掉"
        assert service._reader_task is not None
        await _stop_task(service._reader_task)

    asyncio.run(exercise())


def test_idle_watchdog_never_retires_a_session_with_an_in_flight_commit(monkeypatch):
    """在途 commit 会留住会话：在那里退役会丢掉回合正等着的 final。"""

    async def exercise() -> None:
        monkeypatch.setattr(mod, "IDLE_CHECK_INTERVAL_SECS", 0.01)
        service = _service(idle_timeout=0.05)
        upstream = FakeUpstream()
        service._ws = upstream
        now = asyncio.get_running_loop().time()
        service._last_speech_at = now - 10
        service._last_upstream_event_at = now - 10
        service._pending_commits.append(
            mod._PendingCommit(
                event_id="evt_commit_1",
                trigger="vad-stop",
                sent_at=now,
                connection_generation=0,
                utterance_generation=0,
            )
        )

        watchdog = asyncio.create_task(service._idle_watchdog())
        try:
            await asyncio.sleep(0.1)
        finally:
            await _stop_task(watchdog)

        assert upstream.close_calls == 0
        assert service._ws is upstream

    asyncio.run(exercise())


# ------------------------------------------------------------------ 收尾竞态


def test_cleanup_closes_a_socket_built_by_an_in_flight_reconnect(monkeypatch):
    """收尾与重连赛跑：收尾之后建出来的连接必须就地关掉，不能留成孤儿会话。"""

    async def exercise() -> None:
        service = _service()
        created: list[FakeUpstream] = []

        async def _slow_connect(*_args, **_kwargs):
            await asyncio.sleep(0.05)
            upstream = FakeUpstream()
            created.append(upstream)
            return upstream

        monkeypatch.setattr(mod.websockets, "connect", _slow_connect)

        reconnect = asyncio.create_task(service._ensure_upstream(reason="test"))
        await asyncio.sleep(0)  # 让重连进入握手
        await asyncio.wait_for(service.cleanup(), timeout=5.0)
        await asyncio.wait_for(reconnect, timeout=5.0)

        assert created, "测试前提：重连确实建出了一条连接"
        assert all(item.closed for item in created), "收尾之后建出来的连接必须被关闭"
        assert service._ws is None

    asyncio.run(exercise())


# ------------------------------------------------- 回合边界不丢文本（哑麦回归）


def test_late_final_after_turn_boundary_still_becomes_text():
    """说两轮之后麦克风"哑掉"的回归测试：迟到的 final 必须仍然变成文本。

    真机时序（账本 vs 日志已核实）：
      VAD 停止 → 我们发 commit → Smart Turn 判完回合并广播
      ``UserStoppedSpeakingFrame`` → 0.5~1s 之后 final 才回来。
    final 一旦输给 p99 安全网，回合边界就先到；旧实现在边界处 ``clear()`` 掉在途
    commit，于是那条 final 回来时找不到配对、被判为 "unsolicited" 整条丢弃——丢的
    正是用户刚说的那句话，而且此后每一轮都丢。用户看到的就是"说两轮之后麦克风死了"。

    第二、三段刻意复刻这条时序；同时因为能量收口没有发生（两段之间没静音），
    它们也顺带钉住 commit 门与 partial 门共用同一判据这件事——旧实现的
    ``_speech_seen`` 在第一次 commit 后已复位且不会重新置位，第二段的 commit
    会被自己的门闩丢掉。
    """

    async def exercise() -> None:
        from pipecat.frames.frames import (
            UserStoppedSpeakingFrame,
            VADUserStoppedSpeakingFrame,
        )
        from pipecat.processors.frame_processor import FrameDirection

        journal = FakeJournal()
        service = _service(journal=journal)
        upstream = FakeUpstream()
        service._ws = upstream
        transcripts: list[object] = []

        async def _record_push(frame, *_args, **_kwargs) -> None:
            # 只认转录帧：用量指标等旁路帧不计入这里的时序断言。
            if type(frame).__name__ == "TranscriptionFrame":
                transcripts.append(frame)

        service.push_frame = _record_push  # type: ignore[method-assign]

        reader = asyncio.create_task(service._read_loop(upstream))
        try:
            # 第一段：happy path，final 赶在回合边界之前到家。
            await _feed(service, _pcm(0.1, 0.2))
            await service.process_frame(
                VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            upstream.push({"type": _FINAL_EVT, "transcript": "第一句话"})
            await _wait_until(lambda: len(transcripts) == 1)
            await service.process_frame(
                UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            assert upstream.commits == 1

            # 第二段：final 输给安全网 —— 回合边界先到，final 后到。
            await _feed(service, _pcm(0.1, 0.2))
            await service.process_frame(
                VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            assert upstream.commits == 2, (
                "commit 门与 partial 门必须共用判据：第一次 commit 之后本回合又说了话，"
                "这里必须还能提交"
            )
            await service.process_frame(
                UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            upstream.push({"type": _FINAL_EVT, "transcript": "第二句话"})
            await _wait_until(lambda: len(transcripts) == 2)

            # 第三段：与第二段完全相同的时序 —— 修复前这里就是彻底安静的那一轮。
            await _feed(service, _pcm(0.1, 0.2))
            await service.process_frame(
                VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            await service.process_frame(
                UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            upstream.push({"type": _FINAL_EVT, "transcript": "第三句话"})
            await _wait_until(lambda: len(transcripts) == 3)
            await asyncio.sleep(0.02)
        finally:
            await _stop_task(reader)

        assert [frame.text for frame in transcripts] == ["第一句话", "第二句话", "第三句话"]
        # 赶在回合边界之前到家的 final 负责结束回合；迟到的那些只贡献文本。
        assert transcripts[0].finalized is True
        assert transcripts[1].finalized is False
        assert transcripts[2].finalized is False
        assert all(
            notice[1] != "final_without_commit" for notice in journal.notices
        ), "修复之后不该再有 final 被丢弃"

    asyncio.run(exercise())


def test_duplicate_dashscope_final_item_is_ignored_without_consuming_next_commit():
    """DashScope terminal events are idempotent by the server's item_id.

    The provider contract carries one ``item_id`` per completed transcription.
    A repeated terminal JSON must not pop the next local commit, otherwise the
    following utterance is attached to the previous one and the frontend/backend
    transcripts diverge.
    """

    async def exercise() -> None:
        service = _service()
        upstream = FakeUpstream()
        service._ws = upstream
        service._speech_seen = True
        await service._send_commit("first")
        service._speech_seen = True
        await service._send_commit("second")
        second_commit_id = service._pending_commits[-1].event_id
        frames: list[object] = []

        async def _record_push(frame, *_args, **_kwargs) -> None:
            if type(frame).__name__ == "TranscriptionFrame":
                frames.append(frame)

        service.push_frame = _record_push  # type: ignore[method-assign]
        await service._handle_final(
            {
                "type": _FINAL_EVT,
                "event_id": "event-final-1",
                "item_id": "item-1",
                "transcript": "第一句",
            }
        )
        await service._handle_final(
            {
                "type": _FINAL_EVT,
                "event_id": "event-final-duplicate",
                "item_id": "item-1",
                "transcript": "第一句",
            }
        )
        await service._handle_final(
            {
                "type": _FINAL_EVT,
                "event_id": "event-final-2",
                "item_id": "item-2",
                "transcript": "第二句",
            }
        )

        assert [frame.text for frame in frames] == ["第一句", "第二句"]
        assert frames[1].result["commit_id"] == second_commit_id
        assert frames[1].result["commit_trigger"] == "second"

    asyncio.run(exercise())


def test_speech_only_stops_commit_once_per_utterance():
    """残留噪声不得触发第二次 commit（静音缓冲的 commit 会拿到幻觉 final）。"""

    async def exercise() -> None:
        from pipecat.frames.frames import VADUserStoppedSpeakingFrame
        from pipecat.processors.frame_processor import FrameDirection

        service = _service()
        upstream = FakeUpstream()
        service._ws = upstream

        await _feed(service, _pcm(0.1, 0.2))
        await service.process_frame(
            VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        assert upstream.commits == 1

        # commit 之后 VAD 停止判定的那一两帧残留噪声（远小于最小可提交语音量），
        # 随后能量收口也来触发一次 commit —— 这一次必须被门闩挡住。
        await _feed(service, _pcm(0.02, 0.2))
        await _feed(service, _pcm(0.6))
        assert upstream.commits == 1, "不得对残留噪声提交（会拿到幻觉 final）"

    asyncio.run(exercise())


def test_final_without_a_commit_is_reported_instead_of_vanishing():
    """没有配对 commit 的 final 必须留下可见痕迹（旧实现只有一行 DEBUG）。"""

    async def exercise() -> None:
        journal = FakeJournal()
        service = _service(journal=journal)
        upstream = FakeUpstream()
        service._ws = upstream

        reader = asyncio.create_task(service._read_loop(upstream))
        try:
            upstream.push({"type": _FINAL_EVT, "transcript": "谁说的话？"})
            await _wait_until(
                lambda: any(
                    notice[1] == "final_without_commit" for notice in journal.notices
                )
            )
        finally:
            await _stop_task(reader)

        assert service._dropped_finals == 1
        assert journal.errors == [], "丢文本不是处理器故障，不该走降级通道"

    asyncio.run(exercise())


# ------------------------------------------------- 云端 VAD（2026-09-18 起默认）


def test_session_update_asks_for_400ms_server_vad_by_default():
    """默认（不改任何 env）必须把 session 配成 400ms 云端 VAD，而不是 Manual。

    这是"环境噪音被当成一句'嗯'打断对话"的正面修复：Manual 下任何够响的杂音都会被
    本机能量门送去识别并 finalize 成一句；云端 VAD 模式则由云端自己判断"这段算不算
    语音"。
    """
    service = _service(turn_detection="server_vad")
    session = service._build_session_update()["session"]

    assert session["turn_detection"] == {
        "type": "server_vad",
        "threshold": 0.0,
        "silence_duration_ms": 400,
    }


def test_turn_detection_can_be_pinned_to_manual_by_env(monkeypatch):
    """``DASHSCOPE_ASR_TURN_DETECTION=none`` 回退 Manual（出问题时的逃生门）。"""
    monkeypatch.setenv("DASHSCOPE_ASR_TURN_DETECTION", "none")
    service = _service(turn_detection="server_vad")  # env 优先
    assert service._cloud_vad_enabled is False
    assert service._build_session_update()["session"]["turn_detection"] is None


def test_vad_silence_ms_is_clamped_to_the_official_range(monkeypatch):
    """``silence_duration_ms`` 超出官方 [200, 6000] 时收敛，避免服务端 400。"""
    monkeypatch.setenv("DASHSCOPE_ASR_VAD_SILENCE_MS", "50")
    service = _service(turn_detection="server_vad")
    assert service._vad_silence_ms == 200.0
    # 补静音必须严格长于阈值，否则云端判不出段尾。
    assert service._segment_silence_ms >= service._vad_silence_ms


def test_eou_pads_silence_instead_of_commit_in_cloud_vad_mode():
    """本地判定"这句说完了" → 补静音让云端收尾；VAD 模式下不得再发 commit。"""

    async def exercise() -> None:
        from pipecat.frames.frames import VADUserStoppedSpeakingFrame
        from pipecat.processors.frame_processor import FrameDirection

        service = _service(turn_detection="server_vad")
        upstream = FakeUpstream()
        service._ws = upstream

        await _feed(service, _pcm(0.2, 0.2))
        before = upstream.appended_ms
        await service.process_frame(
            VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )

        assert upstream.commits == 0, "云端 VAD 模式下 commit 属官方'禁用场景'，不该发"
        padded = upstream.appended_ms - before
        assert padded >= 400, f"补静音必须够云端判出段尾，实际 {padded:.0f}ms"
        # 补的必须是**静音**（全零），不是把音频重发一遍。
        assert set(upstream.appended_payloads[-1]) == {0}
        assert service._segments_finished == 1
        # 一次语音只收尾一次：紧接着的重复信号不得再补一段。
        await service.process_frame(
            VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        assert service._segments_finished == 1

    asyncio.run(exercise())


def test_manual_mode_still_commits_and_never_pads(monkeypatch):
    """Manual 模式行为一字不变：commit 是 finalize 手段，不补静音。"""

    async def exercise() -> None:
        from pipecat.frames.frames import VADUserStoppedSpeakingFrame
        from pipecat.processors.frame_processor import FrameDirection

        service = _service(turn_detection="none")
        upstream = FakeUpstream()
        service._ws = upstream

        await _feed(service, _pcm(0.2, 0.2))
        before = upstream.appended_ms
        await service.process_frame(
            VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )

        assert upstream.commits == 1
        assert upstream.appended_ms == before, "Manual 模式不该出现补静音"

    asyncio.run(exercise())


def test_cloud_vad_never_forwards_silence_so_the_cloud_cannot_cut_mid_sentence():
    """云端 VAD 模式下静音一律不外送（Manual 的 2s 尾巴在 VAD 模式必须关掉）。

    否则云端会在任意 ≥``silence_duration_ms`` 的停顿处自己切段——而本机 VAD 允许的
    句中停顿比它长，等于把"半句被切"的权力交出去（当初正是为此改用 Manual）。
    段尾改由 ``_pad_silence()`` 在本地判定说完时补。
    """

    async def exercise() -> None:
        quiet = _pcm(0.5, 0.003)  # 有信号、但不到"像说话"：Manual 会当句尾尾巴送上去

        manual = _service(turn_detection="none")
        manual_upstream = FakeUpstream()
        manual._ws = manual_upstream
        await _feed(manual, _pcm(0.2, 0.2))
        await _feed(manual, quiet)
        assert quiet in manual_upstream.appended_payloads, "Manual 模式应保留句尾尾巴"

        vad = _service(turn_detection="server_vad")
        vad_upstream = FakeUpstream()
        vad._ws = vad_upstream
        await _feed(vad, _pcm(0.2, 0.2))
        await _feed(vad, quiet)
        assert quiet not in vad_upstream.appended_payloads, (
            "云端 VAD 模式不得把静音/弱音送上去：那会让云端自己切段"
        )

    asyncio.run(exercise())


def test_cloud_vad_final_without_a_commit_becomes_text():
    """云端自己收尾的 final（没有配对 commit）是正常路径：文本照进、允许结束回合。"""

    async def exercise() -> None:
        journal = FakeJournal()
        service = _service(journal=journal, turn_detection="server_vad")
        upstream = FakeUpstream()
        service._ws = upstream
        transcripts: list[object] = []

        async def _record_push(frame, *_args, **_kwargs) -> None:
            if type(frame).__name__ == "TranscriptionFrame":
                transcripts.append(frame)

        service.push_frame = _record_push  # type: ignore[method-assign]

        reader = asyncio.create_task(service._read_loop(upstream))
        try:
            upstream.push({"type": _FINAL_EVT, "transcript": "环境里的一句噪音"})
            await _wait_until(lambda: len(transcripts) == 1)
        finally:
            await _stop_task(reader)

        assert transcripts[0].text == "环境里的一句噪音"
        assert transcripts[0].finalized is True, (
            "云端 VAD 的 final 就是这一段的定稿，必须能结束回合（否则回合永不收尾）"
        )
        assert service._dropped_finals == 0
        assert all(
            notice[1] != "final_without_commit" for notice in journal.notices
        ), "VAD 模式下没有配对 commit 是正常路径，不该报丢文本"

    asyncio.run(exercise())


def test_manual_mode_still_reports_a_final_without_a_commit():
    """Manual 模式下"没有配对 commit 的 final"仍然是异常，必须留下痕迹。"""

    async def exercise() -> None:
        journal = FakeJournal()
        service = _service(journal=journal, turn_detection="none")
        upstream = FakeUpstream()
        service._ws = upstream

        reader = asyncio.create_task(service._read_loop(upstream))
        try:
            upstream.push({"type": _FINAL_EVT, "transcript": "谁说的话？"})
            await _wait_until(
                lambda: any(
                    notice[1] == "final_without_commit" for notice in journal.notices
                )
            )
        finally:
            await _stop_task(reader)

        assert service._dropped_finals == 1

    asyncio.run(exercise())


def test_pad_without_any_final_reports_a_notice():
    """补静音却拿不到 final（云端 VAD 没生效）必须报出来，而不是又变成静默哑麦。"""

    async def exercise() -> None:
        journal = FakeJournal()
        service = _service(journal=journal, turn_detection="server_vad")
        upstream = FakeUpstream()
        service._ws = upstream
        service._pad_final_grace_secs = 0.0

        from pipecat.frames.frames import VADUserStoppedSpeakingFrame
        from pipecat.processors.frame_processor import FrameDirection

        await _feed(service, _pcm(0.2, 0.2))
        await service.process_frame(
            VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        assert service._segments_padded == 1

        await service._check_pad_stall()
        assert [notice[1] for notice in journal.notices] == ["cloud_vad_no_final"]
        # 只报一次；而且真的收到 final 之后告警状态要复位。
        await service._check_pad_stall()
        assert len(journal.notices) == 1

        upstream.push({"type": _FINAL_EVT, "transcript": "你好"})
        reader = asyncio.create_task(service._read_loop(upstream))
        try:
            await _wait_until(lambda: service._segments_resolved == 1)
        finally:
            await _stop_task(reader)
        assert service._pad_stall_deadline is None

    asyncio.run(exercise())


# ------------------------------------------------------------------ 判据工具


def test_pcm_rms_separates_silence_from_speech():
    assert mod._pcm16_rms(_pcm(0.05)) == 0.0
    loud = mod._pcm16_rms(_pcm(0.05, 0.2))
    assert 0.15 < loud <= 0.25
    assert mod._pcm16_rms(b"") == 0.0
