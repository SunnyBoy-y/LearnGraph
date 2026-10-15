"""云端 VAD 的"补静音"账目修复回归测试。

真机证据（2026-09-18，生产日志）：
    input_audio_buffer.committed  03:25:24.280
    padded 600ms silence         03:25:24.393   ← 113ms 之后才补
同一现象在三条独立连接上成立（6ms / 113ms / 8ms），而 ``processor.notice``
``cloud_vad_no_final`` 的计数正是"已补 3 段、收到 2 段"——**差 1**。

机理：本机能量尾巴 ``DASHSCOPE_ASR_ENERGY_STOP_SECS``（默认 0.4s）与云端 VAD 的
``silence_duration_ms``（400ms）等长，尾巴期间 ``keep = speaking`` 送出去的正是
安静帧 → 云端往往先我们一步自己 ``committed`` → 我们再补的那 600ms 静音落进已提交
的**空缓冲**：拿不到 final、多付计费，并让"补了几段/回收了几段"的账永久拉偏，
于是报出措辞完全错误的"云端 VAD 可能未生效"。

这里钉住修好后的契约：
1. 云端已收尾且此后没再送音频 → **不补**（省计费、账目不再漂移）；
2. 云端收尾后用户又说了话（有新音频） → **照补**（那是真需要收尾的段）；
3. 有 committed 的连接里"补了却没 final" → 不进 durable notice（VAD 明显在工作）；
4. 一条 committed 都没见过 → 原告警与措辞保持不变（那才是真的 VAD 未生效）；
5. 证据按**连接**重置：重连后的新连接要重新作证。
"""

from __future__ import annotations

import asyncio
import contextlib

import test_asr_connection_lifecycle as harness  # 复用既有替身与工具（无 __init__.py）

from app.voice import embedded_dashscope_stt as mod
from app.voice.embedded_dashscope_stt import (
    DashScopeSTTService,
    DashScopeSTTSettings,
)

_FINAL_EVT = harness._FINAL_EVT
FakeJournal = harness.FakeJournal
FakeUpstream = harness.FakeUpstream
_feed = harness._feed
_pcm = harness._pcm
_service = harness._service
_stop_task = harness._stop_task
_wait_until = harness._wait_until
DashScopeSTTSettings = DashScopeSTTSettings


def _cloud_vad_service(journal: FakeJournal | None = None) -> DashScopeSTTService:
    return _service(journal=journal, turn_detection="server_vad")


def _committed_event() -> dict:
    return {"type": "input_audio_buffer.committed"}


def test_pad_is_skipped_when_the_cloud_already_finalized_this_segment():
    """云端先收尾、此后没有新音频：补静音是无效流量，必须跳过。

    这就是真机上"已补 3 段、收到 2 段"的那一次：那一小段静音永远等不到 final，
    因为服务端手里已经没有属于它的音频了。

    修复点：服务端的 ``committed`` 必须复位本地"自上次交接以来听到过语音"的门闩——
    服务端已经把这批音频收下了，那就是一次交接。
    """

    async def exercise() -> None:
        from pipecat.frames.frames import VADUserStoppedSpeakingFrame
        from pipecat.processors.frame_processor import FrameDirection

        service = _cloud_vad_service()
        upstream = FakeUpstream()
        service._ws = upstream
        reader = asyncio.create_task(service._read_loop(upstream))
        try:
            await _feed(service, _pcm(0.2, 0.2))
            assert service._speech_since_commit() is True, "前提：本地确实听到过语音"

            upstream.push(_committed_event())
            await _wait_until(lambda: service._server_commits == 1)
            assert service._audio_chunks_since_server_commit == 0, (
                "服务端已收尾：此后没有新音频"
            )
            assert service._speech_since_commit() is False, (
                "服务端收下了这批音频 = 完成一次交接，本地门闩必须复位"
            )

            before_ms = upstream.appended_ms
            await service.process_frame(
                VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )

            assert upstream.appended_ms == before_ms, "不得再补静音"
            assert service._segments_padded == 0
            assert service._segments_finished == 0
        finally:
            await _stop_task(reader)

    asyncio.run(exercise())


def test_pad_is_skipped_when_no_audio_ever_reached_the_server_for_this_segment():
    """补刀判据：本地 VAD 报了开口、但一帧音频都没送上去（门闩为真、计数为 0）。

    那种情况下补静音同样是白发一段——云端手里根本没有这个"段"。
    """

    async def exercise() -> None:
        service = _cloud_vad_service()
        upstream = FakeUpstream()
        service._ws = upstream

        # 伪造状态：服务端收尾过（说明 VAD 在判段），本地门闩为真（VAD 报过开口），
        # 但此后没有任何 append（音量门把音频挡在门外）。
        service._server_commits = 1
        service._audio_chunks_since_server_commit = 0
        service._speech_seen = True

        before_ms = upstream.appended_ms
        await service._finish_segment("energy-stop")

        assert upstream.appended_ms == before_ms, "没有未提交音频时不得补静音"
        assert service._segments_padded == 0

    asyncio.run(exercise())


