"""全双工语音事件身份与新载荷测试。

覆盖冻结契约：

* §2.1 事件信封提升 ``generation_id`` / ``segment_id`` / ``context_id``（不新增
  数据库列，只在 ``emit`` 时合并进 payload，由信封序列化提升到顶层）。
* §2.2 重放响应新增 ``contiguous_through``：必须是**会话级水位**，不是返回数组
  的最大值（按类型过滤时返回数组会跳号）。
* §2.3 journal 的句身份 API（``current_turn_id`` / ``sentence_queued`` 返回身份 /
  ``sentence_ended`` / ``playback_ack`` / ``heard_text``）、``_spoken_assistant_text``
  的"已听到前缀"语义，以及 ``turn.finalized`` 只带已播报一侧（未播出的余量
  不进载荷）、``playback_ended`` 的 ``turn_final``。
"""

from __future__ import annotations

import asyncio

from app.core.database import SessionLocal
from app.domain.models import VoiceTurnRecord
from app.voice import events as event_api
from app.voice import schemas as schema_api


# --------------------------------------------------------------------- helpers


def _headers_for(register_user, auth_headers, prefix: str):
    token, ws, _u, _p = register_user(prefix)
    return auth_headers(token, ws)


def _make_chat_session(client, headers) -> str:
    resp = client.post("/api/v1/sessions", headers=headers, json={"title": "voice identity"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _create_voice_session(client, headers, chat_session_id: str) -> dict:
    resp = client.post(
        "/api/v1/voice/sessions",
        headers=headers,
        json={"chat_session_id": chat_session_id, "max_thinking_mode": "off"},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _voice_session_for(client, register_user, auth_headers, prefix: str):
    headers = _headers_for(register_user, auth_headers, prefix)
    chat_id = _make_chat_session(client, headers)
    return headers, chat_id, _create_voice_session(client, headers, chat_id)["id"]


def _events(client, headers, voice_id: str, after: int = 0) -> dict:
    resp = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq={after}", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _of_type(body: dict, event_type: str) -> list[dict]:
    return [event for event in body["events"] if event["type"] == event_type]


def _turn_row(voice_id: str) -> VoiceTurnRecord:
    with SessionLocal() as db:
        return (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )


# ------------------------------------------------- §2.2 replay 连续覆盖水位


def test_replay_reports_session_level_contiguous_watermark(
    client, register_user, auth_headers
):
    """``contiguous_through`` 是会话水位：轮到没有事件返回时也不得回退。

    修复前客户端只有 ``last_event_seq``，它既被当成"已确认连续覆盖"又被当成
    "见过的最大序号"，于是被过滤/跳过的中间序号永远补不回来（F05）。水位必须是
    会话级的：只有服务端知道那个序号到底存在不存在。
    """
    headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_watermark"
    )
    for index in range(3):
        resp = client.post(
            f"/api/v1/voice/sessions/{voice_id}/events/user.interim",
            headers=headers,
            json={"text": f"片段{index}", "phase": "speculative"},
        )
        assert resp.status_code == 200, resp.text

    first = _events(client, headers, voice_id)
    seqs = [event["event_seq"] for event in first["events"]]
    assert first["contiguous_through"] == max(seqs) == first["last_event_seq"]
    assert all(event["event_seq"] <= first["contiguous_through"] for event in first["events"])

    # 从水位之后拉：数组是空的，但水位必须保持不变（回退等于宣称"水位之前的
    # 事件不存在"，客户端会把已经拿到的历史重放一遍）。
    tail = _events(client, headers, voice_id, after=first["contiguous_through"])
    assert tail["events"] == []
    assert tail["contiguous_through"] == first["contiguous_through"]


# ------------------------------------------------- §2.1 信封身份提升


def test_event_envelope_promotes_generation_segment_and_context(
    client, register_user, auth_headers
):
    """句身份三件套必须同时出现在顶层信封与 payload 里，且由 emit 合并。"""
    headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_envelope"
    )
    sink = event_api.VoiceEventSink(voice_id)
    envelope = sink.emit(
        "assistant.sentence.queued",
        {"text": "第一句。", "sentence_seq": 1},
        turn_id="turn_identity",
        phase="speculative",
        generation_id=7,
        segment_id="turn_identity:s1",
        context_id="ctx-abc",
    )
    assert envelope["type"] == "assistant.sentence.queued"
    assert envelope["turn_id"] == "turn_identity"
    assert envelope["generation_id"] == 7
    assert envelope["segment_id"] == "turn_identity:s1"
    assert envelope["context_id"] == "ctx-abc"
    assert envelope["payload"]["sentence_seq"] == 1

    # 回放走的是另一条序列化路径（envelope_from_record），身份同样要在顶层。
    replayed = [
        event
        for event in _events(client, headers, voice_id)["events"]
        if event["event_id"] == envelope["event_id"]
    ]
    assert len(replayed) == 1
    assert replayed[0]["generation_id"] == 7
    assert replayed[0]["segment_id"] == "turn_identity:s1"
    assert replayed[0]["context_id"] == "ctx-abc"


def test_event_envelope_omits_absent_identity(client, register_user, auth_headers):
    """没有身份的事件不得凭空多出身份字段，更不得写进 payload。"""
    _headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_no_identity"
    )
    envelope = event_api.VoiceEventSink(voice_id).emit(
        "assistant.llm.delta", {"text": "增量"}, turn_id=None
    )
    assert envelope["generation_id"] is None
    assert envelope["segment_id"] is None
    assert envelope["context_id"] is None
    assert "generation_id" not in envelope["payload"]
    assert "segment_id" not in envelope["payload"]


