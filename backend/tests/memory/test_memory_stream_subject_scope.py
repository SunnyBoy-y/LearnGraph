"""memory_streams 主体维度回归（复现"Memory extraction failed"线上故障）。

背景：`memory_streams` 的唯一键是 (tenant_id, aggregate_type, aggregate_id)，
但仓储查询额外按调用方主体过滤。工作区级记忆（`memory_records.subject_user_id`
为 NULL）会同时被两种调用方写入 —— 交互路径用登录用户，`system:memory-extraction`
抽取 sweep 用系统主体。谁第二个写，谁就查不到已有的 stream，转而 INSERT 撞唯一键，
整个 sweep 报 `UNIQUE constraint failed: memory_streams...` 并中断该 session。

本文件盯住两条：
1. 主体不同的第二个写入者必须**复用**已有 stream（版本 +1），而不是抛 IntegrityError；
2. 新建 stream 的主体跟随 record 的归属（无主记录 → NULL），从源头保证两种调用方都能查到。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import Base
from app.domain import models as m
from app.domain.memory_event_models import MemoryScopeContext, MemoryStream
from app.services.memory_event_ingestor import event_cipher_from_settings
from app.services.memory_event_store import AppendEvent, MemoryEventStore

WORKSPACE = "ws-stream-subject"
TENANT = "local-tenant"
USER_ACTOR = "user-stream-subject"
SYSTEM_ACTOR = "system:memory-extraction"
AGGREGATE_ID = "lgm_stream_subject_0001"


def _scope(principal: str) -> MemoryScopeContext:
    return MemoryScopeContext(
        tenant_id=TENANT, principal_user_id=principal, workspace_id=WORKSPACE
    )


def _event(suffix: str) -> AppendEvent:
    return AppendEvent(
        event_type="memory.created",
        payload={"memory_id": AGGREGATE_ID, "revision": 1},
        idempotency_key=f"test:{AGGREGATE_ID}:{suffix}",
        producer="api",
        actor_id=suffix,
    )


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_local() as session:
        session.add(
            m.Workspace(
                id=WORKSPACE,
                tenant_id=TENANT,
                owner_user_id=USER_ACTOR,
                name="stream subject",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def _store(db: Session) -> MemoryEventStore:
    return MemoryEventStore(db, event_cipher_from_settings(get_settings()))


def test_second_writer_with_a_different_subject_adopts_the_stream(db: Session) -> None:
    """线上场景：用户路径先建 stream，抽取 sweep 以系统主体再写同一条记忆。"""

    store = _store(db)
    store.append(
        _scope(USER_ACTOR),
        aggregate_type="memory_atom",
        aggregate_id=AGGREGATE_ID,
        expected_version=None,
        event=_event("user"),
    )
    db.commit()

    # 这一句在修复前抛 IntegrityError（UNIQUE constraint failed: memory_streams...）
    result = store.append(
        _scope(SYSTEM_ACTOR),
        aggregate_type="memory_atom",
        aggregate_id=AGGREGATE_ID,
        expected_version=None,
        event=_event("system"),
    )

    assert result.event.stream_version == 2
    streams = db.scalars(
        select(MemoryStream).where(MemoryStream.aggregate_id == AGGREGATE_ID)
    ).all()
    assert len(streams) == 1, "同一 aggregate 只能有一个 stream（唯一键）"
    assert streams[0].current_version == 2
    assert result.event.stream_id == streams[0].id


def test_stream_subject_follows_the_record_owner_not_the_caller(db: Session) -> None:
    """工作区级记录（无主）新建 stream 时主体必须是 NULL，两种调用方都能查到。"""

    store = _store(db)
    store.append(
        _scope(SYSTEM_ACTOR),
        aggregate_type="memory_atom",
        aggregate_id=AGGREGATE_ID,
        expected_version=None,
        event=_event("system"),
        stream_subject_user_id=None,
    )
    db.commit()

    stream = db.scalar(
        select(MemoryStream).where(MemoryStream.aggregate_id == AGGREGATE_ID)
    )
    assert stream is not None
    assert stream.subject_user_id is None

    # 登录用户随后写入同一条无主记忆：直接命中（不依赖兜底查询）
    result = store.append(
        _scope(USER_ACTOR),
        aggregate_type="memory_atom",
        aggregate_id=AGGREGATE_ID,
        expected_version=None,
        event=_event("user"),
        stream_subject_user_id=None,
    )
    assert result.event.stream_version == 2