def test_pad_is_still_sent_when_new_audio_arrived_after_the_cloud_commit():
    """云端收尾之后用户又说了话：那一段还没有被提交，必须照补静音。"""

    async def exercise() -> None:
        from pipecat.frames.frames import VADUserStoppedSpeakingFrame
        from pipecat.processors.frame_processor import FrameDirection

        service = _cloud_vad_service()
        upstream = FakeUpstream()
        service._ws = upstream
        reader = asyncio.create_task(service._read_loop(upstream))
        try:
            await _feed(service, _pcm(0.2, 0.2))
            upstream.push(_committed_event())
            await _wait_until(lambda: service._server_commits == 1)
            await _feed(service, _pcm(0.2, 0.2))
            assert service._audio_chunks_since_server_commit > 0
            assert service._speech_since_commit() is True

            before_ms = upstream.appended_ms
            await service.process_frame(
                VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )

            padded_ms = upstream.appended_ms - before_ms
            assert padded_ms >= 400, f"补静音必须够云端判出段尾，实际 {padded_ms:.0f}ms"
            assert set(upstream.appended_payloads[-1]) == {0}, "补的必须是全零静音"
            assert service._segments_padded == 1
        finally:
            await _stop_task(reader)

    asyncio.run(exercise())


def test_a_pad_without_a_final_is_not_a_notice_while_the_cloud_is_committing():
    """云端明明在判段（见过 committed），"补了却没 final"就不该冒充"VAD 未生效"。"""

    async def exercise() -> None:
        from pipecat.frames.frames import VADUserStoppedSpeakingFrame
        from pipecat.processors.frame_processor import FrameDirection

        journal = FakeJournal()
        service = _cloud_vad_service(journal=journal)
        upstream = FakeUpstream()
        service._ws = upstream
        service._pad_final_grace_secs = 0.0
        reader = asyncio.create_task(service._read_loop(upstream))
        try:
            await _feed(service, _pcm(0.2, 0.2))
            upstream.push(_committed_event())
            await _wait_until(lambda: service._server_commits == 1)
            await _feed(service, _pcm(0.2, 0.2))
            await service.process_frame(
                VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            # 前提：确实补了静音、而且确实没有 final 回应。
            assert service._segments_padded == 1
            assert service._segments_resolved == 0

            await service._check_pad_stall()

            assert journal.notices == [], (
                "云端已经 committed 过，这条连接不是'VAD 未生效'"
            )
        finally:
            await _stop_task(reader)

    asyncio.run(exercise())


def test_notice_still_fires_when_the_cloud_never_commits():
    """反例护栏：一次 committed 都没有（VAD 参数被拒/不支持）时，告警与措辞照旧。"""

    async def exercise() -> None:
        from pipecat.frames.frames import VADUserStoppedSpeakingFrame
        from pipecat.processors.frame_processor import FrameDirection

        journal = FakeJournal()
        service = _cloud_vad_service(journal=journal)
        upstream = FakeUpstream()
        service._ws = upstream
        service._pad_final_grace_secs = 0.0

        await _feed(service, _pcm(0.2, 0.2))
        await service.process_frame(
            VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        assert service._segments_padded == 1

        await service._check_pad_stall()

        assert [notice[:2] for notice in journal.notices] == [
            ("asr", "cloud_vad_no_final")
        ]
        assert "云端 VAD 可能未生效" in journal.notices[0][2]

    asyncio.run(exercise())


def test_the_evidence_is_reset_per_connection(monkeypatch):
    """证据按连接作证：重连后的新连接必须重新证明"云端在判段"。

    否则一条早期正常、后期参数被拒的连接会拿旧证据把真故障说成误报。
    """

    async def exercise() -> None:
        service = _cloud_vad_service()
        created: list[FakeUpstream] = []

        async def _connect(*_args, **_kwargs):
            upstream = FakeUpstream()
            created.append(upstream)
            return upstream

        monkeypatch.setattr(mod.websockets, "connect", _connect)

        service._server_commits = 3
        service._audio_chunks_since_server_commit = 7

        task = asyncio.create_task(service._ensure_upstream(reason="test"))
        try:
            await _wait_until(lambda: service._ws is not None)
            assert created
            assert service._server_commits == 0
            assert service._audio_chunks_since_server_commit == 0
        finally:
            await _stop_task(task)
            with contextlib.suppress(Exception):
                await service.cleanup()

    asyncio.run(exercise())


def test_final_still_resolves_the_pad_accounting():
    """账目仍然对得上：一次补静音 + 一条 final → resolved 追平 padded。"""

    async def exercise() -> None:
        from pipecat.frames.frames import VADUserStoppedSpeakingFrame
        from pipecat.processors.frame_processor import FrameDirection

        service = _cloud_vad_service()
        upstream = FakeUpstream()
        service._ws = upstream
        reader = asyncio.create_task(service._read_loop(upstream))
        try:
            await _feed(service, _pcm(0.2, 0.2))
            await service.process_frame(
                VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            assert service._segments_padded == 1

            upstream.push({"type": _FINAL_EVT, "transcript": "你好"})
            await _wait_until(lambda: service._segments_resolved == 1)

            assert service._segments_resolved >= service._segments_padded
            assert service._pad_stall_deadline is None
        finally:
            await _stop_task(reader)

    asyncio.run(exercise())