def test_new_event_types_are_registered_in_both_registries():
    """两张事件类型表必须同步，否则 emit 会拒绝一个已经"合法"的类型。"""
    assert set(event_api.VOICE_EVENT_TYPES) == set(schema_api.VOICE_EVENT_TYPES)
    for event_type in ("assistant.sentence.ended", "assistant.playback.ack"):
        assert event_type in event_api.VOICE_EVENT_TYPES


# ------------------------------------------------- §2.3 journal 句身份


def test_sentence_identity_is_allocated_by_the_journal(
    client, register_user, auth_headers
):
    """``sentence_seq`` / ``segment_id`` 由账本分配并原样返回给快通道 marker。

    TTS 适配器自己数一套序号（旧实现）与账本另数一套，是字幕与持久事件对不上的
    根因：同一句话在两个通道里 seq 不同。这里断言的是"账本给的就是唯一的那个"。
    """
    headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_sentence_identity"
    )
    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        assert journal.current_turn_id() is None, "还没有回合时不得凭空造一个 id"
        await journal.user_final("讲一下并发")
        turn_id = journal.current_turn_id()
        assert turn_id, "回合打开后必须能同步读到持久 turn id"

        first = await journal.sentence_queued(
            "第一句。", audio_cursor_ms=100, context_id="ctx-1", generation_id=2
        )
        assert first == {
            "turn_id": turn_id,
            "sentence_seq": 1,
            "segment_id": f"{turn_id}:s1",
            "generation_id": 2,
            "audio_cursor_ms": 100,
        }
        second = await journal.sentence_queued(
            "第二句。", audio_cursor_ms=400, context_id="ctx-1", generation_id=2
        )
        assert second["sentence_seq"] == 2, "句序号从 1 开始、逐句递增"
        assert second["segment_id"] == f"{turn_id}:s2"
        assert second["audio_cursor_ms"] == 400, "信封游标取回合内累计最大值"

        await journal.sentence_ended(
            sentence_seq=first["sentence_seq"],
            audio_end_cursor_ms=350,
            segment_id=first["segment_id"],
            generation_id=2,
        )
        # 没有确认时，已入队的句子按全文计（老客户端兜底，保留现状）。
        assert journal.heard_text() == "第一句。第二句。"
        await journal.assistant_completed()
        await journal.drain_persistence()

    asyncio.run(_run())

    body = _events(client, headers, voice_id)
    queued = _of_type(body, "assistant.sentence.queued")
    assert [event["payload"]["sentence_seq"] for event in queued] == [1, 2]
    turn_id = _turn_row(voice_id).id
    for event, segment in zip(queued, (f"{turn_id}:s1", f"{turn_id}:s2")):
        assert event["turn_id"] == turn_id, "marker 必须带持久回合 id，而不是 context uuid"
        assert event["payload"]["segment_id"] == segment
        assert event["payload"]["generation_id"] == 2
        assert event["payload"]["context_id"] == "ctx-1"
        assert event["segment_id"] == segment, "句身份同样提升到顶层信封"
        assert event["context_id"] == "ctx-1"

    ended = _of_type(body, "assistant.sentence.ended")
    assert len(ended) == 1
    assert ended[0]["payload"]["sentence_seq"] == 1
    assert ended[0]["payload"]["audio_end_cursor_ms"] == 350
    assert ended[0]["payload"]["text"] == "第一句。"
    assert ended[0]["payload"]["context_id"] == "ctx-1"
    assert ended[0]["segment_id"] == f"{turn_id}:s1"
    assert ended[0]["generation_id"] == 2


