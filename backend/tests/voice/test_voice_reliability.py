"""Focused reliability and isolation tests for the voice control plane."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.errors import AppError
from app.domain.models import (
    SandboxAgentTask,
    VoiceResultInboxRecord,
    VoiceSessionRecord,
    VoiceSpeechDeliveryRecord,
    VoiceTaskLinkRecord,
    utc_now,
)
from app.voice.coordinator import (
    VoiceTaskCoordinator,
    capture_voice_task_outcome,
    resolve_voice_tool_policy,
)
from app.voice.runner_registry import (
    VoiceRunnerHandle,
    clear_registry,
    close_runner,
    register_runner,
)
from app.voice.service import VoiceSessionService
from app.voice import events as voice_events


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        yield session
    engine.dispose()


def _link(db, *, workspace_id: str = "ws-1", tenant_id: str = "tenant-1"):
    link = VoiceTaskLinkRecord(
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        voice_session_id="vs-1",
        chat_session_id="chat-1",
        subagent_id="task-1",
        status="RUNNING",
        thinking_mode="medium",
        title="Research",
        original_prompt="compare hashes",
        current_prompt="compare hashes",
        requirement_version=1,
        latest_result_version=0,
        allowed_tools=["web_search"],
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def _task(
    db,
    link: VoiceTaskLinkRecord,
    *,
    status: str = "SUCCEEDED",
    result_text: str = "hash tables differ in collision handling",
):
    task = SandboxAgentTask(
        workspace_id=link.workspace_id,
        owner_user_id="user-1",
        task_id=link.subagent_id,
        chat_session_id=link.chat_session_id,
        title=link.title,
        role_key="research",
        status=status,
        status_reason=None if status == "SUCCEEDED" else status.casefold(),
        spec_json={"objective": link.current_prompt},
        deliverables_json={
            "summary": result_text,
            "sources": [{"url": "https://example.test/source"}],
            "artifacts": [],
        },
        event_seq=1,
        finished_at=utc_now(),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_result_inbox_dedupes_and_revision_stales_old_ready_result(db):
    link = _link(db)
    task = _task(db, link)

    first = capture_voice_task_outcome(db, task)
    db.commit()
    second = capture_voice_task_outcome(db, task)
    db.commit()

    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert first.status == "ready"
    assert first.requirement_version == 1
    assert first.source_count == 1

    coordinator = VoiceTaskCoordinator(
        db, workspace_id=link.workspace_id, tenant_id=link.tenant_id
    )
    coordinator.revise_requirement(link, prompt="only official docs", note="scope tight")
    db.refresh(first)

    assert first.status == "stale"
    assert first.stale_at is not None
    assert link.requirement_version == 2


def test_delivery_is_idempotent_and_reconnect_does_not_claim(db):
    link = _link(db)
    task = _task(db, link)
    result = capture_voice_task_outcome(db, task)
    db.commit()
    assert result is not None
    coordinator = VoiceTaskCoordinator(
        db, workspace_id=link.workspace_id, tenant_id=link.tenant_id
    )

    listed = coordinator.list_results(link.voice_session_id)
    assert [item.status for item in listed] == ["ready"]

    delivered, delivery, created = coordinator.deliver_result(
        voice_session_id=link.voice_session_id,
        result_id=result.id,
        request_id="delivery-request-1",
    )
    assert created is True
    assert delivered.status == "delivered"
    assert delivery.status == "delivered"
    assert delivery.playback_status == "claimed"
    coordinator.update_speech_delivery(
        voice_session_id=link.voice_session_id,
        speech_id=delivery.speech_id,
        state="enqueued",
    )
    coordinator.update_speech_delivery(
        voice_session_id=link.voice_session_id,
        speech_id=delivery.speech_id,
        state="started",
    )
    coordinator.update_speech_delivery(
        voice_session_id=link.voice_session_id,
        speech_id=delivery.speech_id,
        state="played",
    )
    db.refresh(delivered)
    db.refresh(delivery)
    assert delivered.status == "delivered"
    assert delivery.status == "delivered"
    assert delivery.playback_status == "played"

    delivered_again, same_delivery, created_again = coordinator.deliver_result(
        voice_session_id=link.voice_session_id,
        result_id=result.id,
        request_id="delivery-request-1",
    )
    assert created_again is False
    assert same_delivery.id == delivery.id
    assert delivered_again.status == "delivered"
    assert (
        db.query(VoiceSpeechDeliveryRecord)
        .filter(VoiceSpeechDeliveryRecord.result_id == result.id)
        .count()
        == 1
    )


def test_released_delivery_can_be_claimed_again(db):
    link = _link(db)
    task = _task(db, link)
    result = capture_voice_task_outcome(db, task)
    assert result is not None
    coordinator = VoiceTaskCoordinator(
        db, workspace_id=link.workspace_id, tenant_id=link.tenant_id
    )

    delivered, _delivery, created = coordinator.deliver_result(
        voice_session_id=link.voice_session_id,
        result_id=result.id,
        request_id="delivery-release-1",
    )
    assert created is True
    assert delivered.status == "delivered"

    released = coordinator.release_delivery(
        voice_session_id=link.voice_session_id,
        result_id=result.id,
        request_id="delivery-release-1",
    )
    assert released.status == "ready"
    assert coordinator.deliveries.get_by_result(
        result.id, tenant_id=link.tenant_id
    ) is None

    _result, _delivery, created_again = coordinator.deliver_result(
        voice_session_id=link.voice_session_id,
        result_id=result.id,
        request_id="delivery-release-2",
    )
    assert created_again is True


def test_failed_result_can_deliver_a_safe_failure_explanation(db):
    link = _link(db)
    task = _task(db, link, status="FAILED", result_text="")
    result = capture_voice_task_outcome(db, task)
    db.commit()
    assert result is not None
    assert result.status == "failed"
    coordinator = VoiceTaskCoordinator(
        db, workspace_id=link.workspace_id, tenant_id=link.tenant_id
    )

    delivered, delivery, created = coordinator.deliver_result(
        voice_session_id=link.voice_session_id,
        result_id=result.id,
        request_id="delivery-failed-1",
    )
    assert created is True
    assert delivered.status == "failed"
    assert delivered.delivered_at is not None
    assert delivery.status == "delivered"
    assert delivery.playback_status == "claimed"
    coordinator.update_speech_delivery(
        voice_session_id=link.voice_session_id,
        speech_id=delivery.speech_id,
        state="played",
    )
    db.refresh(delivered)
    db.refresh(delivery)
    assert delivered.status == "failed"
    assert delivered.delivered_at is not None
    assert delivery.status == "delivered"
    assert delivery.playback_status == "played"


class _SessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    def __enter__(self):
        return self.session

    def __exit__(self, *_args):
        return False


def _voice_session(db, session_id: str = "vs-owner"):
    row = VoiceSessionRecord(
        id=session_id,
        workspace_id="ws-1",
        tenant_id="tenant-1",
        chat_session_id="chat-1",
        owner_user_id="user-1",
        status="active",
    )
    db.add(row)
    db.commit()
    return row


def test_runtime_owner_lease_claim_heartbeat_takeover_and_release(db, monkeypatch):
    _voice_session(db)
    monkeypatch.setattr(voice_events, "SessionLocal", _SessionFactory(db))

    assert voice_events.claim_runtime_owner("vs-owner", "owner-a", lease_seconds=30)
    assert not voice_events.claim_runtime_owner("vs-owner", "owner-b", lease_seconds=30)
    assert not voice_events.heartbeat_runtime_owner("vs-owner", "owner-b", lease_seconds=30)
    # ``force`` describes a reconnect attempt; it must not steal a live lease
    # while the old worker can still be emitting media.  Takeover is allowed
    # once the durable lease has expired.
    row = db.get(VoiceSessionRecord, "vs-owner")
    assert row is not None
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    row.runtime_owner_lease_until = expired
    db.commit()
    assert voice_events.claim_runtime_owner(
        "vs-owner", "owner-b", lease_seconds=30, force=True
    )
    assert not voice_events.release_runtime_owner("vs-owner", "owner-a")
    assert not voice_events.heartbeat_runtime_owner("vs-owner", "owner-a", lease_seconds=30)
    assert voice_events.heartbeat_runtime_owner("vs-owner", "owner-b", lease_seconds=30)
    assert voice_events.release_runtime_owner("vs-owner", "owner-b")

    db.refresh(db.get(VoiceSessionRecord, "vs-owner"))
    row = db.get(VoiceSessionRecord, "vs-owner")
    assert row.runtime_owner_id is None
    assert row.runtime_owner_lease_until is None


def test_expired_owner_lease_can_be_taken_over_without_force(db, monkeypatch):
    row = _voice_session(db, "vs-expired")
    row.runtime_owner_id = "dead-owner"
    row.runtime_owner_lease_until = datetime.now(timezone.utc).replace(
        microsecond=0
    )
    db.commit()
    monkeypatch.setattr(voice_events, "SessionLocal", _SessionFactory(db))

    # The owner is immediately treated as expired after moving the lease into
    # the past; a new instance can recover without a force flag.
    row.runtime_owner_lease_until = datetime.now(timezone.utc).replace(
        microsecond=0
    )
    row.runtime_owner_lease_until = row.runtime_owner_lease_until.replace(
        year=row.runtime_owner_lease_until.year - 1
    )
    db.commit()
    assert voice_events.claim_runtime_owner("vs-expired", "owner-new", lease_seconds=30)
    db.refresh(row)
    assert row.runtime_owner_id == "owner-new"


def test_stale_result_cannot_be_delivered(db):
    link = _link(db)
    task = _task(db, link)
    result = capture_voice_task_outcome(db, task)
    db.commit()
    assert result is not None
    coordinator = VoiceTaskCoordinator(
        db, workspace_id=link.workspace_id, tenant_id=link.tenant_id
    )
    coordinator.revise_requirement(link, prompt="new requirement")

    with pytest.raises(AppError) as exc:
        coordinator.deliver_result(
            voice_session_id=link.voice_session_id,
            result_id=result.id,
            request_id="delivery-request-2",
        )
    assert exc.value.code == "voice_result_stale"


def test_cancel_request_is_durable_and_checkpointed(db):
    link = _link(db)
    coordinator = VoiceTaskCoordinator(
        db, workspace_id=link.workspace_id, tenant_id=link.tenant_id
    )
    coordinator.request_cancel(link, reason="user_requested")
    assert link.cancel_requested_at is not None
    assert link.cancel_checkpoint_json["phase"] == "requested"

    task = _task(db, link, status="CANCELLED", result_text="")
    result = capture_voice_task_outcome(db, task)
    db.commit()

    assert result is not None
    assert result.status == "failed"
    assert result.result_type == "cancelled"
    assert link.cancel_acknowledged_at is not None
    assert link.cancel_checkpoint_json["observed_status"] == "CANCELLED"


def test_interrupt_does_not_cancel_background_task(db, monkeypatch):
    link = _link(db)
    service = VoiceSessionService(db, _context(), SimpleNamespace())
    monkeypatch.setattr(
        service, "get_session", lambda *args, **kwargs: SimpleNamespace(id="vs-1")
    )
    monkeypatch.setattr("app.voice.turns.interrupt_turn", lambda *args, **kwargs: None)

    service.interrupt("vs-1")

    db.refresh(link)
    assert link.cancel_requested_at is None
    assert link.status == "RUNNING"


def test_server_owned_tool_policy_does_not_allow_reasoning_tool_escalation():
    assert resolve_voice_tool_policy("reason", ["sandbox_bash"]) == []
    assert resolve_voice_tool_policy(
        "research", ["sandbox_bash", "web_search"]
    ) == [
        "search_web",
        "fetch_web_page",
        "sandbox_download",
        "parallel_web_research",
    ]
    assert resolve_voice_tool_policy(
        "tool", ["search_web", "web_search"]
    ) == ["search_web"]


def _context(user_id: str = "user-1", tenant_id: str = "tenant-1"):
    return SimpleNamespace(
        workspace_id="ws-1",
        workspace=SimpleNamespace(id="ws-1"),
        principal=SimpleNamespace(user_id=user_id, tenant_id=tenant_id),
    )


def test_peer_binding_rejects_another_peer_and_cross_tenant_access(db):
    session = VoiceSessionRecord(
        id="vs-1",
        chat_session_id="chat-1",
        workspace_id="ws-1",
        tenant_id="tenant-1",
        owner_user_id="user-1",
        status="active",
    )
    db.add(session)
    db.commit()

    service = VoiceSessionService(db, _context(), SimpleNamespace())
    service.bind_peer("vs-1", "pc-1")
    assert service.require_runtime_access("vs-1", peer_connection_id="pc-1").id == "vs-1"

    with pytest.raises(AppError) as exc:
        service.require_runtime_access("vs-1", peer_connection_id="pc-other")
    assert exc.value.code == "voice_peer_mismatch"

    other = VoiceSessionService(db, _context(user_id="user-2"), SimpleNamespace())
    with pytest.raises(AppError) as cross_user:
        other.require_runtime_access("vs-1")
    assert cross_user.value.code == "voice_session_not_found"

    cross_tenant = VoiceSessionService(
        db, _context(user_id="user-1", tenant_id="tenant-2"), SimpleNamespace()
    )
    with pytest.raises(AppError) as tenant_exc:
        cross_tenant.require_runtime_access("vs-1")
    assert tenant_exc.value.code == "voice_session_not_found"


def test_close_runner_awaits_real_runtime_stop():
    clear_registry()
    stopped: list[str] = []

    async def stop(reason: str) -> None:
        stopped.append(reason)

    register_runner(
        VoiceRunnerHandle(
            voice_session_id="vs-close",
            chat_session_id="chat-close",
            owner_user_id="user-1",
            stop=stop,
        )
    )

    async def exercise() -> bool:
        return await close_runner("vs-close", reason="delete")

    assert asyncio.run(exercise()) is True
    assert stopped == ["delete"]
    clear_registry()
def test_voice_reliability_migration_backfills_tenant_and_creates_inbox():
    from app.core.migrations.voice_reliability import apply_voice_reliability_migration

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE workspaces (id VARCHAR(64) PRIMARY KEY, tenant_id VARCHAR(64))"))
        connection.execute(text("CREATE TABLE voice_sessions (id VARCHAR(64) PRIMARY KEY, chat_session_id VARCHAR(36), owner_user_id VARCHAR(64), status VARCHAR(24), max_thinking_mode VARCHAR(16), event_seq INTEGER, session_epoch INTEGER, model_id VARCHAR(160), provider_id VARCHAR(80), runtime_ready BOOLEAN, signaling_url VARCHAR(500), context_snapshot JSON, workspace_id VARCHAR(64), created_at DATETIME, updated_at DATETIME)"))
        connection.execute(text("CREATE TABLE voice_task_links (id VARCHAR(64) PRIMARY KEY, voice_session_id VARCHAR(64), subagent_id VARCHAR(120), chat_session_id VARCHAR(36), status VARCHAR(24), thinking_mode VARCHAR(16), title VARCHAR(200), workspace_id VARCHAR(64), created_at DATETIME, updated_at DATETIME)"))
        connection.execute(text("INSERT INTO workspaces (id, tenant_id) VALUES ('ws-1', 'tenant-9')"))
        connection.execute(text("INSERT INTO voice_sessions (id, chat_session_id, owner_user_id, status, workspace_id, created_at, updated_at) VALUES ('vs-1', 'chat-1', 'user-1', 'active', 'ws-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO voice_task_links (id, voice_session_id, subagent_id, chat_session_id, status, title, workspace_id, created_at, updated_at) VALUES ('link-1', 'vs-1', 'task-1', 'chat-1', 'queued', 'Legacy', 'ws-1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))

        apply_voice_reliability_migration(connection)

        session_columns = {column["name"] for column in inspect(connection).get_columns("voice_sessions")}
        assert {"tenant_id", "peer_connection_id", "peer_generation"} <= session_columns
        assert inspect(connection).has_table("voice_result_inbox")
        assert inspect(connection).has_table("voice_speech_deliveries")
        assert connection.execute(text("SELECT tenant_id FROM voice_sessions WHERE id='vs-1'")).scalar_one() == "tenant-9"
        assert connection.execute(text("SELECT tenant_id FROM voice_task_links WHERE id='link-1'")).scalar_one() == "tenant-9"
    engine.dispose()


def test_interrupt_event_records_whether_the_pipeline_already_applied_it(monkeypatch):
    """`turn.interrupted` 必须带上来源，否则 worker 会自己打断自己。

    实测的回路：VAD/RTVI 触发一次 barge-in → journal 采到 InterruptionFrame → 落一条
    `turn.interrupted` → 同一个进程的 VoiceControlWatchdog 下一个 tick 读到它 → 再
    打断一次，而这一击落在紧接着开始的下一轮（打字回合毫秒级起跑）上，把它的 LLM
    流 abort 掉，用户的问题静默丢失。来源为 "pipeline" 就是给 watchdog 看的"已执行"。
    """
    from app.voice import turns

    captured: dict[str, object] = {}

    def _emit(_session_id, event_type, **kwargs):
        captured.update({"type": event_type, **kwargs})
        return {}

    monkeypatch.setattr(turns, "emit_event", _emit)
    # 不传 turn_id 且没有开着的回合：跳过 DB 分支，只验证信封。
    monkeypatch.setattr(turns, "open_turn", lambda _session_id: None)

    turns.interrupt_turn("vs-1", reason="barge_in", origin="pipeline")
    assert captured["type"] == "turn.interrupted"
    assert captured["payload"]["origin"] == "pipeline"

    turns.interrupt_turn("vs-1", reason="client_interrupt")
    assert captured["payload"]["origin"] == "control", "默认必须仍然是要执行的打断"


def test_control_watchdog_skips_self_inflicted_interrupts(monkeypatch):
    """watchdog 只执行"别处请求的"打断；自己造成的那些不能再打一遍。"""
    from app.voice import journal as journal_module

    events = [
        {"event_seq": 1, "type": "turn.interrupted", "turn_id": "t1", "payload": {"origin": "pipeline"}},
        {"event_seq": 2, "type": "turn.interrupted", "turn_id": "t2", "payload": {"origin": "control"}},
        # 老事件没有 origin：按"要执行"处理，不能因为字段缺失就静默丢掉远程打断。
        {"event_seq": 3, "type": "turn.interrupted", "turn_id": "t3", "payload": {"reason": "barge_in"}},
    ]
    monkeypatch.setattr(journal_module, "_read_control", lambda _sid, _cursor: list(events))
    monkeypatch.setattr(
        journal_module, "load_session", lambda _sid: SimpleNamespace(active=True)
    )

    seen: list[str] = []

    async def on_interrupt(event):
        seen.append(str(event.get("turn_id")))

    async def on_model_changed(_event):
        raise AssertionError("context.updated 不应触发")

    async def on_close(_reason):
        raise AssertionError("会话仍然 active，不应关闭")

    watchdog = journal_module.VoiceControlWatchdog(
        voice_session_id="vs-1",
        on_interrupt=on_interrupt,
        on_model_changed=on_model_changed,
        on_close=on_close,
        on_reconcile=None,
    )
    asyncio.run(watchdog.tick())

    assert seen == ["t2", "t3"]
    assert watchdog._cursor == 3, "被跳过的序号也必须推进游标，否则每 tick 都要重读"


def test_control_watchdog_skips_self_authored_context_updates(monkeypatch):
    """模型上的同类回路：`origin="pipeline"` 的 context.updated 是回执，不是指令。

    实测的回路：管线处理一条 context.updated（上下文快照或切换请求）时会写回一条
    带 ``origin="pipeline"`` 的回执 → 同一个 watchdog 下一拍读到自己的回执 → 再跑
    一次模型重指向并再写一条 → 每 tick 一条，直到挂断；同时前端的模型 pin 永远停在
    "将在下次接通后生效"。
    """
    from app.voice import journal as journal_module

    events = [
        {"event_seq": 1, "type": "context.updated", "payload": {"origin": "pipeline", "repointed": True}},
        # 上下文快照与切换请求都要处理：前者要刷新上下文，后者要重指向模型。
        {"event_seq": 2, "type": "context.updated", "payload": {"reason": "context_snapshot"}},
        {"event_seq": 3, "type": "context.updated", "payload": {"reason": "model_switch", "model_id": "m2"}},
    ]
    monkeypatch.setattr(journal_module, "_read_control", lambda _sid, _cursor: list(events))
    monkeypatch.setattr(
        journal_module, "load_session", lambda _sid: SimpleNamespace(active=True)
    )

    seen: list[int] = []

    async def on_model_changed(event):
        seen.append(int(event.get("event_seq")))

    async def on_interrupt(_event):
        raise AssertionError("没有打断事件")

    async def on_close(_reason):
        raise AssertionError("会话仍然 active，不应关闭")

    watchdog = journal_module.VoiceControlWatchdog(
        voice_session_id="vs-1",
        on_interrupt=on_interrupt,
        on_model_changed=on_model_changed,
        on_close=on_close,
        on_reconcile=None,
    )
    asyncio.run(watchdog.tick())

    assert seen == [2, 3], "自己的回执不能再执行一遍"
    assert watchdog._cursor == 3, "被跳过的序号也必须推进游标，否则每 tick 都要重读"


def test_model_change_report_is_marked_as_pipeline_authored():
    """回执必须自带 origin="pipeline"，否则 watchdog 认不出来就会自触发。"""
    from app.voice.embedded_bot import context_change_report

    report = context_change_report(
        {"reason": "model_switch", "model_id": "m2"},
        repointed=True,
        effective_model_id="m2",
        applied_epoch=4,
    )
    assert report["origin"] == "pipeline"
    assert report["repointed"] is True
    assert report["certainty"] == "repointed"
    assert report["applies_to"] == "next_turn"
    assert report["applied_epoch"] == 4
    # 触发请求的字段必须原样带上：客户端要靠它区分"请求了什么"和"实际生效什么"。
    assert report["reason"] == "model_switch"
    assert report["model_id"] == "m2"

    unconfirmed = context_change_report(
        None,
        repointed=False,
        effective_model_id="m1",
        applied_epoch=5,
    )
    assert unconfirmed["origin"] == "pipeline"
    assert unconfirmed["certainty"] == "next_connection"
    assert unconfirmed["effective_model_id"] == "m1"
