"""账本帧 + relay 与持久事件必须是同一套句身份（契约 §2.4 × §2.3 的接缝）。

F02/F04/F05 的共同根因就在这里：适配器自己数一套序号、账本另数一套，同一条流上的
两个通道对不上。单元测试证明"帧一被放行就按账本分配的身份记账"，这一份用**真实账本 +
真实数据库**证明账本返回的身份与落库的身份确实是同一份 —— 账本帧与
``assistant.sentence.queued`` / ``assistant.sentence.ended`` 的
``(turn_id, sentence_seq, segment_id, context_id, generation_id)`` 必须逐字段相等。

与旧版的区别（阶段 1）：记账时刻不再依赖播放锚点 + ``asyncio.sleep`` 的换算，而是
**因果**的——帧排在那句音频前面/后面，被输出传输放行时记账。这份测试因此不再需要
``journal.playback_started()`` 造锚点，它显式驱动 relay 来完成"放行"这一步。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pipecat.processors.frame_processor import FrameDirection

from app.core.database import SessionLocal
from app.domain.models import VoiceEventRecord
from app.voice.caption_ledger import VoiceLedgerFrame, VoiceLedgerRelay
from app.voice.embedded_volcengine_tts import (
    EventType,
    Message,
    MsgType,
    MsgTypeFlagBits,
    TTSRequestOptions,
    VolcengineTTSService,
)
from app.voice.journal import VoiceTurnJournal

CONTEXT_ID = "ctx-journal"
SAMPLE_RATE = 24000
GENERATION = 4
AUDIO_BYTES = 4800


def _make_chat_session(client, headers) -> str:
    resp = client.post("/api/v1/sessions", headers=headers, json={"title": "tts ledger"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _create_voice_session(client, headers, chat_session_id: str) -> str:
    resp = client.post(
        "/api/v1/voice/sessions",
        headers=headers,
        json={"chat_session_id": chat_session_id, "max_thinking_mode": "off"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


class _FakeWS:
    """按 ``_synthesize_sentence`` 自己生成的 session id 现编上游回包。"""

    def __init__(self, audio_bytes: int = AUDIO_BYTES) -> None:
        self._audio_bytes = audio_bytes
        self._messages: list[bytes] = []

    def bind(self, session_id: str) -> None:
        self._messages = [
            Message(
                type=MsgType.AudioOnlyServer,
                flag=MsgTypeFlagBits.WithEvent,
                event=EventType.TTSResponse,
                session_id=session_id,
                payload=b"\x00" * self._audio_bytes,
            ).marshal(),
            Message(
                type=MsgType.FullServerResponse,
                flag=MsgTypeFlagBits.WithEvent,
                event=EventType.SessionFinished,
                session_id=session_id,
                payload=b'{"status_code": 20000000}',
            ).marshal(),
        ]

    async def recv(self) -> bytes:
        await asyncio.sleep(0)
        if not self._messages:
            raise RuntimeError("upstream closed")
        return self._messages.pop(0)


def _stub_upstream(service: VolcengineTTSService) -> None:
    async def _ok(*args: Any, **kwargs: Any) -> Any:
        return True

    async def _send_event(event: Any, session_id: str = "", payload: Any = None) -> None:
        ws = service._ws
        if event == EventType.StartSession and hasattr(ws, "bind"):
            ws.bind(str(session_id))
        if event == EventType.TaskRequest:
            ws._messages.insert(-1, Message(type=MsgType.FullServerResponse, flag=MsgTypeFlagBits.WithEvent,
                event=EventType.TTSSubtitle, session_id=session_id,
                payload=json.dumps({"text": payload["req_params"]["text"], "words": [{"startTime": 0,
                    "endTime": ws._audio_bytes / 2 / SAMPLE_RATE}]}).encode()).marshal())

    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    service._ensure_upstream = _ok  # type: ignore[method-assign]
    service._send_event = _send_event  # type: ignore[method-assign]
    service._expect_event = _noop  # type: ignore[method-assign]


def _drain(service: VolcengineTTSService) -> list[Any]:
    queue = service._audio_contexts[CONTEXT_ID]
    frames: list[Any] = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    return frames


def _ledger_frames(frames: list[Any]) -> list[VoiceLedgerFrame]:
    return [frame for frame in frames if isinstance(frame, VoiceLedgerFrame)]


async def _release(frames: list[Any], journal: VoiceTurnJournal) -> None:
    """模拟输出传输的放行：帧被推出媒体队列时才真正记账。"""
    relay = VoiceLedgerRelay(journal=journal)
    for frame in frames:
        # The start marker is released only when the first audio frame reaches
        # the downstream relay, so exercise the complete media order here.
        await relay.process_frame(frame, FrameDirection.DOWNSTREAM)


def test_tts_ledger_frames_and_durable_events_share_one_sentence_identity(
    client, register_user, auth_headers
):
    token, workspace_id, _username, _password = register_user("voice_marker")
    headers = auth_headers(token, workspace_id)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)

    async def _run() -> tuple[str | None, list[Any]]:
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("请用两句话回答")
        journal.note_assistant_text("第一句。第二句。")
        turn_id = journal.current_turn_id()

        service = VolcengineTTSService(
            api_key="test-key",
            journal=journal,
            generation_source=lambda: GENERATION,
        )
        service._sample_rate = SAMPLE_RATE
        service._options = TTSRequestOptions(
            voice_type="test-voice", sample_rate=SAMPLE_RATE
        )
        await service.create_audio_context(CONTEXT_ID)
        service._ws = _FakeWS()
        _stub_upstream(service)

        for sentence in ("第一句。", "第二句。"):
            await service._synthesize_sentence(sentence, CONTEXT_ID)

        frames = _drain(service)
        # 帧还只是"排队中"：这一步之前账本必须一个字都没有（因果记账的核心断言）。
        assert not [
            row
            for row in _query_events(voice_id)
            if row.event_type.startswith("assistant.sentence.")
        ], "入队不等于播出：帧还在队列里时不该有任何句子事件"
        await _release(frames, journal)
        # Playback events are persisted through the journal's FIFO write tail so
        # the media path never waits on SQL. Drain it before inspecting the
        # durable event log; the transport boundary has already been exercised.
        await journal.drain_persistence()
        return turn_id, _ledger_frames(frames)

    turn_id, ledger = asyncio.run(_run())

    assert turn_id, "user_final 必须开出一个可归属的回合"
    assert [frame.kind for frame in ledger] == ["start", "end", "start", "end"]
    starts = ledger[0::2]
    ends = ledger[1::2]
    assert [frame.text for frame in starts] == ["第一句。", "第二句。"]
    assert [frame.context_id for frame in starts] == [CONTEXT_ID, CONTEXT_ID]
    assert [frame.generation_id for frame in starts] == [GENERATION, GENERATION]
    # 每一句都是"起点 < 终点"的有界窗口：前端只能按这个窗口插值句内位置。
    one_ms = int(AUDIO_BYTES / 2 / SAMPLE_RATE * 1000)
    assert starts[0].audio_cursor_ms == 0
    assert ends[0].audio_end_cursor_ms == starts[1].audio_cursor_ms == one_ms
    assert ends[1].audio_end_cursor_ms == 2 * one_ms

    rows = _query_events(voice_id)
    queued = [row for row in rows if row.event_type == "assistant.sentence.queued"]
    ended = [row for row in rows if row.event_type == "assistant.sentence.ended"]

    assert len(queued) == 2 and len(ended) == 2
    # 持久事件的句身份由账本在放行时刻分配；释放顺序 = 播放顺序。
    assert [row.payload["sentence_seq"] for row in queued] == [1, 2]
    assert [row.payload["segment_id"] for row in queued] == [
        f"{turn_id}:s1",
        f"{turn_id}:s2",
    ]
    assert [row.turn_id for row in queued] == [turn_id, turn_id]
    assert [row.payload["context_id"] for row in queued] == [CONTEXT_ID, CONTEXT_ID]
    assert [row.payload["generation_id"] for row in queued] == [GENERATION, GENERATION]
    assert [row.payload["text"] for row in queued] == ["第一句。", "第二句。"]
    assert [row.payload["segment_id"] for row in ended] == [
        f"{turn_id}:s1",
        f"{turn_id}:s2",
    ]
    assert [row.payload["audio_end_cursor_ms"] for row in ended] == [
        frame.audio_end_cursor_ms for frame in ends
    ]


def _query_events(voice_id: str) -> list[VoiceEventRecord]:
    with SessionLocal() as db:
        return (
            db.query(VoiceEventRecord)
            .filter(VoiceEventRecord.voice_session_id == voice_id)
            .order_by(VoiceEventRecord.event_seq)
            .all()
        )


def test_foreground_activity_survives_real_event_validation_and_replay(
    client, register_user, auth_headers
):
    token, workspace_id, *_ = register_user("voice_activity")
    headers = auth_headers(token, workspace_id)
    voice_id = _create_voice_session(client, headers, _make_chat_session(client, headers))

    async def run() -> str:
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("请在后台整理表格")
        turn_id = journal.current_turn_id()
        assert turn_id
        await journal.assistant_activity("thinking", turn_id=turn_id)
        await journal.assistant_activity("thinking", turn_id=turn_id)
        await journal.assistant_activity("delegating", turn_id="old-turn")
        await journal.assistant_activity("delegating", turn_id=turn_id)
        await journal.assistant_activity("idle", turn_id=turn_id)
        await journal.drain_persistence()
        await journal.close_open_turns_on_session_end()
        return turn_id

    turn_id = asyncio.run(run())
    rows = [row for row in _query_events(voice_id) if row.event_type == "assistant.activity"]
    assert [row.payload["state"] for row in rows] == ["thinking", "delegating", "idle"]
    assert all(row.turn_id == turn_id for row in rows)
    replay = client.get(f"/api/v1/voice/sessions/{voice_id}/events", headers=headers)
    assert replay.status_code == 200, replay.text
    activity = [item for item in replay.json()["events"] if item["type"] == "assistant.activity"]
    assert [item["payload"]["state"] for item in activity] == ["thinking", "delegating", "idle"]
    from app.voice.events import VOICE_EVENT_TYPES as persisted_types
    from app.voice.schemas import VOICE_EVENT_TYPES as wire_types
    assert set(persisted_types) == set(wire_types)