def test_current_turn_id_is_none_after_the_turn_is_finalized(
    client, register_user, auth_headers
):
    """收尾之后不得再对外提供一个已经关闭的回合 id。"""
    _headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_turn_closed"
    )
    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        assert journal.current_turn_id() is not None
        await journal.playback_started()
        await journal.assistant_completed()
        await journal.drain_persistence()
        assert journal.current_turn_id() is None

    asyncio.run(_run())


# ------------------------------- §2.3 方案 B：记账必须发生在这一句开始播放时


def test_reserve_and_deferred_recording_keep_unread_text_out_of_the_ledger(
    client, register_user, auth_headers
):
    """方案 B 的账本契约：预分配序号不记账，未投递的句子永远不进账本与库。

    这是"未读内容彻底不入库"的后端落点：
      * ``reserve_sentence_seq()`` 只给号（句尾 marker 要复用它），不写
        ``sentence_texts``、不发事件；
      * 只有投递时刻带 ``sequence=`` 的 ``sentence_queued()`` 才真正记账；
      * 于是 ``heard_text()``（→ ``turn.finalized`` / Message / 记忆）里永远不会出现
        用户没听到的句子——不需要事后裁切，因为那些句子从未被写进来。
    """
    headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_delivery_accounting"
    )
    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("讲两句")
        assert journal.playback_anchor_at() is None, "音频还没开始播放时不得有锚点"
        await journal.playback_started()
        assert journal.playback_anchor_at() is not None, (
            "BotStartedSpeakingFrame 就是本回合的播放锚点"
        )

        first = journal.reserve_sentence_seq()
        second = journal.reserve_sentence_seq()
        assert (first, second) == (1, 2)
        assert journal.heard_text() == "", "预约不是记账：未投递的句子一个字都不许出现"

        recorded = await journal.sentence_queued(
            "第一句。", audio_cursor_ms=0, context_id="ctx-1", sequence=first
        )
        assert recorded["sentence_seq"] == first
        assert journal.heard_text() == "第一句。"

        # 同序号重复投递（重试 / 双通道）：身份原样返回，不重复记账。
        again = await journal.sentence_queued(
            "第一句。", audio_cursor_ms=0, context_id="ctx-1", sequence=first
        )
        assert again["sentence_seq"] == first
        assert journal.heard_text() == "第一句。"

        # 第二句从未投递：它只被预约过，回合收尾时也不得出现。
        await journal.assistant_completed()
        await journal.drain_persistence()

    asyncio.run(_run())

    body = _events(client, headers, voice_id)
    queued = _of_type(body, "assistant.sentence.queued")
    assert [event["payload"]["sentence_seq"] for event in queued] == [1], (
        "未投递的句子不得进入事件流"
    )
    finalized = _of_type(body, "turn.finalized")
    assert finalized[-1]["payload"]["heard_text"] == "第一句。"


def test_playback_anchor_keeps_the_first_speaking_edge(
    client, register_user, auth_headers
):
    """同一回合里输出队列排空又续上时，锚点不得被覆盖——否则整轮排期会前移。"""
    _headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_anchor_first_edge"
    )
    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        await journal.playback_started()
        anchor = journal.playback_anchor_at()
        await asyncio.sleep(0.02)
        await journal.playback_started()
        assert journal.playback_anchor_at() == anchor, "重复的 speaking 沿不得覆盖锚点"

    asyncio.run(_run())


# ------------------------------------- §2.3 playback_ack 与"只带已播报一侧"


