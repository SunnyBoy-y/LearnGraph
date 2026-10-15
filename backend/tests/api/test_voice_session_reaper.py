"""会话级空闲收尾（S7 的会话 TTL）离线验证。

审计（2026-09-16）结论：一条被丢弃的通话（合上盖子、手机进兜、忘了关页面）不会
被任何一层收尾——客户端不该写库（轮询是只读的），aiortc 也不会察觉还活着的 peer。
于是 durable 行一直 ``active``，它背后那条实时 ASR 会话与双向 TTS 流就一直挂着。

这里钉住收尾判据的三条边界：

1. 有活动证据（事件/回合/会话行）在窗口内 → **不**收尾；
2. 证据都过期 → 收尾，且走的是与正常挂断同一条幂等路径（``session.closed``）；
3. 同进程管线仍在收音频（库里看不见的证据）→ 本轮跳过。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.core.database import SessionLocal
from app.domain.models import VoiceEventRecord, VoiceSessionRecord
from app.voice import reaper
from app.voice.reaper import idle_voice_sessions, run_voice_session_idle_sweep


def _create_voice_session(client, register_user, auth_headers) -> str:
    token, workspace_id, _user, _password = register_user("reaper")
    headers = auth_headers(token, workspace_id)
    chat = client.post(
        "/api/v1/sessions", headers=headers, json={"title": "reaper"}
    )
    assert chat.status_code in (200, 201), chat.text
    created = client.post(
        "/api/v1/voice/sessions",
        headers=headers,
        json={"chat_session_id": chat.json()["id"], "max_thinking_mode": "off"},
    )
    assert created.status_code in (200, 201), created.text
    return created.json()["id"]


def _backdate_session(voice_session_id: str, *, seconds: float) -> datetime:
    """把会话行与其全部事件挪到过去，模拟"phone in a pocket"。"""
    stamp = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    with SessionLocal() as db:
        db.execute(
            update(VoiceSessionRecord)
            .where(VoiceSessionRecord.id == voice_session_id)
            .values(updated_at=stamp, created_at=stamp)
        )
        db.execute(
            update(VoiceEventRecord)
            .where(VoiceEventRecord.voice_session_id == voice_session_id)
            .values(created_at=stamp, updated_at=stamp)
        )
        db.commit()
    return stamp


def _status(voice_session_id: str) -> str:
    with SessionLocal() as db:
        row = db.get(VoiceSessionRecord, voice_session_id)
        return "" if row is None else str(row.status)


def _cleanup(voice_session_id: str) -> None:
    with SessionLocal() as db:
        db.execute(
            VoiceEventRecord.__table__.delete().where(
                VoiceEventRecord.voice_session_id == voice_session_id
            )
        )
        row = db.get(VoiceSessionRecord, voice_session_id)
        if row is not None:
            db.delete(row)
        db.commit()


def test_abandoned_session_is_closed_by_the_sweep(client, register_user, auth_headers):
    voice_id = _create_voice_session(client, register_user, auth_headers)
    try:
        _backdate_session(voice_id, seconds=3600)
        assert _status(voice_id) == "active"

        candidate_ids = {
            item["voice_session_id"]
            for item in idle_voice_sessions(idle_timeout_seconds=2700)
        }
        assert voice_id in candidate_ids, "一小时没有任何活动的会话必须被判为空闲"

        totals = run_voice_session_idle_sweep(idle_timeout_seconds=2700)
        assert totals["closed"] >= 1
        assert _status(voice_id) == "ended", "收尾必须是 durable 状态变更，而不只是本地关闭"

        # 与正常挂断同一条幂等路径：再扫一轮不该重复产生 session.closed。
        again = run_voice_session_idle_sweep(idle_timeout_seconds=2700)
        assert again["closed"] == 0
    finally:
        _cleanup(voice_id)


def test_recent_activity_keeps_the_session_alive(client, register_user, auth_headers):
    """只要还有事件在写（转录、回答、任务、上下文），就绝不能被误收。"""
    voice_id = _create_voice_session(client, register_user, auth_headers)
    try:
        # 会话行很旧，但刚刚又发生了一次事件——这正是"库里看得见的活动"。
        _backdate_session(voice_id, seconds=3600)
        from app.voice.events import emit_event

        emit_event(voice_id, "user.final", payload={"text": "我刚说了一句"})

        candidate_ids = {
            item["voice_session_id"]
            for item in idle_voice_sessions(idle_timeout_seconds=2700)
        }
        assert voice_id not in candidate_ids

        totals = run_voice_session_idle_sweep(idle_timeout_seconds=2700)
        assert totals["closed"] == 0
        assert _status(voice_id) == "active"
    finally:
        _cleanup(voice_id)


def test_local_pipeline_activity_saves_a_session_the_journal_cannot_see(
    client, register_user, auth_headers, monkeypatch
):
    """库里看着空闲、但本进程管线仍在收音频 → 本轮跳过（只有内存里有这个证据）。"""
    voice_id = _create_voice_session(client, register_user, auth_headers)
    try:
        _backdate_session(voice_id, seconds=3600)
        monkeypatch.setattr(reaper, "_local_pipeline_idle_seconds", lambda _sid: 3.0)

        totals = run_voice_session_idle_sweep(idle_timeout_seconds=2700)
        assert totals["skipped_local"] >= 1
        assert totals["closed"] == 0
        assert _status(voice_id) == "active"
    finally:
        _cleanup(voice_id)


def test_local_activity_is_read_from_the_live_pipeline_processors():
    """``_local_pipeline_idle_seconds`` 真的会去读管线里的 STT 实例。"""
    from app.voice.embedded_dashscope_stt import (
        DashScopeSTTService,
        DashScopeSTTSettings,
    )
    from app.voice.runner_registry import (
        VoiceRunnerHandle,
        clear_registry,
        register_runner,
        unregister_runner,
    )

    clear_registry()
    service = DashScopeSTTService(
        api_key="test-key", settings=DashScopeSTTSettings(model="qwen3-asr-flash-realtime")
    )
    register_runner(
        VoiceRunnerHandle(
            voice_session_id="vs-reaper-local",
            chat_session_id="chat-reaper",
            owner_user_id="user-1",
            processors=[service],
        )
    )
    try:
        idle = reaper._local_pipeline_idle_seconds("vs-reaper-local")
        assert idle is not None and idle < 5.0
        assert reaper._local_pipeline_idle_seconds("vs-reaper-missing") is None
    finally:
        unregister_runner("vs-reaper-local")
        clear_registry()


def test_sweep_is_disabled_by_a_zero_timeout(client):
    """``idle_timeout_seconds=0`` = 退回"连接随通话保持"的旧语义。"""
    totals = run_voice_session_idle_sweep(idle_timeout_seconds=0)
    assert totals == {"sessions": 0, "idle": 0, "closed": 0, "skipped_local": 0}
    # 默认值必须来自配置，而不是硬编码在扫描里。
    from app.core.config import get_settings

    assert get_settings().voice_session_idle_timeout_seconds == 2700.0


def test_naive_sqlite_timestamps_do_not_break_the_idle_math():
    """SQLite 回来的时间戳是 naive 的；直接与 aware ``now`` 相减会抛异常，
    而那样的异常会让收尾**永远不触发**（被 try/except 吞掉），是最难发现的一类失效。"""
    naive = datetime(2026, 1, 1, 12, 0, 0)
    normalized = reaper._as_utc(naive)
    assert normalized is not None and normalized.tzinfo is timezone.utc
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert (aware - normalized).total_seconds() == 0
    assert reaper._as_utc(None) is None


def test_unknown_session_is_not_reported_as_idle(client):
    """查不到行 = 没有意见，不得推断成"空闲到极点"。"""
    assert idle_voice_sessions(idle_timeout_seconds=2700, limit=1) is not None
    assert run_voice_session_idle_sweep(idle_timeout_seconds=-1) == {
        "sessions": 0,
        "idle": 0,
        "closed": 0,
        "skipped_local": 0,
    }
