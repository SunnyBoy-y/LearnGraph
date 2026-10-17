"""离线验证 TTS 适配器写进音频队列的**账本触发帧**（契约 §2.4）。

不需要凭据也不需要上游：用本模块自己的 ``Message`` 编解码器造出火山侧会回的那几种
包，直接驱动 ``_synthesize_sentence`` / ``_drain_sentence_audio``，然后从真实的
audio context 队列里把账本帧取出来。

覆盖范围与**未覆盖**范围（不夸大）：

* 覆盖：账本帧的字段与取值、帧在音频队列里的位置（句首帧在音频**之前**、句尾帧在
  之后）、**只有帧被输出传输放行（= 音频真的写出去了）时账本才被写**、句序号由账本在
  释放时刻分配且跨句单调、没有 journal / journal 没有 turn 时的判定、``generation_source``
  的接线、打断与失败时没有句尾。
* 未覆盖：真实火山 websocket 的时序（重连、CancelSession 竞争）、输出传输真实的队列
  放行节奏与浏览器侧渲染，那些需要真机/真实凭据，这里不假装断言过。

异步一律按本仓库既有写法用 ``asyncio.run(...)`` 驱动（本机没有 pytest-asyncio）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from typing import Any, Callable

import pytest
from pipecat.frames.frames import DataFrame, SystemFrame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection

from app.voice.caption_ledger import VoiceLedgerFrame, VoiceLedgerRelay
from app.voice.embedded_volcengine_tts import (
    EventType,
    Message,
    MsgType,
    MsgTypeFlagBits,
    TTSRequestOptions,
    VolcengineTTSService,
)

CONTEXT_ID = "ctx-1"
# 采样率只在 ``setup()`` 里落地（那一步会连真上游），这里手工补上：字幕游标的毫秒
# 换算依赖它。
SAMPLE_RATE = 24000


class _FakeJournal:
    """按契约 §2.3 分配句身份的 journal 替身（方案 B：分配与记账分离）。"""

    def __init__(
        self,
        *,
        turn_id: str | None = "turn_a",
        no_open_turn: bool = False,
        anchor: float | None = None,
    ):
        self.turn_id = turn_id
        self.no_open_turn = no_open_turn
        # 播放锚点默认放在一小时前：所有排期因此"已到点"，投递立即发生——本文件
        # 测的是账本与 marker 的契约，不是真的等一遍播放。传 None 之外的显式值
        # 才能测排期本身（见 test_delivery_waits_for_the_playback_position）。
        self.anchor = time.monotonic() - 3600.0 if anchor is None else anchor
        self.queued: list[dict[str, Any]] = []
        self.queued_at: list[float] = []
        self.ended: list[dict[str, Any]] = []
        self.errors: list[tuple] = []
        self._seq = 0

    def open_turn_id(self) -> str | None:
        return None if self.no_open_turn else self.turn_id

    def current_turn_id(self) -> str | None:
        return self.open_turn_id()

    def reserve_sentence_seq(self) -> int | None:
        """预分配序号但不记账（真实 journal 的 reserve_sentence_seq）。"""
        if self.open_turn_id() is None:
            return None
        self._seq += 1
        return self._seq

    def playback_anchor_at(self) -> float | None:
        return self.anchor

    def sentence_recorded(self, sequence: int) -> bool:
        return any(item["sentence_seq"] == int(sequence) for item in self.queued)

    async def sentence_queued(
        self,
        text: str,
        *,
        audio_cursor_ms: int | None = None,
        context_id: str | None = None,
        generation_id: int | None = None,
        sequence: int | None = None,
    ) -> dict[str, Any]:
        if self.open_turn_id() is None:
            # 与真实 journal 一致：没有未 finalize 的回合就一个字都不记。
            return {}
        if sequence is None:
            self._seq += 1
            sequence = self._seq
        self.queued.append(
            {
                "text": text,
                "audio_cursor_ms": audio_cursor_ms,
                "context_id": context_id,
                "generation_id": generation_id,
                "sentence_seq": sequence,
            }
        )
        self.queued_at.append(time.monotonic())
        return {
            "turn_id": self.turn_id,
            "sentence_seq": sequence,
            "segment_id": f"{self.turn_id}:s{sequence}",
            "generation_id": generation_id,
            "audio_cursor_ms": audio_cursor_ms,
        }

    async def sentence_ended(
        self,
        *,
        sentence_seq: int,
        audio_end_cursor_ms: int,
        segment_id: str | None = None,
        generation_id: int | None = None,
    ) -> None:
        self.ended.append(
            {
                "sentence_seq": sentence_seq,
                "audio_end_cursor_ms": audio_end_cursor_ms,
                # 与真实 journal 一致：调用方不给 segment_id 时由账本自己拼
                # `{turn_id}:s{seq}`（见 journal.py 的 sentence_ended）。
                "segment_id": segment_id or f"{self.turn_id}:s{sentence_seq}",
                "generation_id": generation_id,
            }
        )

    async def processor_error(self, *args: Any, **kwargs: Any) -> None:
        self.errors.append((args, kwargs))


class _FakeWS:
    """按 session id 现编上游回包的假连接。

    包必须带上 ``_synthesize_sentence`` 自己生成的 session id（每一句都是新的 uuid），
    否则适配器会把这些音频当成上一轮打断的残留而丢弃 —— 那是它的正确行为，测试包错
    id 只会测出"什么都没发生"。

    ``block_when_exhausted`` 用来模拟"上游还开着但没有新包"：音频收干之前不会触发
    结束事件，正是被打断的那一刻。
    """

    def __init__(self, packets: Callable[[str], list[bytes]], *, block_when_exhausted: bool = False):
        self._packets = packets
        self._block = block_when_exhausted
        self._messages: list[bytes] = []

    def bind(self, session_id: str) -> None:
        self._messages = list(self._packets(session_id))

    async def recv(self) -> bytes:
        if not self._messages:
            if self._block:
                await asyncio.sleep(3600)
            raise RuntimeError("upstream closed")
        return self._messages.pop(0)


def _audio_packet(session_id: str, *, audio_bytes: int) -> bytes:
    return Message(
        type=MsgType.AudioOnlyServer,
        flag=MsgTypeFlagBits.WithEvent,
        event=EventType.TTSResponse,
        session_id=session_id,
        payload=b"\x00" * audio_bytes,
    ).marshal()


def _server_event(session_id: str, event: EventType, payload: dict) -> bytes:
    return Message(
        type=MsgType.FullServerResponse,
        flag=MsgTypeFlagBits.WithEvent,
        event=event,
        session_id=session_id,
        payload=json.dumps(payload).encode("utf-8"),
    ).marshal()


def _build_service(
    *,
    journal: _FakeJournal | None,
    generation: int | None = None,
) -> VolcengineTTSService:
    service = VolcengineTTSService(
        api_key="test-key",
        journal=journal,
        generation_source=(lambda: generation) if generation is not None else None,
    )
    service._sample_rate = SAMPLE_RATE
    service._options = TTSRequestOptions(voice_type="test-voice", sample_rate=SAMPLE_RATE)
    return service


def test_acknowledgement_and_answer_contexts_share_one_turn_audio_cursor():
    async def run():
        journal = _FakeJournal(turn_id="turn-a")
        service = _build_service(journal=journal)
        service._audio_cursor_ms = 500
        service._caption_turn_id = "turn-a"
        await service._close_turn_context(None, None)
        assert service._audio_cursor_ms == 500
        starts = []
        async def synthesize(text, context_id, **kwargs):
            starts.append(service._audio_cursor_ms)
            service._audio_cursor_ms += 100
        service._synthesize_sentence = synthesize
        service.create_task = asyncio.create_task
        service._enqueue_sentence("答案。", "answer-context")
        await service._session_chain
        assert starts == [500]
        journal.turn_id = "turn-b"
        service._enqueue_sentence("下一轮。", "next-context")
        await service._session_chain
        assert starts == [500, 0]
        await service.cleanup()
    asyncio.run(run())


def _stub_upstream(service: VolcengineTTSService) -> None:
    """桩掉真实 websocket 的三个动作，但在 StartSession 时把假连接绑到该 session。"""

    async def _ok(*args: Any, **kwargs: Any) -> Any:
        return True

    async def _send_event(event: Any, session_id: str = "", payload: Any = None) -> None:
        ws = service._ws
        if event == EventType.StartSession and hasattr(ws, "bind"):
            ws.bind(str(session_id))

    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    service._ensure_upstream = _ok  # type: ignore[method-assign]
    service._send_event = _send_event  # type: ignore[method-assign]
    service._expect_event = _noop  # type: ignore[method-assign]


def _drained_frames(service: VolcengineTTSService) -> list[Any]:
    frames: list[Any] = []
    queue = service._audio_contexts[CONTEXT_ID]
    while not queue.empty():
        frames.append(queue.get_nowait())
    return frames


def _ledger_frames(frames: list[Any]) -> list[VoiceLedgerFrame]:
    return [frame for frame in frames if isinstance(frame, VoiceLedgerFrame)]


async def _release(frames: list[Any], journal: Any) -> VoiceLedgerRelay:
    """把队列里的账本帧按顺序喂给 relay —— 模拟输出传输"前序音频已写出、帧被放行"。

    这一步在真机上由输出传输的媒体队列自动完成；测试里显式驱动，是为了把"帧还在队列里"
    与"帧已到点"这两个时刻分开断言（前者绝不能写账本）。
    """
    relay = VoiceLedgerRelay(journal=journal)
    for frame in frames:
        if isinstance(frame, (VoiceLedgerFrame, TTSAudioRawFrame)):
            await relay.process_frame(frame, FrameDirection.DOWNSTREAM)
    return relay


async def _run_one_sentence(
    service: VolcengineTTSService,
    text: str,
    *,
    packets: Callable[[str], list[bytes]],
    block_when_exhausted: bool = False,
) -> list[Any]:
    """跑完一句的合成，返回这一句写进 audio context 的全部帧（含音频与账本帧）。

    返回的是"还在队列里"的帧：它们要靠输出传输放行才会触发账本写入，测试用
    ``_release`` 显式完成那一步。
    """
    await service.create_audio_context(CONTEXT_ID)
    service._ws = _FakeWS(packets, block_when_exhausted=block_when_exhausted)
    _stub_upstream(service)
    await service._synthesize_sentence(text, CONTEXT_ID)
    return _drained_frames(service)


def _one_sentence_packets(audio_bytes: int = 4800) -> Callable[[str], list[bytes]]:
    def _build(session_id: str) -> list[bytes]:
        return [
            _audio_packet(session_id, audio_bytes=audio_bytes),
            _server_event(session_id, EventType.SessionFinished, {"status_code": 20000000}),
        ]

    return _build


def _audio_ms(service: VolcengineTTSService, audio_bytes: int) -> int:
    return int(audio_bytes / 2 / service.sample_rate * 1000)


# --------------------------------------------------------- 句首账本帧的载荷


def test_start_frame_carries_the_sentence_and_the_generation():
    """句首账本帧带着这一句的文本与代次，并且排在它自己那句音频**之前**。"""

    async def _run() -> None:
        journal = _FakeJournal(turn_id="turn_a")
        service = _build_service(journal=journal, generation=3)

        frames = await _run_one_sentence(
            service, "第一句。", packets=_one_sentence_packets()
        )

        first = frames[0]
        assert isinstance(first, VoiceLedgerFrame)
        assert first.kind == "start"
        assert first.text == "第一句。"
        assert first.context_id == CONTEXT_ID
        assert first.audio_cursor_ms == 0
        assert first.generation_id == 3
        assert first.token, "句首/句尾靠 token 配对"
        # 句首帧必须排在音频前面：这样它才会在"这一句刚要出声"时被放行。
        assert isinstance(frames[1], TTSAudioRawFrame)
        assert frames[-1].kind == "end"

    asyncio.run(_run())


def test_ledger_frame_fields_are_exactly_the_frozen_set():
    """字段集合冻结：多一个少一个都会让 relay 的判据悄悄失效。"""

    ledger_fields = {field.name for field in dataclasses.fields(VoiceLedgerFrame)} - {
        field.name for field in dataclasses.fields(DataFrame)
    }
    assert ledger_fields == {
        "kind",
        "token",
        "text",
        "turn_id",
        "context_id",
        "audio_cursor_ms",
        "audio_end_cursor_ms",
        "generation_id",
        "turn_final",
    }
    # 必须是普通数据帧：SystemFrame 会被输出传输直接下发、绕过媒体队列，"到点"保证就没了。
    assert not issubclass(VoiceLedgerFrame, SystemFrame)


def test_end_frame_carries_no_text_and_the_sentence_end_cursor():
    async def _run() -> None:
        service = _build_service(journal=_FakeJournal(), generation=2)

        frames = await _run_one_sentence(
            service, "第一句。", packets=_one_sentence_packets()
        )
        end = _ledger_frames(frames)[-1]

        assert end.kind == "end"
        # 句文本只由句首帧认领：句尾再带一份只会给下游两个真源。
        assert end.text == ""
        assert end.audio_end_cursor_ms == _audio_ms(service, 4800)
        assert end.generation_id == 2

    asyncio.run(_run())


# --------------------------------------------- 到点才记账（因果，而不是入队即记）


def test_nothing_is_recorded_while_the_frames_are_still_queued():
    """合成结束时帧还在队列里 —— 此刻账本必须**一个字都没有**。

    这是"未听到的文本不落库"的结构保证：合成远快于播放，入队即记账会让压在执行队列
    里、还没播出来的整段回答提前进账本、进转录、进记忆。
    """

    async def _run() -> None:
        journal = _FakeJournal(turn_id="turn_a")
        service = _build_service(journal=journal, generation=7)

        frames = await _run_one_sentence(
            service, "第一句。", packets=_one_sentence_packets()
        )

        assert _ledger_frames(frames), "帧必须已经入队"
        assert journal.queued == [], "入队不等于播出：账本此刻必须是空的"
        assert journal.ended == []

        await _release(frames, journal)

        assert journal.queued == [
            {
                "text": "第一句。",
                "audio_cursor_ms": 0,
                "context_id": CONTEXT_ID,
                "generation_id": 7,
                "sentence_seq": 1,
            }
        ]
        assert journal.ended == [
            {
                "sentence_seq": 1,
                "audio_end_cursor_ms": _audio_ms(service, 4800),
                "segment_id": "turn_a:s1",
                "generation_id": 7,
            }
        ]

    asyncio.run(_run())


def test_sentence_sequence_is_allocated_at_release_time_and_stays_monotonic():
    """跨句：序号在**释放时刻**按播放顺序分配，游标首尾相接。"""

    async def _run() -> None:
        journal = _FakeJournal(turn_id="turn_a")
        service = _build_service(journal=journal, generation=1)

        first = await _run_one_sentence(
            service, "第一句。", packets=_one_sentence_packets()
        )
        second = await _run_one_sentence(
            service, "第二句。", packets=_one_sentence_packets()
        )
        one_ms = _audio_ms(service, 4800)

        first_frames = _ledger_frames(first)
        second_frames = _ledger_frames(second)
        assert (first_frames[0].audio_cursor_ms, first_frames[1].audio_end_cursor_ms) == (
            0,
            one_ms,
        )
        assert (
            second_frames[0].audio_cursor_ms,
            second_frames[1].audio_end_cursor_ms,
        ) == (one_ms, 2 * one_ms)

        await _release(first, journal)
        await _release(second, journal)

        assert [item["sentence_seq"] for item in journal.queued] == [1, 2]
        assert [item["sentence_seq"] for item in journal.ended] == [1, 2]
        assert [item["segment_id"] for item in journal.ended] == [
            "turn_a:s1",
            "turn_a:s2",
        ]
        assert [item["audio_cursor_ms"] for item in journal.queued] == [0, one_ms]

    asyncio.run(_run())


# --------------------------------------------------------------- 兜底路径


def test_without_journal_the_frames_still_ride_the_queue():
    """没有 journal：账本一个字都不写，但帧照常入队（字幕由官方 bot-output 负责）。"""

    async def _run() -> None:
        frames = await _run_one_sentence(
            _build_service(journal=None, generation=4),
            "第一句。",
            packets=_one_sentence_packets(),
        )
        start = _ledger_frames(frames)[0]
        assert start.generation_id == 4
        # relay 没有账本可写：不崩、也不记。
        await _release(frames, None)

    asyncio.run(_run())


def test_journal_without_an_open_turn_keeps_the_sentence_out_of_the_ledger():
    """账本没有可用回合时，这一句**一个字都不许落库**（句尾也不能凭空出现）。

    帧本身照常走队列——它只是"这一刻该记账"的触发信号，权威判据始终是账本：账本拒绝，
    这一句就既不在事件流里、也不在转录与记忆里。
    """

    async def _run() -> None:
        journal = _FakeJournal(no_open_turn=True)
        service = _build_service(journal=journal, generation=5)

        frames = await _run_one_sentence(
            service, "无人认领的一句。", packets=_one_sentence_packets()
        )
        await _release(frames, journal)

        assert journal.queued == []
        assert journal.ended == [], "句首都没记上，句尾就不许写"

    asyncio.run(_run())


def test_missing_generation_source_reports_none_instead_of_a_fake_number():
    async def _run() -> None:
        frames = await _run_one_sentence(
            _build_service(journal=None, generation=None),
            "第一句。",
            packets=_one_sentence_packets(),
        )
        assert _ledger_frames(frames)[0].generation_id is None

    asyncio.run(_run())


def test_generation_follows_the_gate_between_sentences():
    """闸门换代后，后一句的帧必须带新的代次（前端据此丢弃上一代残留）。"""

    async def _run() -> None:
        state = {"generation": 0}
        service = VolcengineTTSService(
            api_key="test-key",
            journal=_FakeJournal(),
            generation_source=lambda: state["generation"],
        )
        service._sample_rate = SAMPLE_RATE
        service._options = TTSRequestOptions(
            voice_type="test-voice", sample_rate=SAMPLE_RATE
        )

        first = await _run_one_sentence(
            service, "第一句。", packets=_one_sentence_packets()
        )
        state["generation"] = 1
        second = await _run_one_sentence(
            service, "第二句。", packets=_one_sentence_packets()
        )

        assert _ledger_frames(first)[0].generation_id == 0
        assert _ledger_frames(second)[0].generation_id == 1

    asyncio.run(_run())


def test_end_frame_is_only_enqueued_when_the_sentence_completed():
    """合成失败的那一句没有结束偏移：不排句尾帧，也不能让账本以为它播完了。"""

    async def _run() -> None:
        journal = _FakeJournal()
        service = _build_service(journal=journal, generation=0)

        def _packets(session_id: str) -> list[bytes]:
            return [
                _audio_packet(session_id, audio_bytes=2400),
                _server_event(
                    session_id, EventType.SessionFailed, {"status_code": 40000001}
                ),
            ]

        frames = await _run_one_sentence(service, "半句话", packets=_packets)
        await _release(frames, journal)

        assert [frame.kind for frame in _ledger_frames(frames)] == ["start"]
        assert journal.ended == []
        assert journal.errors, "合成失败必须留下一条阶段错误"

    asyncio.run(_run())


def test_interrupted_sentence_enqueues_no_end_frame():
    """被打断的那一句同样没有结束偏移；`sentence_ended` 也不该落。"""

    async def _run() -> None:
        journal = _FakeJournal()
        service = _build_service(journal=journal, generation=0)
        await service.create_audio_context(CONTEXT_ID)
        service._ws = _FakeWS(
            lambda session_id: [_audio_packet(session_id, audio_bytes=2400)],
            block_when_exhausted=True,
        )
        _stub_upstream(service)

        task = asyncio.ensure_future(
            service._synthesize_sentence("被切断的一句", CONTEXT_ID)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        frames = _drained_frames(service)
        assert [frame.kind for frame in _ledger_frames(frames)] == ["start"]
        assert journal.ended == []

    asyncio.run(_run())


def test_sentence_of_an_interrupted_turn_is_dropped_before_any_work():
    """入队代次一过期，整句放弃：不建 session、不写音频、不排账本帧、不进账本。

    实测的坏结果（2026-09-17，真机探针 + 容器日志）：被打断那一句在 3.4 秒后才收到
    首帧音频，而句身份是在"收到首帧"那一刻才铸造的——于是它按**新回合**领了序号 1，
    一句从没播过的旧句子进了字幕与转录（打字那句被记成"由水话题回答"），并可能进
    记忆。代次闸把这条路径整段掐掉：代次在入队时钉住，打断把它 +1。
    """
    journal = _FakeJournal()
    service = _build_service(journal=journal)
    # 模拟"这一句属于上一轮"：它的入队代次已经过期。
    stale_epoch = service._caption_epoch - 1

    async def _run() -> list[Any]:
        await service.create_audio_context(CONTEXT_ID)
        service._ws = _FakeWS(_one_sentence_packets())
        _stub_upstream(service)
        await service._synthesize_sentence("旧句。", CONTEXT_ID, epoch=stale_epoch)
        return _drained_frames(service)

    frames = asyncio.run(_run())

    assert frames == [], "过期句不得产生任何帧（音频与账本帧都不行）"
    assert journal.queued == [], "过期句不得进账本，哪怕它后来真的收到了音频"
    assert journal.ended == [], "过期句没有句尾可言"