def test_playback_ack_records_progress_without_touching_the_turn(
    client, register_user, auth_headers
):
    """播放确认只用于回放位置与审计：既不成全收尾，也不改变回合终态。"""
    headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_ack_only"
    )
    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        await journal.sentence_queued("这是一句很长的回答。", audio_cursor_ms=1_000)
        await journal.playback_started()
        await journal.playback_ack(sentence_seq=1, played_ms=1_500, phase="progress")
        assert journal._turn is not None and not journal._turn.finalized, (
            "播放确认绝不能收尾回合：一条迟到的客户端确认不是完成判据"
        )
        assert journal._turn.acknowledged is True

    asyncio.run(_run())

    assert _turn_row(voice_id).status == "accepted", "确认不得改动回合终态"
    body = _events(client, headers, voice_id)
    acks = _of_type(body, "assistant.playback.ack")
    assert len(acks) == 1
    assert acks[0]["payload"] == {"sentence_seq": 1, "played_ms": 1_500, "phase": "progress"}


def test_heard_prefix_is_persisted_and_the_unheard_remainder_is_not(
    client, register_user, auth_headers
):
    """听到的是前缀，没播完的余量既不落转录、也不进事件载荷。"""
    headers, chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_heard_split"
    )
    from app.voice.journal import VoiceTurnJournal

    generated = "这是一句很长的回答。"

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        journal.note_assistant_text(generated)
        await journal.sentence_queued(generated, audio_cursor_ms=1_000)
        await journal.playback_started()
        await journal.sentence_ended(sentence_seq=1, audio_end_cursor_ms=2_000)
        # 播到句内一半的位置（媒体时钟 1_500ms，本句窗口 1_000→2_000ms）：
        # 10 个字线性插值到 50% = 前 5 个字。
        await journal.playback_ack(sentence_seq=1, played_ms=1_500, phase="progress")
        assert journal.heard_text() == "这是一句很"
        await journal.assistant_completed()
        await journal.drain_persistence()

    asyncio.run(_run())

    row = _turn_row(voice_id)
    assert row.status == "finalized"
    assert row.assistant_text == "这是一句很", "落库的转录只能是用户真的听到的前缀"

    with SessionLocal() as db:
        from app.domain.models import Message

        assistant = (
            db.query(Message)
            .filter(Message.session_id == chat_id, Message.role == "assistant")
            .one()
        )
        assert assistant.content == "这是一句很"

    body = _events(client, headers, voice_id)
    finalized = _of_type(body, "turn.finalized")
    assert len(finalized) == 1, "拆分的载荷必须落在唯一那条 turn.finalized 上"
    payload = finalized[0]["payload"]
    assert payload["heard_text"] == "这是一句很"
    assert "unheard_text" not in payload, "没播出去的余量不上屏也不落载荷"
    assert "generated_text" not in payload, "LLM 原始输出不进载荷"
    assert payload["text"] == "这是一句很"
    assert payload["role"] == "assistant"
    assert payload["acknowledged"] is True
    assert payload["interrupted"] is False
    assert payload["outcome"] == "finalized"
    assert payload["failed"] is False
    assert payload["retryable"] is False
    assert payload["user_text"] == "问题"


def test_unacknowledged_sentence_counts_as_queued_text(client, register_user, auth_headers):
    """没有确认记录的句子按已入队全文计，并把 ``acknowledged: false`` 标出来。

    老客户端（或确认消息丢失）不能因此让整段回答消失——那是比"偏乐观"更坏的
    错误；诚实度靠 ``acknowledged`` 标注，而不是靠猜。
    """
    headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_no_ack"
    )
    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        journal.note_assistant_text("第一句。第二句。")
        await journal.sentence_queued("第一句。")
        await journal.sentence_queued("第二句。")
        await journal.playback_started()
        await journal.assistant_completed()
        await journal.drain_persistence()

    asyncio.run(_run())

    assert _turn_row(voice_id).assistant_text == "第一句。第二句。"
    body = _events(client, headers, voice_id)
    payload = _of_type(body, "turn.finalized")[0]["payload"]
    assert payload["heard_text"] == "第一句。第二句。"
    assert "unheard_text" not in payload
    assert payload["acknowledged"] is False


