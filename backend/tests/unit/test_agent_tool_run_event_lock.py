"""Regression tests for the goal/agent-mode tool-round SQLite lock fix.

背景（2026-08-11）：同一轮 provider ``tool_calls`` 有 2+ 个工具时，第二个工具
执行完成后标记 ``tool_record`` completed 的提交会瞬时 ``database is locked``
（08:43 lg_goal_edit_draft / 07:20 lg_skill_read 两次线上复现）。根因是
``_record_agent_run_event`` 的幂等键按 run_id（助手消息）而非按工具调用唯一，
第二个工具命中 409 → SAVEPOINT 回滚 → 随后的 tool_record 提交与共享 session
连接状态竞争。

修复：
1. 幂等键加入 ``tool_call_id`` —— 同一轮每个工具都追加自己的 agent_run 事件；
2. ``_append_event_locked_retry`` —— 工具完成提交遇瞬时锁时 rollback→redo→
   重追加→重提交，流不再被打死。

本测试直接驱动 ChatService 内部方法，用临时文件 SQLite 隔离，不依赖网络。
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event as sa_event, select
from sqlalchemy.orm import sessionmaker

# 必须先 import，让 memory 事件表注册进 Base.metadata（create_all 才建表）。
from app.domain import memory_event_models  # noqa: F401
from app.domain.memory_event_models import MemoryEvent, MemoryStream
from app.domain.models import (
    ChatSession,
    Message,
    MessagePartRecord,
    MessageStreamEvent,
    MessageVersion,
)
from app.services.chat import ChatService


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 1},
    )

    @sa_event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=500")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    from app.domain import extension_models, models  # noqa: F401

    from app.core.database import Base

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session(engine):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        yield db


@pytest.fixture()
def service(session):
    # ChatService.__init__ 只存引用；测试只调用不触网的内部方法。
    provider = SimpleNamespace(
        provider_id="test-provider",
        remote_capability=False,
        model_id="test-model",
    )
    return ChatService(session, "ws-1", "user-1", provider)


@pytest.fixture()
def chat_rows(service):
    """最小 FK 数据：session → message → version → tool part。"""
    session_row = ChatSession(workspace_id="ws-1", title="测试会话", session_kind="main")
    service.db.add(session_row)
    service.db.flush()
    message = Message(
        workspace_id="ws-1",
        session_id=session_row.id,
        role="assistant",
        content="",
    )
    service.db.add(message)
    service.db.flush()
    version = MessageVersion(
        workspace_id="ws-1",
        message_id=message.id,
        version=1,
        status="streaming",
    )
    service.db.add(version)
    service.db.flush()
    tool_record = MessagePartRecord(
        workspace_id="ws-1",
        message_version_id=version.id,
        ordinal=0,
        part_type="tool_call",
        status="streaming",
        content="",
        data={},
    )
    service.db.add(tool_record)
    service.db.commit()
    return {
        "session_id": session_row.id,
        "message_id": message.id,
        "version_id": version.id,
        "tool_record": tool_record,
    }


def _agent_run_events(db, run_id: str) -> list[MemoryEvent]:
    stream = db.scalar(
        select(MemoryStream).where(MemoryStream.aggregate_id == run_id)
    )
    if stream is None:
        return []
    return list(
        db.scalars(
            select(MemoryEvent)
            .where(MemoryEvent.stream_id == stream.id)
            .order_by(MemoryEvent.stream_version)
        ).all()
    )


# ---------------------------------------------------------------------------
# 修复 1：agent_run 幂等键按工具调用唯一化
# ---------------------------------------------------------------------------


def test_second_tool_call_appends_own_agent_run_event(service) -> None:
    """同一轮两个工具（同 run_id、同 succeeded）各自追加事件，不再 409。"""
    run_id = "run_msg-12345678-9012-3456-7890-abcdef123456"
    # 第一个工具：创建 run 流 + 事件 v1
    service._record_agent_run_event(
        "sess-1",
        run_id,
        succeeded=True,
        output="goal created",
        meta={"status": "completed"},
        sources=[],
        tool_call_id="call-0001",
    )
    # 第二个工具：同 run_id，但 tool_call_id 不同 → 应追加事件 v2 而非 409 冲突
    service._record_agent_run_event(
        "sess-1",
        run_id,
        succeeded=True,
        output="draft card opened",
        meta={"status": "completed"},
        sources=[],
        tool_call_id="call-0002",
    )
    service.db.commit()

    events = _agent_run_events(service.db, run_id)
    assert len(events) == 2, "同一 run 的两个工具都必须追加各自的事件"
    assert [item.stream_version for item in events] == [1, 2]
    keys = {item.idempotency_key for item in events}
    assert len(keys) == 2, "幂等键必须按工具调用唯一"
    assert any(key.endswith(":call-0001") for key in keys)
    assert any(key.endswith(":call-0002") for key in keys)


def test_same_legacy_key_second_call_conflicts_and_is_swallowed(service) -> None:
    """对照：旧幂等键（不带 tool_call_id）第二次调用命中 409 并被吞掉。

    这正是修复前第二个工具事件丢失、触发 SAVEPOINT 回滚链的路径。
    """
    run_id = "run_msg-abcdef12-3456-7890-abcd-ef1234567890"
    service._record_agent_run_event(
        "sess-1",
        run_id,
        succeeded=True,
        output="first",
        meta={"status": "completed"},
        sources=[],
        tool_call_id="",
    )
    # 第二次同键不同 payload → MemoryEventStore 抛 409，_record_agent_run_event
    # 的 except 吞掉，SAVEPOINT 回滚，不向调用方抛异常。
    service._record_agent_run_event(
        "sess-1",
        run_id,
        succeeded=True,
        output="second payload differs",
        meta={"status": "completed"},
        sources=[],
        tool_call_id="",
    )
    service.db.commit()

    events = _agent_run_events(service.db, run_id)
    assert len(events) == 1, "旧键下第二个工具的事件被 409 丢弃（修复前行为）"


# ---------------------------------------------------------------------------
# 修复 2：_append_event_locked_retry 瞬时锁重试
# ---------------------------------------------------------------------------


def test_append_event_locked_retry_recovers_from_real_lock(
    service, chat_rows
) -> None:
    """真实写锁竞争下：前几次提交失败，重试后成功，事件不重复。"""
    engine = service.db.get_bind()
    tool_record = chat_rows["tool_record"]

    def redo() -> None:
        tool_record.status = "completed"
        tool_record.content = "工具调用完成"
        tool_record.data = {
            **tool_record.data,
            "output": {"status": "completed"},
        }

    # holder 连接持有 WAL 写锁 1.6s（busy_timeout=500ms → 前几次尝试必失败）。
    # 0 行 UPDATE 同样把事务升级为写事务并持有写锁，且不违反任何约束。
    holder = engine.connect()
    holder.exec_driver_sql("BEGIN")
    holder.exec_driver_sql(
        "UPDATE message_parts SET content = content WHERE id = 'definitely-not-there'"
    )

    def release() -> None:
        time.sleep(1.6)
        holder.exec_driver_sql("COMMIT")

    threading.Thread(target=release, daemon=True).start()
    started = time.monotonic()
    try:
        envelope = service._append_event_locked_retry(
            redo=redo,
            session_id=chat_rows["session_id"],
            message_id=chat_rows["message_id"],
            message_version_id=chat_rows["version_id"],
            part_id=tool_record.id,
            sequence=1,
            event_type="part.completed",
            payload={
                "part": {
                    "id": tool_record.id,
                    "type": "tool_call",
                    "status": "completed",
                    "content": "工具调用完成",
                    "data": tool_record.data,
                    "sequence": 0,
                }
            },
        )
    finally:
        holder.close()

    elapsed = time.monotonic() - started
    assert envelope.type == "part.completed"
    assert elapsed >= 0.5, "至少经历过一次锁等待后恢复"
    # 恰好 1 条 part.completed 事件（失败尝试的回滚没有产生重复事件）。
    events = list(
        service.db.scalars(
            select(MessageStreamEvent).where(
                MessageStreamEvent.message_version_id == chat_rows["version_id"],
                MessageStreamEvent.event_type == "part.completed",
            )
        ).all()
    )
    assert len(events) == 1
    # redo 在失败后重放了 tool_record 字段。
    refreshed = service.db.get(MessagePartRecord, tool_record.id)
    assert refreshed.status == "completed"
    assert refreshed.content == "工具调用完成"


def test_append_event_locked_retry_fast_path_without_lock(
    service, chat_rows
) -> None:
    """无锁时一次成功，redo 不执行（正常路径不受影响）。"""
    tool_record = chat_rows["tool_record"]

    def redo() -> None:
        tool_record.status = "completed"

    envelope = service._append_event_locked_retry(
        redo=redo,
        session_id=chat_rows["session_id"],
        message_id=chat_rows["message_id"],
        message_version_id=chat_rows["version_id"],
        part_id=tool_record.id,
        sequence=1,
        event_type="part.completed",
        payload={"part": {"id": tool_record.id, "type": "tool_call", "status": "streaming"}},
    )
    assert envelope.type == "part.completed"
    events = list(
        service.db.scalars(
            select(MessageStreamEvent).where(
                MessageStreamEvent.message_version_id == chat_rows["version_id"],
                MessageStreamEvent.event_type == "part.completed",
            )
        ).all()
    )
    assert len(events) == 1
    # redo 未执行：tool_record 保持原值。
    refreshed = service.db.get(MessagePartRecord, tool_record.id)
    assert refreshed.status == "streaming"
