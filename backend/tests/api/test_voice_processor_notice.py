"""`processor.notice` 通道的持久化契约。

为什么要单独钉：`VoiceEventSink.emit` 对事件类型**白名单校验**（未知类型抛
`ValueError`），而 `VoiceTurnJournal._emit` 会把异常吞掉只打一条 warning。于是一个
没登记的类型会**静默丢失**——本仓库刚踩过这个坑（2026-09-16 加
`processor.notice` 时漏改白名单，两条本该落库的提示直接消失）。

同时钉住"它不是错误"：前端只对 `degraded=true` 的 `processor.error` 降级并强制
文本模式，notice 必须不带这个字段。
"""

from __future__ import annotations

from app.core.database import SessionLocal
from app.domain.models import VoiceEventRecord
from app.voice.events import VOICE_EVENT_TYPES
from app.voice.journal import VoiceTurnJournal


def _create_voice_session(client, register_user, auth_headers) -> str:
    token, workspace_id, _user, _password = register_user("notice")
    headers = auth_headers(token, workspace_id)
    chat = client.post("/api/v1/sessions", headers=headers, json={"title": "notice"})
    assert chat.status_code in (200, 201), chat.text
    created = client.post(
        "/api/v1/voice/sessions",
        headers=headers,
        json={"chat_session_id": chat.json()["id"], "max_thinking_mode": "off"},
    )
    assert created.status_code in (200, 201), created.text
    return created.json()["id"]


def test_processor_notice_is_a_registered_durable_event_type():
    """白名单必须包含它，否则 notice 会被 `_emit` 静默吞掉。"""
    assert "processor.notice" in VOICE_EVENT_TYPES
    from app.voice.schemas import VOICE_EVENT_TYPES as SCHEMA_TYPES

    assert "processor.notice" in SCHEMA_TYPES


def test_notice_is_persisted_and_does_not_degrade_the_call(
    client, register_user, auth_headers
):
    voice_id = _create_voice_session(client, register_user, auth_headers)
    journal = VoiceTurnJournal(voice_id)

    import asyncio

    asyncio.run(
        journal.notice("asr", "upstream_closed", "ASR 上游已关闭，下次说话自动重连")
    )

    with SessionLocal() as db:
        # `.one()` 本身就是断言：行不存在（= 被白名单静默吞掉）会直接失败。
        rows = (
            db.query(VoiceEventRecord)
            .filter(
                VoiceEventRecord.voice_session_id == voice_id,
                VoiceEventRecord.event_type == "processor.notice",
            )
            .all()
        )
        assert len(rows) == 1, "notice 必须真的写进 durable 日志，而不是被吞掉"
        payload = dict(rows[0].payload or {})

    assert payload["stage"] == "asr"
    assert payload["code"] == "upstream_closed"
    # 非降级：前端 `deriveDegradedStages` 只认 degraded=true。
    assert "degraded" not in payload
    assert payload.get("retryable") in (None, False)

    # 收尾：不留活跃会话给其它测试的扫描。
    client.delete(f"/api/v1/voice/sessions/{voice_id}")
    with SessionLocal() as db:
        for row in (
            db.query(VoiceEventRecord)
            .filter(VoiceEventRecord.voice_session_id == voice_id)
            .all()
        ):
            db.delete(row)
        db.commit()