def test_playback_ended_marks_the_turn_final_and_finalizes(
    client, register_user, auth_headers
):
    """``playback_ended`` 带 ``turn_final: True``：前端只认这个信号收尾（F03）。"""
    headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_turn_final"
    )
    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        journal.note_assistant_text("回答。")
        await journal.sentence_queued("回答。")
        await journal.playback_started()
        await journal.playback_ended()
        await journal.drain_persistence()

    asyncio.run(_run())

    body = _events(client, headers, voice_id)
    ended = _of_type(body, "assistant.sentence.playback_ended")
    assert len(ended) == 1
    assert ended[0]["payload"]["turn_final"] is True
    assert ended[0]["payload"]["sentence_seq"] == 1
    assert len(_of_type(body, "turn.finalized")) == 1
    assert _turn_row(voice_id).status == "finalized"


def test_heard_text_is_empty_without_a_turn(client, register_user, auth_headers):
    """没有回合时 ``heard_text`` 返回空串，而不是抛错或返回上一轮的内容。"""
    _headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_heard_empty"
    )
    from app.voice.journal import VoiceTurnJournal

    journal = VoiceTurnJournal(voice_id)
    assert journal.heard_text() == ""
    assert journal.current_turn_id() is None


def test_unavailable_media_position_is_not_recorded_as_zero(
    client, register_user, auth_headers
):
    """没有句尾游标时，一个位置报告不得被折算成"一个字都没听到"。

    按 0 记会把其实已经说了半句的文本清空——比保守地不记录更糟。只有 ``ended``
    这种明确的完成信号才能在没有窗口的情况下把整句视为已听到。
    """
    _headers, _chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_ack_no_window"
    )
    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        await journal.sentence_queued("没有句尾游标的回答。", audio_cursor_ms=1_000)
        # 没有 sentence_ended：窗口未知，progress 不能被折算。
        await journal.playback_ack(sentence_seq=1, played_ms=1_900, phase="progress")
        assert journal.heard_text() == "没有句尾游标的回答。"
        # ended 是明确的完成信号：整句算已听到。
        await journal.playback_ack(sentence_seq=1, played_ms=2_500, phase="ended")
        assert journal.heard_text() == "没有句尾游标的回答。"
        # ack 里出现的未知句序号不得凭空造出一句文本。
        await journal.playback_ack(sentence_seq=99, played_ms=9_999, phase="ended")
        assert journal.heard_text() == "没有句尾游标的回答。"
        await journal.assistant_completed()
        await journal.drain_persistence()

    asyncio.run(_run())

    assert _turn_row(voice_id).assistant_text == "没有句尾游标的回答。"


def test_no_playback_fails_without_persisting_generated_text(
    client, register_user, auth_headers
):
    """TTS 完全没出声时，草稿不上屏、不进事件表、也不写助手消息。"""
    headers, chat_id, voice_id = _voice_session_for(
        client, register_user, auth_headers, "voice_no_playback_failure"
    )
    from app.voice.journal import VoiceTurnJournal
    from app.voice.turns import TURN_FAILED

    generated = "TTS 没有出声。"

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        journal.note_assistant_text(generated)
        # Legacy callers may still invoke this method; it must remain a no-op.
        await journal.assistant_delta(generated, force=True)
        await journal.assistant_completed(
            outcome=TURN_FAILED,
            failure_reason="no_playback_frames",
            degraded_reason="no_playback_frames",
        )
        await journal.drain_persistence()

    asyncio.run(_run())

    row = _turn_row(voice_id)
    assert row.status == TURN_FAILED
    assert not row.assistant_text

    with SessionLocal() as db:
        from app.domain.models import Message

        assistants = (
            db.query(Message)
            .filter(Message.session_id == chat_id, Message.role == "assistant")
            .all()
        )
        assert assistants == []

    body = _events(client, headers, voice_id)
    assert _of_type(body, "assistant.llm.delta") == []
    finalized = _of_type(body, "turn.finalized")
    assert len(finalized) == 1
    payload = finalized[0]["payload"]
    assert payload["text"] == ""
    assert payload["heard_text"] == ""
    assert payload["outcome"] == TURN_FAILED
    assert payload["failed"] is True
    assert payload["retryable"] is True

    errors = _of_type(body, "processor.error")
    assert errors[0]["payload"]["stage"] == "tts"
    assert errors[0]["payload"]["retryable"] is True
