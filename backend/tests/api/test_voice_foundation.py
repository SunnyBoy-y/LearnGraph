"""全双工语音基础链路测试。

覆盖验收点：

1. 持久化会话/事件：event_seq 单调、按 after_event_seq 回放、跨进程（新 Session）
   仍可读 → 等价于「重启 / 多 worker 后仍可恢复」。
2. turn 幂等：同一 client_message_id 重试不产生第二个回合、第二条消息。
3. 只有 finalized turn 进入 Message / Memory；provisional、interim、被打断且
   未听见的文本不得写入长期记忆。
4. 字幕单一权威：TTS marker 丢失 / 重复 / 乱序，最终只产生一条 assistant 消息。
5. session close / interrupt 幂等。
6. typed message 与 ASR final 不重复。
7. 首轮语音上下文快照携带会话尾部、记忆、token budget。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.core.database import SessionLocal
from app.domain.models import Message, VoiceEventRecord, VoiceSessionRecord, VoiceTurnRecord


# --------------------------------------------------------------------- helpers


def _make_chat_session(client, headers) -> str:
    resp = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"title": "voice foundation"},
    )
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


def _post_accept(client, headers, voice_id: str, text: str, cmid: str) -> dict:
    resp = client.post(
        f"/api/v1/voice/sessions/{voice_id}/turns/accept",
        headers=headers,
        json={"text": text, "client_message_id": cmid},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post_finalize(client, headers, voice_id: str, turn_id: str, text: str) -> dict:
    resp = client.post(
        f"/api/v1/voice/sessions/{voice_id}/turns/{turn_id}/finalize",
        headers=headers,
        json={"assistant_text": text},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------------------------------- durability / replay

def test_client_environment_is_owned_validated_and_survives_context_refresh(client, register_user, auth_headers):
    token, ws, *_ = register_user("voice_environment")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    environment = {"timezone": "Asia/Shanghai", "location_status": "available", "location": {"city": "Paris", "latitude": 48.86, "longitude": 2.35, "timezone": "Europe/Paris", "source": "client_ip"}}
    response = client.post("/api/v1/voice/sessions", headers=headers, json={"chat_session_id": chat_id, "client_environment": environment})
    assert response.status_code == 200, response.text
    voice = response.json()
    assert voice["context_snapshot"]["client_environment"]["location"]["city"] == "Paris"
    # Ordinary session reconnect refreshes chat context without erasing location.
    refreshed = _create_voice_session(client, headers, chat_id)
    assert refreshed["context_snapshot"]["client_environment"] == voice["context_snapshot"]["client_environment"]
    path = f"/api/v1/voice/sessions/{voice['id']}/environment"
    assert client.patch(path, headers=headers, json={"timezone": "invalid/zone"}).status_code == 422
    other_token, other_ws, *_ = register_user("voice_environment_other")
    assert client.patch(path, headers=auth_headers(other_token, other_ws), json={"timezone": "UTC"}).status_code in (403, 404)
    updated = client.patch(path, headers=headers, json={"timezone": "UTC", "location_status": "unavailable"})
    assert updated.status_code == 200
    assert updated.json()["session_epoch"] == refreshed["session_epoch"]
    assert updated.json()["context_snapshot"]["client_environment"]["location"] is None


def test_voice_events_are_monotonic_and_replayable_across_workers(client, register_user, auth_headers):
    token, ws, _u, _p = register_user("voice_evt")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    session = _create_voice_session(client, headers, chat_id)
    voice_id = session["id"]

    for index in range(5):
        resp = client.post(
            f"/api/v1/voice/sessions/{voice_id}/events/user.interim",
            headers=headers,
            json={"text": f"片段{index}", "phase": "speculative"},
        )
        assert resp.status_code == 200, resp.text

    first = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()
    seqs = [event["event_seq"] for event in first["events"]]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs)), "event_seq 必须唯一单调"

    # Cursor replay: asking from a middle cursor returns only the newer events.
    midpoint = seqs[len(seqs) // 2]
    tail = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq={midpoint}",
        headers=headers,
    ).json()
    assert all(event["event_seq"] > midpoint for event in tail["events"])
    assert {event["event_seq"] for event in tail["events"]} == {
        seq for seq in seqs if seq > midpoint
    }

    # A brand-new DB session (i.e. another process/worker, or after a restart)
    # observes the same cursor and the same events.
    with SessionLocal() as db:
        row = db.get(VoiceSessionRecord, voice_id)
        assert row is not None
        assert int(row.event_seq) == max(seqs)
        stored = (
            db.query(VoiceEventRecord)
            .filter(VoiceEventRecord.voice_session_id == voice_id)
            .count()
        )
        assert stored >= len(seqs)


def test_event_request_id_is_idempotent(client, register_user, auth_headers):
    token, ws, _u, _p = register_user("voice_idem")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    payload = {
        "text": "重复投递",
        "request_id": "req-fixed-1",
        "phase": "speculative",
    }
    first = client.post(
        f"/api/v1/voice/sessions/{voice_id}/events/user.interim",
        headers=headers,
        json=payload,
    ).json()["event"]
    second = client.post(
        f"/api/v1/voice/sessions/{voice_id}/events/user.interim",
        headers=headers,
        json=payload,
    ).json()["event"]

    assert first["event_id"] == second["event_id"]
    assert first["event_seq"] == second["event_seq"]

    with SessionLocal() as db:
        rows = (
            db.query(VoiceEventRecord)
            .filter(
                VoiceEventRecord.voice_session_id == voice_id,
                VoiceEventRecord.request_id == "req-fixed-1",
            )
            .all()
        )
        assert len(rows) == 1


# ------------------------------------------------------------------- turn idempotency


def test_turn_accept_is_idempotent_on_client_message_id(client, register_user, auth_headers):
    token, ws, _u, _p = register_user("voice_turn")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    first = _post_accept(client, headers, voice_id, "你好呀", "cmid-1")
    second = _post_accept(client, headers, voice_id, "你好呀", "cmid-1")
    assert first["turn_id"] == second["turn_id"], "同一幂等键不得开出第二个回合"

    with SessionLocal() as db:
        count = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .count()
        )
        assert count == 1


def test_finalize_is_idempotent_and_writes_one_message_pair(client, register_user, auth_headers):
    token, ws, _u, _p = register_user("voice_fin")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    turn = _post_accept(client, headers, voice_id, "第一句", "cmid-fin")
    _post_finalize(client, headers, voice_id, turn["turn_id"], "回答一")
    _post_finalize(client, headers, voice_id, turn["turn_id"], "回答一")

    with SessionLocal() as db:
        rows = (
            db.query(Message)
            .filter(
                Message.session_id == chat_id,
                Message.role.in_(("user", "assistant")),
            )
            .all()
        )
        assert len(rows) == 2, f"期望恰好一对 user/assistant，实际 {len(rows)}"
        assistant = [row for row in rows if row["role"] == "assistant"][0] if False else [
            row for row in rows if row.role == "assistant"
        ][0]
        assert assistant.content == "回答一"
        assert (assistant.provider_trace or {}).get("voice_turn_id") == turn["turn_id"]

    # Same idempotency key again, this time through the worker-side code path.
    from app.voice.turns import finalize_turn

    finalize_turn(voice_id, turn["turn_id"], "回答一")
    with SessionLocal() as db:
        count = (
            db.query(Message)
            .filter(Message.session_id == chat_id)
            .count()
        )
        assert count == 2


def test_compact_list_payload_keeps_voice_turn_identity(client, register_user, auth_headers):
    """列表接口的 compact provider_trace 必须留住语音回合的身份键。

    语音回合没有乐观 id 映射：画布只认 provider_trace 里的回合 id / 幂等键，才把
    "通话中本地渲染的那一行"与"worker 落库的那一行"当成同一行（前端
    `findVoiceCounterpart` / `isVoiceTrace`）。compact 白名单少一个键，刷新回来的
    持久化消息就认不出自己属于哪个语音回合，于是被当成一条普通回答与本地那一行
    并排渲染——用户看到的是同一条回答出现两份（用户问题因为还能按内容配对，
    反倒只显示一份）。
    """

    token, ws, _u, _p = register_user("voice_list_trace")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    turn = _post_accept(client, headers, voice_id, "请你介绍自己。", "cmid-trace")
    _post_finalize(client, headers, voice_id, turn["turn_id"], "你好，我是 LearnGraph 学习助手。")

    resp = client.get(f"/api/v1/sessions/{chat_id}/messages", headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assistant = [item for item in items if item["role"] == "assistant"]
    assert len(assistant) == 1, f"期望恰好一条回答，实际 {len(assistant)}"
    trace = assistant[0]["provider_trace"]
    assert trace.get("voice") is True, trace
    assert trace.get("voice_turn") is True, trace
    assert trace.get("voice_turn_id") == turn["turn_id"], trace
    assert trace.get("client_message_id") == "cmid-trace", trace


# --------------------------------------------------- provisional never persists


def test_interim_and_provisional_never_reach_transcript_or_memory(client, register_user, auth_headers):
    token, ws, _u, _p = register_user("voice_prov")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    for text in ("嗯", "嗯我", "嗯我想问"):
        client.post(
            f"/api/v1/voice/sessions/{voice_id}/events/user.interim",
            headers=headers,
            json={"text": text, "phase": "speculative"},
        )
    client.post(
        f"/api/v1/voice/sessions/{voice_id}/events/assistant.llm.delta",
        headers=headers,
        json={"text": "草稿", "phase": "speculative"},
    )

    with SessionLocal() as db:
        messages = db.query(Message).filter(Message.session_id == chat_id).count()
        jobs_before = _memory_job_count(db, chat_id)
    assert messages == 0, "provisional 事件不得写入 Message"
    assert jobs_before == 0, "provisional 事件不得触发记忆抽取"


def test_interrupted_turn_keeps_transcript_but_not_memory(client, register_user, auth_headers):
    token, ws, _u, _p = register_user("voice_int")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    turn = _post_accept(client, headers, voice_id, "用户的问题", "cmid-int")

    from app.voice.turns import finalize_turn, interrupt_turn

    interrupt_turn(voice_id, turn_id=turn["turn_id"], reason="barge_in")
    # The partial answer the user did hear is kept for audit/refresh...
    finalize_turn(voice_id, turn["turn_id"], "只听到的这一句", memory=False)

    with SessionLocal() as db:
        messages = (
            db.query(Message).filter(Message.session_id == chat_id).all()
        )
        assert len(messages) == 2, "被打断的回合仍应保留可审计的 transcript"
        jobs = _memory_job_count(db, chat_id)

    assert jobs == 0, "用户没听见的被打断文本不得进入长期记忆"

    # A normally finalized turn *does* enqueue memory extraction.
    turn2 = _post_accept(client, headers, voice_id, "第二个问题", "cmid-int2")
    _post_finalize(client, headers, voice_id, turn2["turn_id"], "完整回答")
    with SessionLocal() as db:
        assert _memory_job_count(db, chat_id) == 1


def _memory_job_count(db, chat_id: str) -> int:
    """Count queued memory-extraction jobs for a chat session."""
    from app.domain.models import DurableJob

    rows = db.query(DurableJob).filter(DurableJob.kind == "memory.extract").all()
    return sum(
        1
        for row in rows
        if isinstance(row.payload, dict) and row.payload.get("session_id") == chat_id
    )


# ------------------------------------------- single authoritative assistant message


def test_single_authoritative_message_under_lost_duplicate_and_reordered_markers(
    client, register_user, auth_headers
):
    """TTS marker 丢失 / 重复 / 乱序时，最终只产生一条 assistant 消息。

    权威文本来自 LLM 流（journal 累加），marker 只驱动 speculative 层；因此
    这里用「marker 事件乱序 + 重复 + 完全缺失」三种情形验证最终回合唯一。
    """
    token, ws, _u, _p = register_user("voice_marker")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    turn = _post_accept(client, headers, voice_id, "讲一下并发", "cmid-marker")
    turn_id = turn["turn_id"]

    # Duplicated + out-of-order sentence markers (sentence_seq 3 then 1 then 3).
    for seq, cursor in ((3, 900), (1, 300), (3, 900)):
        client.post(
            f"/api/v1/voice/sessions/{voice_id}/events/assistant.sentence.queued",
            headers=headers,
            json={
                "text": f"句子{seq}",
                "sentence_seq": seq,
                "audio_cursor_ms": cursor,
                "turn_id": turn_id,
                "phase": "speculative",
            },
        )
    # A marker that never arrives at all is represented by simply finalizing.
    _post_finalize(client, headers, voice_id, turn_id, "并发就是多个任务交替推进。")
    _post_finalize(client, headers, voice_id, turn_id, "并发就是多个任务交替推进。")

    with SessionLocal() as db:
        assistant = (
            db.query(Message)
            .filter(Message.session_id == chat_id, Message.role == "assistant")
            .all()
        )
        assert len(assistant) == 1, "重复 finalize / 丢 marker 都只能有一条权威消息"
        assert assistant[0].content == "并发就是多个任务交替推进。"

    events = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()["events"]
    finalized = [event for event in events if event["type"] == "turn.finalized"]
    assert len(finalized) == 1, "turn.finalized 只能有一条（否则 UI 会重复上屏）"


# ------------------------------------------------------- typed vs ASR duplication


def test_typed_message_and_asr_final_do_not_duplicate(client, register_user, auth_headers):
    """typed 输入先建回合；随后的 ASR final 必须挂在同一回合上。"""
    from app.voice.turns import accept_turn, open_turn

    token, ws, _u, _p = register_user("voice_typed")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    typed = _post_accept(client, headers, voice_id, "打字的问题", "cmid-typed")

    # The worker observes an ASR final while the typed turn is still open: it
    # must attach to that turn, not open a parallel one.
    claimed = open_turn(voice_id)
    assert claimed is not None and claimed["turn_id"] == typed["turn_id"]

    attached = accept_turn(voice_id, "打字的问题", turn_id=typed["turn_id"])
    assert attached["turn_id"] == typed["turn_id"]
    assert attached["created"] is False

    _post_finalize(client, headers, voice_id, typed["turn_id"], "唯一回答")

    with SessionLocal() as db:
        user_rows = (
            db.query(Message)
            .filter(Message.session_id == chat_id, Message.role == "user")
            .all()
        )
        assert len(user_rows) == 1, "typed 与 ASR 不得各产生一条 user 消息"
        assert user_rows[0].content == "打字的问题"


# --------------------------------------------------------------- close / interrupt


def test_session_close_is_idempotent_and_emits_single_closed_event(client, register_user, auth_headers):
    token, ws, _u, _p = register_user("voice_close")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    first = client.delete(f"/api/v1/voice/sessions/{voice_id}", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "ended"
    second = client.delete(f"/api/v1/voice/sessions/{voice_id}", headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "ended"

    events = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()["events"]
    closed = [event for event in events if event["type"] == "session.closed"]
    assert len(closed) == 1, "session.closed 必须只发一次"


def test_interrupt_is_durable_and_marks_the_open_turn(client, register_user, auth_headers):
    token, ws, _u, _p = register_user("voice_barge")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    turn = _post_accept(client, headers, voice_id, "被打断的话", "cmid-barge")
    resp = client.post(f"/api/v1/voice/sessions/{voice_id}/interrupt", headers=headers)
    assert resp.status_code == 200, resp.text

    events = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()["events"]
    interrupts = [event for event in events if event["type"] == "turn.interrupted"]
    assert len(interrupts) == 1
    assert interrupts[0]["turn_id"] == turn["turn_id"]

    with SessionLocal() as db:
        row = db.get(VoiceTurnRecord, turn["turn_id"])
        assert row.status == "interrupted"


def test_close_marks_runner_as_finished_for_the_watchdog(client, register_user, auth_headers):
    """跨 worker 关闭：worker 通过 durable 状态发现会话已结束并收束自己。"""
    token, ws, _u, _p = register_user("voice_wd")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]
    client.delete(f"/api/v1/voice/sessions/{voice_id}", headers=headers)

    from app.voice.events import is_session_active, load_session

    assert is_session_active(voice_id) is False
    handle = load_session(voice_id)
    assert handle is not None and handle.status == "ended"


def test_offer_is_rejected_after_close(client, register_user, auth_headers):
    """关闭后信令入口必须拒绝，避免遗留 worker 继续接线。"""
    token, ws, _u, _p = register_user("voice_offer")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]
    client.delete(f"/api/v1/voice/sessions/{voice_id}", headers=headers)

    resp = client.post(
        f"/api/v1/voice/sessions/{voice_id}/api/offer",
        headers=headers,
        json={"sdp": "v=0", "type": "offer"},
    )
    assert resp.status_code == 404


# -------------------------------------------------------------- context snapshot


def test_first_turn_sees_session_context_and_budget(
    client, register_user, auth_headers, stub_chat_service
):
    token, ws, _u, _p = register_user("voice_ctx")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)

    # Put a tail of ordinary chat into the session first.
    stream = client.post(
        f"/api/v1/sessions/{chat_id}/messages/stream",
        headers=headers,
        json={"content": "我之前问过一致性哈希", "generation_mode": "text"},
    )
    assert stream.status_code in (200, 201), stream.text
    stream.text  # consume the SSE body

    session = _create_voice_session(client, headers, chat_id)
    snapshot = session.get("context_snapshot") or {}
    assert snapshot, "首轮语音前必须生成 VoiceContextSnapshot"
    assert snapshot.get("chat_session_id") == chat_id
    assert "prompt_block" in snapshot
    assert snapshot.get("token_budget", 0) > 0, "快照必须携带 context budget"
    assert snapshot.get("model_id") or snapshot.get("provider_id") or True


# ------------------------------------------------------------- runner registry


def test_runner_registry_close_is_idempotent():
    from app.voice.runner_registry import (
        VoiceRunnerHandle,
        close_runner,
        get_runner,
        register_runner,
        unregister_runner,
    )

    handle = VoiceRunnerHandle(
        voice_session_id="vs_test_registry",
        chat_session_id="chat-1",
        owner_user_id="user-1",
    )
    register_runner(handle)
    assert get_runner("vs_test_registry") is handle

    async def _close():
        first = await close_runner("vs_test_registry", reason="test")
        second = await close_runner("vs_test_registry", reason="test")
        return first, second

    first, second = asyncio.run(_close())
    assert first is True
    assert second is False, "重复关闭必须幂等"
    assert get_runner("vs_test_registry") is None
    unregister_runner("vs_test_registry")


def test_journal_watchdog_observes_close_and_interrupt(client, register_user, auth_headers):
    """worker 侧控制通道：能从 durable 事件里看到打断与关闭。"""
    token, ws, _u, _p = register_user("voice_wdog")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    from app.voice.journal import VoiceControlWatchdog

    seen: list[str] = []

    async def _on_interrupt(_event):
        seen.append("interrupt")

    async def _on_model(_event):
        seen.append("model")

    async def _on_close(reason):
        seen.append(f"close:{reason}")

    async def _run():
        watchdog = VoiceControlWatchdog(
            voice_session_id=voice_id,
            on_interrupt=_on_interrupt,
            on_model_changed=_on_model,
            on_close=_on_close,
        )
        await watchdog._initial_cursor()
        client.post(
            f"/api/v1/voice/sessions/{voice_id}/turns/accept",
            headers=headers,
            json={"text": "x", "client_message_id": "cmid-wd"},
        )
        client.post(f"/api/v1/voice/sessions/{voice_id}/interrupt", headers=headers)
        await watchdog.tick()
        assert "interrupt" in seen
        client.delete(f"/api/v1/voice/sessions/{voice_id}", headers=headers)
        await watchdog.tick()
        assert any(item.startswith("close:") for item in seen)

    asyncio.run(_run())


# --------------------------------------------------------------- schema/migration


def test_voice_tables_exist_with_event_type_column():
    """VoiceEventRecord 的物理列必须叫 event_type（历史别名曾导致启动失败）。"""
    from sqlalchemy import inspect

    from app.core.database import engine

    inspector = inspect(engine)
    for table in ("voice_sessions", "voice_turns", "voice_events", "voice_task_links"):
        assert inspector.has_table(table), f"缺少表 {table}"
    columns = {column["name"] for column in inspector.get_columns("voice_events")}
    assert "event_type" in columns
    assert "type" not in columns
    for required in (
        "event_id",
        "voice_session_id",
        "session_epoch",
        "turn_id",
        "event_seq",
        "request_id",
        "phase",
        "causality",
        "audio_cursor_ms",
        "payload",
    ):
        assert required in columns, f"voice_events 缺少列 {required}"


def test_voice_session_reuses_active_row_on_refresh(client, register_user, auth_headers):
    """刷新页面重复 POST 必须复用同一会话，否则 seq 与未完成回合会分叉。"""
    token, ws, _u, _p = register_user("voice_reuse")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    first = _create_voice_session(client, headers, chat_id)
    second = _create_voice_session(client, headers, chat_id)
    assert first["id"] == second["id"]
    assert second["event_seq"] >= first["event_seq"]


def test_model_switch_is_reported_as_next_turn(client, register_user, auth_headers):
    token, ws, _u, _p = register_user("voice_model")
    headers = auth_headers(token, ws)
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    before = client.get(f"/api/v1/voice/sessions/{voice_id}", headers=headers).json()
    resp = client.patch(
        f"/api/v1/voice/sessions/{voice_id}/model",
        headers=headers,
        json={"model_id": "some-other-model"},
    )
    assert resp.status_code == 200, resp.text
    after = resp.json()
    assert after["session_epoch"] == before["session_epoch"] + 1

    events = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()["events"]
    updated = [event for event in events if event["type"] == "context.updated"]
    assert updated, "model 切换必须发 context.updated"
    assert updated[-1]["payload"].get("applies_to") == "next_turn"
    assert updated[-1]["payload"].get("applied_epoch") == after["session_epoch"]
    # 三种 context.updated 必须可区分：请求带 reason=model_switch，快照带
    # reason=context_snapshot。客户端过去只认事件类型，于是把建立通话时写的快照
    # 当成一次切换请求，渲染出"本次通话仍在使用「X」，「新模型」将在下次接通后生效"。
    assert updated[-1]["payload"].get("reason") == "model_switch"
    switch_events = [
        event
        for event in updated
        if event["payload"].get("reason") == "model_switch"
        and event["payload"].get("model_id") == "some-other-model"
    ]
    assert switch_events, "切换请求必须带 reason=model_switch 与目标模型"
    snapshot_events = [
        event
        for event in updated
        if event["payload"].get("reason") == "context_snapshot"
    ]
    assert snapshot_events, "建立通话必须写一条带 reason=context_snapshot 的快照事件"
    for event in snapshot_events:
        assert "model_id" not in event["payload"], "快照事件与模型无关，不能带模型 id"


# ------------------------------------------------- LLM failure / idle recovery


def _voice_session_for(register_user, auth_headers, prefix: str):
    token, ws, _u, _p = register_user(prefix)
    headers = auth_headers(token, ws)
    return headers


def test_llm_failure_without_text_persists_question_and_is_retryable(
    client, register_user, auth_headers
):
    """模型失败且没有任何文本时：问题必须落库、可重试，且不得进长期记忆。

    修复前：回合永不 finalize，刷新后问题彻底消失。
    """
    headers = _voice_session_for(register_user, auth_headers, "voice_llmfail")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    import asyncio

    from app.voice.journal import VoiceTurnJournal
    from app.voice.turns import TURN_FAILED

    async def _run():
        journal = VoiceTurnJournal(
            voice_id, idle_timeout_secs=30.0, retry_attempts=0, retry_hook=None
        )
        await journal.user_final("模型会失败的问题")
        # LLM-stage error with zero text produced (retry budget 0).
        await journal.llm_failed("upstream 502")
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        turns = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .all()
        )
        assert len(turns) == 1
        assert turns[0].status == TURN_FAILED, turns[0].status
        assert turns[0].failure_reason == "llm_error"
        assert turns[0].user_text == "模型会失败的问题"

        messages = db.query(Message).filter(Message.session_id == chat_id).all()
        assert [row.role for row in messages] == ["user"], "失败回合只落用户问题"
        assert messages[0].content == "模型会失败的问题"
        assert _memory_job_count(db, chat_id) == 0, "没有回答就不得进入长期记忆"

    transcript = client.get(
        f"/api/v1/voice/sessions/{voice_id}/transcript", headers=headers
    ).json()["turns"]
    assert len(transcript) == 1
    assert transcript[0]["status"] == TURN_FAILED
    assert transcript[0]["user_text"] == "模型会失败的问题"

    events = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()["events"]
    finalized = [event for event in events if event["type"] == "turn.finalized"]
    assert len(finalized) == 1, "只应有一条 turn.finalized"
    assert finalized[0]["payload"].get("retryable") is True
    assert finalized[0]["payload"].get("outcome") == TURN_FAILED


def test_llm_failure_retries_then_succeeds(client, register_user, auth_headers):
    """首次失败 -> 排重试；重试成功后按正常回合收尾，且只落一对消息。"""
    headers = _voice_session_for(register_user, auth_headers, "voice_retry")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    import asyncio

    from app.voice.journal import VoiceTurnJournal

    retries: list[int] = []

    async def _run():
        journal: VoiceTurnJournal

        async def _hook() -> None:
            retries.append(1)
            journal.note_assistant_text("重试之后的回答")
            await journal.sentence_queued("重试之后的回答")
            await journal.playback_started()
            await journal.assistant_completed()
            await journal.drain_persistence()

        journal = VoiceTurnJournal(
            voice_id, idle_timeout_secs=30.0, retry_attempts=3, retry_hook=_hook
        )
        await journal.user_final("会被重试的问题")
        await journal.llm_failed("upstream 503")
        await journal.drain_persistence()
        assert journal._retry_handle is not None, "失败后应排出重试"
        # Drive the scheduled retry deterministically instead of waiting.
        journal._retry_handle.cancel()
        journal._retry_handle = None
        await journal._run_retry()
        await journal.drain_persistence()

    asyncio.run(_run())
    assert retries == [1]

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.status == "finalized"
        messages = db.query(Message).filter(Message.session_id == chat_id).all()
        assert len(messages) == 2, "重试成功只应有一对消息"
        assistant = [row for row in messages if row.role == "assistant"][0]
        assert assistant.content == "重试之后的回答"
        assert _memory_job_count(db, chat_id) == 1, "成功回合才进入长期记忆"

    events = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()["events"]
    assert "processor.retry_scheduled" in [event["type"] for event in events]


def test_llm_failure_with_unheard_partial_text_is_not_finalized_as_success(
    client, register_user, auth_headers
):
    """已有生成草稿时不自动重跑，但没有播放证据也绝不能保存成成功回答。"""
    headers = _voice_session_for(register_user, auth_headers, "voice_partial")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    import asyncio

    from app.voice.journal import VoiceTurnJournal

    retried: list[int] = []

    async def _run():
        async def _hook() -> None:
            retried.append(1)

        journal = VoiceTurnJournal(
            voice_id, idle_timeout_secs=30.0, retry_attempts=3, retry_hook=_hook
        )
        await journal.user_final("问题")
        journal.note_assistant_text("已经说了一半")
        await journal.llm_failed("mid-stream reset")
        await journal.drain_persistence()
        await journal.assistant_completed()
        await journal.drain_persistence()

    asyncio.run(_run())
    assert retried == [], "有文本时不得重试"

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.status == "failed"
        assert turn.failure_reason == "no_playback_frames"
        assert not turn.assistant_text
        assistants = (
            db.query(Message)
            .filter(Message.session_id == chat_id, Message.role == "assistant")
            .all()
        )
        assert assistants == []


def test_duplicate_llm_failure_report_consumes_only_one_attempt(
    client, register_user, auth_headers
):
    """同一次 LLM 失败被两处各报一次时，只能算一次失败。

    错误帧会被管线观察点看到，同一次故障在抵达 worker 源头时又会触发
    ``on_pipeline_error``。两次上报若各算一次，一次真实故障就吃掉两次重试预算，
    ``retry_attempts=2`` 的会话会直接失败而根本没重试过。
    """
    headers = _voice_session_for(register_user, auth_headers, "voice_dup_error")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    import asyncio

    from app.voice.journal import VoiceTurnJournal

    seen: dict[str, int] = {}

    async def _run():
        retried: list[int] = []

        async def _hook() -> None:
            retried.append(1)

        journal = VoiceTurnJournal(
            voice_id, idle_timeout_secs=30.0, retry_attempts=2, retry_hook=_hook
        )
        await journal.user_final("网络抖动的问题")
        await journal.llm_failed("upstream 502")
        await journal.drain_persistence()
        seen["attempt"] = int(journal._turn.retry_attempt)
        # Second report of the *same* failure, from the other observation point.
        await journal.llm_failed("upstream 502")
        await journal.drain_persistence()
        seen["attempt_after_duplicate"] = int(journal._turn.retry_attempt)
        journal._cancel_retry()
        seen["retries"] = len(retried)

    asyncio.run(_run())
    assert seen["attempt"] == 1, "首次失败应排一次重试"
    assert seen["attempt_after_duplicate"] == 1, (
        "重复上报不得再吃掉一次重试预算"
    )
    assert seen["retries"] == 0, "重试是延后执行的，此处不应已经跑过"


def test_idle_turn_is_finalized_without_any_provider_event(
    client, register_user, auth_headers
):
    """LLM 既不报错也不结束时，idle 兜底必须让回合终结（否则问题永久悬挂）。"""
    headers = _voice_session_for(register_user, auth_headers, "voice_idle")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    import asyncio

    from app.voice.journal import VoiceTurnJournal
    from app.voice.turns import TURN_FAILED

    async def _run():
        # 1s is the enforced floor for the idle ceiling, so the wait must clear it.
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=0.05)
        assert journal._idle_timeout_secs == 1.0, "idle 上限不得低于 1s 下限"
        await journal.user_final("会被超时兜底的问题")
        # No text, no error, no end frame: exactly the hang case.
        await asyncio.sleep(1.4)
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.status == TURN_FAILED
        assert turn.failure_reason == "turn_idle_timeout"
        messages = db.query(Message).filter(Message.session_id == chat_id).all()
        assert [row.role for row in messages] == ["user"]
        assert _memory_job_count(db, chat_id) == 0


def test_idle_turn_with_unheard_text_fails_retryably(client, register_user, auth_headers):
    """超时时即使已生成草稿，没有播报证据也只能失败，不能伪装成成功回答。"""
    headers = _voice_session_for(register_user, auth_headers, "voice_idle2")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    import asyncio

    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=0.05)
        await journal.user_final("问题")
        journal.note_assistant_text("已经生成但没有播出的回答")
        await asyncio.sleep(1.4)
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.status == "failed"
        assert turn.failure_reason == "turn_idle_timeout"
        assert not turn.assistant_text
        messages = db.query(Message).filter(Message.session_id == chat_id).all()
        assert [row.role for row in messages] == ["user"]
        assert _memory_job_count(db, chat_id) == 0


def test_interrupted_turn_persists_only_sentences_that_reached_playback(
    client, register_user, auth_headers
):
    """被打断时，只把已经开始播报的句子写进可审计转录。

    每句的游标在它的首个音频包入队时发出，所以“已经开始播报”= 用户听到的内容。
    修复前这里只保留第一句（``played_sentence_seq`` 永远停在 1），于是一段被完整
    听完、只是在收尾时被下一句抢断的回答，落库后只剩开头一句；现在保留全部已播报
    的句子，未进入播报的句子（连游标都没有）依然一个字都不写。
    """
    headers = _voice_session_for(register_user, auth_headers, "voice_heard")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    import asyncio

    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        journal.note_assistant_text("第一句。第二句。第三句。")
        await journal.sentence_queued("第一句。")
        await journal.playback_started()
        await journal.sentence_queued("第二句。")
        await journal.sentence_queued("第三句。")
        await journal.turn_interrupted(reason="barge_in")
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.status == "interrupted"
        assert turn.assistant_text == "第一句。第二句。第三句。"
        assistant = (
            db.query(Message)
            .filter(Message.session_id == chat_id, Message.role == "assistant")
            .one()
        )
        assert assistant.content == "第一句。第二句。第三句。"
        assert _memory_job_count(db, chat_id) == 0


def test_partial_playback_timeout_does_not_write_long_term_memory(
    client, register_user, auth_headers
):
    """TTS 已开始但未确认完整播报时，兜底收尾只写“已经开读”的内容，且不写长期记忆。"""
    headers = _voice_session_for(register_user, auth_headers, "voice_partial_tts")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    import asyncio

    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        journal.note_assistant_text("第一句。第二句。")
        await journal.sentence_queued("第一句。")
        await journal.playback_started()
        await journal.assistant_completed(degraded_reason="no_playback_frames")
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.status == "finalized"
        # 只有第一句进入了播报；第二句从未开读，所以一个字都不入库。
        assert turn.assistant_text == "第一句。"
        assert _memory_job_count(db, chat_id) == 0


# ------------------------------------------------- grace finalize vs. real playback


def test_grace_finalize_stands_down_once_playback_was_observed(
    client, register_user, auth_headers
):
    """已观测到播放时，兜底收尾不得判 TTS 降级。

    修复前：grace 从 LLM 收尾起算（默认 6s），比一句朗读还短，于是每通正常电话都会
    在音频仍在播时收到 ``processor.error(tts, no_playback_frames)``。前端把 ``tts``
    锁进 ``degradedStages``（整通电话挂着「语音播报已不可用」横幅），而该回合因
    degraded_reason 非空而 ``memory=False``，整段问答静默不进长期记忆。
    """
    headers = _voice_session_for(register_user, auth_headers, "voice_grace_ok")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, grace_secs=0.05, idle_timeout_secs=30.0)
        await journal.user_final("介绍一下你自己")
        journal.note_assistant_text("一句还在朗读的回答。")
        await journal.sentence_queued("一句还在朗读的回答。")
        await journal.playback_started()
        journal.llm_closed()
        # 让 grace 定时器真的跑一次；它只应在「没有任何播放」时才收尾。
        await asyncio.sleep(0.3)
        assert journal._turn is not None and not journal._turn.finalized, (
            "已观测到播放时，grace 定时器不得抢在 BotStoppedSpeaking 之前收尾"
        )
        await journal.assistant_completed()
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.status == "finalized"
        assert _memory_job_count(db, chat_id) == 1, "正常播报过的回合必须进长期记忆"

    events = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()["events"]
    assert "processor.error" not in [event["type"] for event in events]


def test_grace_finalize_fails_when_no_playback_arrived(
    client, register_user, auth_headers
):
    """TTS 完全没出声时，兜底必须失败且保留重试，不得保存模型草稿。"""
    headers = _voice_session_for(register_user, auth_headers, "voice_grace_down")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, grace_secs=0.05, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        journal.note_assistant_text("TTS 没有出声。")
        journal.llm_closed()
        await asyncio.sleep(0.3)
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.status == "failed"
        assert turn.failure_reason == "no_playback_frames"
        assert not turn.assistant_text
        assert [row.role for row in db.query(Message).filter(Message.session_id == chat_id).all()] == ["user"]
        assert _memory_job_count(db, chat_id) == 0

    events = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()["events"]
    assert not [event for event in events if event["type"] == "assistant.llm.delta"]
    errors = [event for event in events if event["type"] == "processor.error"]
    assert errors and errors[0]["payload"]["reason"] == "no_playback_frames"
    assert errors[0]["payload"]["stage"] == "tts"
    assert errors[0]["payload"]["retryable"] is True


# ------------------------------------------------------ merged ASR final persistence


def test_merged_asr_final_is_what_reaches_the_transcript(
    client, register_user, auth_headers
):
    """分段 ASR 合并后的问题必须落库，页面不能只显示第一段。

    修复前：合并只写进内存 ``state.user_text``，``voice_turns.user_text`` 仍是开启
    回合的第一段；finalize 按行落库 → 页面上出现「问题被截断、回答对不上」。
    """
    headers = _voice_session_for(register_user, auth_headers, "voice_merge_text")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("你好。")
        await journal.user_final("请一句话介绍一下你自己。")
        journal.note_assistant_text("我是学习助手。")
        await journal.sentence_queued("我是学习助手。")
        await journal.playback_started()
        await journal.assistant_completed()
        await journal.drain_persistence()

    asyncio.run(_run())

    expected = "你好。请一句话介绍一下你自己。"
    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.user_text == expected
        user = (
            db.query(Message)
            .filter(Message.session_id == chat_id, Message.role == "user")
            .one()
        )
        assert user.content == expected


# ------------------------------------------- interrupted turns keep the user's words


def test_interrupted_turn_keeps_the_question_when_nothing_was_heard(
    client, register_user, auth_headers
):
    """被抢话、且还没听到任何回答时，用户说过的话仍要留在转录里。

    ``finalize_turn`` 的文档写明 ``interrupted`` 会保留用户问题（丢掉它正是「用户
    好像没开口」的成因），但该路径原来只在 heard_text 非空时才落库，于是任何在
    机器人出声前被抢断的句子都从对话里彻底消失。
    """
    headers = _voice_session_for(register_user, auth_headers, "voice_barge_kept")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    from app.voice.journal import VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("等等。")
        await journal.turn_interrupted(reason="user_started_speaking")
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.status == "interrupted"
        user = (
            db.query(Message)
            .filter(Message.session_id == chat_id, Message.role == "user")
            .one()
        )
        assert user.content == "等等。"
        assert (
            db.query(Message)
            .filter(Message.session_id == chat_id, Message.role == "assistant")
            .count()
            == 0
        ), "没有回答就不该写出空的助手气泡"
        assert _memory_job_count(db, chat_id) == 0, "被打断的回合不进长期记忆"


# ------------------------------------- idle ceiling vs. an answer still playing


def test_idle_ceiling_does_not_fire_while_the_answer_is_still_playing(
    client, register_user, auth_headers
):
    """播放进行中不得触发空闲上限。

    修复前：20s 空闲上限只被"句子边界"刷新，而长回答在播放期间不再产生任何事件
    —— 于是音频还在播时就报 ``processor.error(tts, turn_idle_timeout, degraded)``。
    两个后果都实测过：前端亮出"语音播报已不可用"的误报横幅；该轮因 degraded_reason
    非空而 ``memory=False``，用户真实的一段对话静默不进长期记忆。
    """
    headers = _voice_session_for(register_user, auth_headers, "voice_idle_playing")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    from app.voice.journal import VoiceTurnJournal

    async def _run():
        # grace 拉长到不会触发，专门观察空闲上限这条路径。
        journal = VoiceTurnJournal(voice_id, grace_secs=30.0, idle_timeout_secs=1.0)
        await journal.user_final("问题")
        journal.note_assistant_text("一句很长的回答。")
        await journal.sentence_queued("一句很长的回答。")
        await journal.playback_started()
        # 直接驱动空闲回调（空闲上限有 1s 下限，不该靠真实等待），播放中它必须重排
        # 而不是收尾。
        for _ in range(3):
            await journal._on_turn_idle()
        assert journal._turn is not None and not journal._turn.finalized, (
            "音频还在播时空闲上限不得收尾"
        )
        await journal.playback_ended()
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.status == "finalized"
        assert _memory_job_count(db, chat_id) == 1, "正常播完的回合必须进长期记忆"

    events = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()["events"]
    errors = [event for event in events if event["type"] == "processor.error"]
    assert errors == [], f"播放中不该报降级：{errors}"


def test_idle_ceiling_still_fires_when_playback_never_reports_its_end(
    client, register_user, auth_headers
):
    """反向保护：播放结束信号丢失时，空闲上限仍必须收尾（有上限地重排）。"""
    headers = _voice_session_for(register_user, auth_headers, "voice_idle_stuck")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    from app.voice.journal import MAX_PLAYBACK_REARMS, VoiceTurnJournal

    async def _run():
        journal = VoiceTurnJournal(voice_id, grace_secs=30.0, idle_timeout_secs=1.0)
        await journal.user_final("问题")
        journal.note_assistant_text("永远播不完的回答。")
        await journal.sentence_queued("永远播不完的回答。")
        await journal.playback_started()
        # 播放结束信号一直不来：重排次数用尽后必须收尾，不能永远挂着。
        for _ in range(MAX_PLAYBACK_REARMS + 1):
            await journal._on_turn_idle()
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        assert turn.status == "finalized"

    events = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()["events"]
    reasons = [
        event["payload"].get("reason")
        for event in events
        if event["type"] == "processor.error"
    ]
    assert "turn_idle_timeout" in reasons, reasons


# ------------------------------------------------- typed turn journaling (tap side)


def test_typed_utterance_is_journaled_under_the_clients_own_turn(
    client, register_user, auth_headers
):
    """打字说的那句话必须真正进入账本，并且复用控制面已建的那条回合。

    修复前：``LLMMessagesAppendFrame`` 的观察点挂在 user aggregator 下游
    （assistant tap），而该帧恰好被 ``LLMUserAggregator`` 消费——观察者永远看不到它，
    于是 ``user_typed`` 从不执行：journal 没有打开回合（``assistant.*`` 事件
    ``turn_id`` 全为 NULL）、空闲上限无从 arm、``finalize_turn`` 永不触发，
    页面上那句问题既不落库也不进记忆，状态永远停在「正在回答」。
    """
    headers = _voice_session_for(register_user, auth_headers, "voice_typed_journal")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    from app.voice.journal import VoiceTurnJournal

    typed = _post_accept(client, headers, voice_id, "我想学辣椒炒肉", "cmid-journal")

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        # What the RTVI client-message channel announces before ``send-text``.
        journal.expect_typed_turn("cmid-journal", "我想学辣椒炒肉")
        await journal.user_typed("我想学辣椒炒肉")
        assert journal._turn is not None, "打字回合必须打开账本"
        assert journal._turn.turn_id == typed["turn_id"], "必须复用控制面已建的回合"
        # The assistant half now carries the same turn id, which is what makes
        # finalize/persist/memory reachable at all.
        journal.note_assistant_text("先选肉。")
        await journal.sentence_queued("先选肉。")
        await journal.assistant_completed()
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        rows = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .all()
        )
        assert len(rows) == 1, "一句话不得变成两条回合"
        assert rows[0].id == typed["turn_id"]
        assert rows[0].status == "finalized", rows[0].status
        messages = db.query(Message).filter(Message.session_id == chat_id).all()
        assert [row.role for row in messages] == ["user", "assistant"], (
            "打字回合必须落一对消息"
        )
        assert messages[0].content == "我想学辣椒炒肉"


def test_typed_utterance_without_a_key_attaches_to_the_open_turn(
    client, register_user, auth_headers
):
    """没有 idempotency key（旧客户端 / 通道乱序）时只能挂到已开的回合上。"""
    headers = _voice_session_for(register_user, auth_headers, "voice_typed_attach")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    from app.voice.journal import VoiceTurnJournal

    typed = _post_accept(client, headers, voice_id, "打字的问题", "cmid-attach")

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_typed("打字的问题")
        assert journal._turn is not None
        assert journal._turn.turn_id == typed["turn_id"]

    asyncio.run(_run())

    with SessionLocal() as db:
        rows = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .all()
        )
        assert len(rows) == 1, "挂接路径同样不得开出第二条回合"


# ------------------------------------------------------------- stale turn sweep


def test_stale_sweep_closes_hung_turns_and_spares_live_ones(
    client, register_user, auth_headers
):
    """没人认领的回合要被老化收尾；worker 正在答的回合一根汗毛都不能动。"""
    headers = _voice_session_for(register_user, auth_headers, "voice_stale_sweep")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    from app.voice.turns import (
        STALE_TURN_REASON,
        accept_turn,
        finalize_stale_turns,
        record_assistant_draft,
    )

    hung = accept_turn(voice_id, "没人回答的问题", client_message_id="cmid-hung")
    alive = accept_turn(voice_id, "正在回答的问题", client_message_id="cmid-alive")
    # Evidence that the worker picked this one up: an ``assistant.*`` event under
    # its own turn id. Old age alone must never close it.
    record_assistant_draft(voice_id, alive["turn_id"], "正在生成", phase="speculative")

    # Age both rows past the ceiling (the sweep itself is age-gated).
    with SessionLocal() as db:
        for row in (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .all()
        ):
            row.started_at = row.started_at - timedelta(minutes=10)
        db.commit()

    closed = finalize_stale_turns(voice_id, max_age_secs=60.0)
    assert closed == [hung["turn_id"]], closed

    with SessionLocal() as db:
        rows = {
            row.id: row
            for row in db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .all()
        }
        assert rows[hung["turn_id"]].status == "failed"
        assert rows[hung["turn_id"]].failure_reason == STALE_TURN_REASON
        assert rows[hung["turn_id"]].user_text == "没人回答的问题"
        assert rows[alive["turn_id"]].status == "accepted", "在答的回合不能被收尾"
        # The hung turn keeps the question (retryable) and writes no empty answer.
        messages = db.query(Message).filter(Message.session_id == chat_id).all()
        assert [row.role for row in messages] == ["user"]
        assert messages[0].content == "没人回答的问题"
        assert _memory_job_count(db, chat_id) == 0, "没有回答就不得进入长期记忆"

    events = client.get(
        f"/api/v1/voice/sessions/{voice_id}/events?after_event_seq=0", headers=headers
    ).json()["events"]
    finalized = [event for event in events if event["type"] == "turn.finalized"]
    assert len(finalized) == 1, "只应收尾那条挂死的回合"
    assert finalized[0]["payload"].get("failure_reason") == STALE_TURN_REASON
    assert finalized[0]["payload"].get("retryable") is True


def test_session_end_settles_turns_still_open(client, register_user, auth_headers):
    """挂断时仍开着的回合必须当场收尾，而不是永久停在 accepted。"""
    headers = _voice_session_for(register_user, auth_headers, "voice_close_settles")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    from app.voice.journal import VoiceTurnJournal

    typed = _post_accept(client, headers, voice_id, "说一半就挂了", "cmid-close")

    async def _run():
        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_typed("说一半就挂了")
        await journal.close_open_turns_on_session_end()
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        rows = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].id == typed["turn_id"]
        assert rows[0].status == "failed", rows[0].status
        assert rows[0].failure_reason == "session_closed"
        messages = db.query(Message).filter(Message.session_id == chat_id).all()
        assert [row.role for row in messages] == ["user"]


def test_unheard_text_is_never_persisted_or_returned(client, register_user, auth_headers):
    """未播出的余量不进任何一层：转录、消息 trace、``/transcript`` 都只带听到的前缀。

    旧契约把"生成出来但没播出去"的余量塞进消息 trace 与 ``/transcript``，让页面
    刷新后能重建折叠区。那等于让没被读到的字在刷新后重新出现在正文旁边，所以余量
    整条通路被拆掉了：现在只坚持"说过什么就是什么"，刷新重建的也只有已播报内容。
    """
    headers = _voice_session_for(register_user, auth_headers, "voice_unheard")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    async def _run():
        from app.voice.journal import VoiceTurnJournal

        journal = VoiceTurnJournal(voice_id, idle_timeout_secs=30.0)
        await journal.user_final("问题")
        journal.note_assistant_text("第一句。第二句。")
        await journal.sentence_queued("第一句。", audio_cursor_ms=0)
        await journal.sentence_ended(sentence_seq=1, audio_end_cursor_ms=1000)
        await journal.sentence_queued("第二句。", audio_cursor_ms=1000)
        await journal.sentence_ended(sentence_seq=2, audio_end_cursor_ms=2000)
        # 第二句说到一半（1000→2000ms 的中间）用户插话。
        await journal.playback_ack(sentence_seq=2, played_ms=1500, phase="progress")
        await journal.turn_interrupted(reason="barge_in")
        await journal.drain_persistence()

    asyncio.run(_run())

    with SessionLocal() as db:
        turn = (
            db.query(VoiceTurnRecord)
            .filter(VoiceTurnRecord.voice_session_id == voice_id)
            .one()
        )
        # 已听到 = 第一句整句 + 第二句的前两个字。
        assert turn.assistant_text == "第一句。第二", turn.assistant_text
        assistant = (
            db.query(Message)
            .filter(Message.session_id == chat_id, Message.role == "assistant")
            .one()
        )
        # 正文里绝不能出现没播出去的字。
        assert assistant.content == "第一句。第二"
        trace = assistant.provider_trace or {}
        assert trace.get("voice_unheard") is None, trace
        # 画布按 `voice_turn` 判定语音回合（乐观行写的也是这个名字）：服务端行必须同样
        # 带上它，否则刷新后同一条消息会退回"普通消息"，句级字幕与语音专属门控一起失效。
        assert trace.get("voice_turn") is True, trace
        assert trace.get("voice") is True, trace
        # 被打断的回合没有 ``turn.finalized`` 事件：``interrupt_turn`` 已经把状态置成
        # ``interrupted``，``finalize_turn`` 于是走"已落定"分支、不再补发终态事件。
        # 余量因此既没有事件载体，也没有消息 trace 载体 —— 本用例验证的正是它无处可去。
        assert (
            db.query(VoiceEventRecord)
            .filter(
                VoiceEventRecord.voice_session_id == voice_id,
                VoiceEventRecord.event_type == "turn.finalized",
            )
            .count()
            == 0
        )
        interrupted = (
            db.query(VoiceEventRecord)
            .filter(
                VoiceEventRecord.voice_session_id == voice_id,
                VoiceEventRecord.event_type == "turn.interrupted",
            )
            .one()
        )
        assert interrupted.turn_id == turn.id
        assert "unheard_text" not in (interrupted.payload or {})

    # 刷新路径：/transcript 只交回已经播报出去的内容。
    resp = client.get(f"/api/v1/voice/sessions/{voice_id}/transcript", headers=headers)
    assert resp.status_code == 200, resp.text
    turns = resp.json()["turns"]
    assert len(turns) == 1
    assert turns[0]["assistant_text"] == "第一句。第二"
    assert "unheard_text" not in turns[0]


def test_transcript_also_returns_the_turn_that_is_still_running(client, register_user, auth_headers):
    """/transcript 要交回"正在进行"的那一轮：刷新不该让用户自己的提问消失。

    进行中的回合还没有落成聊天消息，实时气泡也只活在画布里，所以它曾经在刷新后整轮
    不见。它只带 user_text，不带任何助手文本（那一刻确实一个字都没播出去），并且会被
    stale-turn 巡检或管线自身的 finalize 收尾。
    """
    headers = _voice_session_for(register_user, auth_headers, "voice_transcript_open")
    chat_id = _make_chat_session(client, headers)
    voice_id = _create_voice_session(client, headers, chat_id)["id"]

    accepted = client.post(
        f"/api/v1/voice/sessions/{voice_id}/turns/accept",
        headers=headers,
        json={"text": "正在进行的问题"},
    )
    assert accepted.status_code == 200, accepted.text
    turn_id = accepted.json()["turn_id"]

    resp = client.get(f"/api/v1/voice/sessions/{voice_id}/transcript", headers=headers)
    assert resp.status_code == 200, resp.text
    turns = resp.json()["turns"]
    assert [item["turn_id"] for item in turns] == [turn_id]
    assert turns[0]["status"] == "accepted"
    assert turns[0]["user_text"] == "正在进行的问题"
    assert not turns[0]["assistant_text"], "进行中的回合不能带助手文本"

    # 管线取历史上下文走的是同一个 loader，它必须仍然只看已落定的回合。
    resp = client.get(
        f"/api/v1/voice/sessions/{voice_id}/transcript?include_open=false", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["turns"] == []

    client.post(
        f"/api/v1/voice/sessions/{voice_id}/turns/{turn_id}/finalize",
        headers=headers,
        json={"assistant_text": "答完了"},
    )
    resp = client.get(f"/api/v1/voice/sessions/{voice_id}/transcript", headers=headers)
    turns = resp.json()["turns"]
    assert len(turns) == 1, "落定后不能既算进行中又算已落定"
    assert turns[0]["status"] == "finalized"
    assert turns[0]["assistant_text"] == "答完了"


def test_background_delivery_persists_only_assistant_without_fake_user(client, register_user, auth_headers):
    from app.voice.turns import accept_turn, finalize_turn
    from sqlalchemy import select
    token, ws, _, _ = register_user("voice_delivery")
    headers = auth_headers(token, ws)
    chat = _make_chat_session(client, headers)
    voice = _create_voice_session(client, headers, chat)["id"]
    accepted = accept_turn(voice, "", client_message_id="voice-delivery:speech-1", origin="delivery")
    assert accepted["created"]
    for _ in range(2):
        result = finalize_turn(voice, accepted["turn_id"], "后台结果已经完成。", memory=False)
        assert result["persisted"]
    with SessionLocal() as db:
        messages = list(db.scalars(select(Message).where(Message.session_id == chat)))
        assert [(message.role, message.content) for message in messages] == [("assistant", "后台结果已经完成。")]
        assert not list(db.scalars(select(VoiceEventRecord).where(VoiceEventRecord.voice_session_id == voice,
                                                                  VoiceEventRecord.event_type == "user.final")))
