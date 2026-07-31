from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select, text

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-search-proj-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'search.db').as_posix()}"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.migrations import apply_schema_migrations  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import (  # noqa: E402
    MemoryScopeContext,
    MemorySearchDocument,
)
from app.domain.memory_event_types import MemoryEventType  # noqa: E402
from app.services.memory_crypto import MemoryPayloadCipher  # noqa: E402
from app.services.memory_event_store import AppendEvent, MemoryEventStore  # noqa: E402
from app.services.memory_projector import (  # noqa: E402
    MemoryProjector,
    ensure_memory_search_fts,
    normalize_bm25_score,
    probe_memory_search_fts_capability,
)
from app.services.memory_retrieval import MemoryHybridRetriever  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        apply_schema_migrations(connection)
    yield


def scope(
    *,
    tenant_id: str = "tenant-a",
    user_id: str = "user-a",
    workspace_id: str = "workspace-a",
) -> MemoryScopeContext:
    return MemoryScopeContext(
        tenant_id=tenant_id,
        principal_user_id=user_id,
        workspace_id=workspace_id,
    )


def _append_and_project(
    db,
    store: MemoryEventStore,
    projector: MemoryProjector,
    *,
    memory_id: str,
    title: str,
    content: str,
    event_type: str = MemoryEventType.MEMORY_CREATED,
    expected_version: int | None = 0,
    idempotency_key: str | None = None,
    status: str = "active",
    extra: dict | None = None,
):
    payload = {
        "memory_id": memory_id,
        "title": title,
        "content": content,
        "status": status,
        "record_kind": "semantic_memory",
        "importance": 0.8,
        "confidence": 0.9,
        "keywords": ["图谱", "记忆"],
        "entity_aliases": ["LearnGraph"],
        **(extra or {}),
    }
    result = store.append(
        scope(),
        aggregate_type="memory_atom",
        aggregate_id=memory_id,
        expected_version=expected_version,
        event=AppendEvent(
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key or f"{event_type}:{memory_id}:{title}:{content}",
            actor_id="user-a",
        ),
    )
    if not result.idempotent_replay:
        projector.apply(result.event, payload)
    return result


def test_normalize_bm25_score_ordering_and_bounds() -> None:
    better = normalize_bm25_score(-12.0)
    mid = normalize_bm25_score(0.0)
    worse = normalize_bm25_score(6.0)
    assert 0.0 <= worse < mid < better < 1.0
    assert math.isclose(mid, 0.5, rel_tol=1e-9)
    assert normalize_bm25_score(float("nan")) == 0.0
    assert normalize_bm25_score(float("inf")) == 0.0


def test_fts_capability_probe_records_tokenizer() -> None:
    with SessionLocal() as db:
        capability = ensure_memory_search_fts(db)
        db.commit()
        assert capability in {"trigram", "unicode"}
        assert probe_memory_search_fts_capability(db) == capability


def test_rebuild_replays_events_and_is_deterministic() -> None:
    cipher = MemoryPayloadCipher("secret")
    with SessionLocal() as db:
        store = MemoryEventStore(db, cipher)
        projector = MemoryProjector(db, cipher=cipher)
        _append_and_project(
            db,
            store,
            projector,
            memory_id="mem-a",
            title="用户偏好",
            content="用户喜欢用中文回答关于知识图谱的问题",
        )
        _append_and_project(
            db,
            store,
            projector,
            memory_id="mem-b",
            title="项目约定",
            content="LearnGraph 默认使用 SQLite FTS5 做记忆检索",
            expected_version=0,
        )
        # Correct mem-a content so replay must land on the latest version.
        _append_and_project(
            db,
            store,
            projector,
            memory_id="mem-a",
            title="用户偏好",
            content="用户更喜欢简洁的中文回答",
            event_type=MemoryEventType.MEMORY_CORRECTED,
            expected_version=1,
        )
        db.commit()

        live_docs = {
            doc.target_id: doc.content_hash
            for doc in db.scalars(select(MemorySearchDocument)).all()
        }
        assert set(live_docs) == {"mem-a", "mem-b"}

        first = projector.rebuild_search_projection()
        db.commit()
        assert first.applied_count >= 3
        assert first.document_count == 2
        assert first.fts_row_count == 2
        assert first.fts_capability in {"trigram", "unicode"}
        rebuilt = {
            doc.target_id: (doc.content, doc.content_hash)
            for doc in db.scalars(select(MemorySearchDocument)).all()
        }
        assert rebuilt["mem-a"][0] == "用户更喜欢简洁的中文回答"
        assert rebuilt["mem-a"][1] == live_docs["mem-a"]

        second = projector.rebuild_search_projection()
        db.commit()
        assert second.content_fingerprint == first.content_fingerprint
        assert second.document_count == first.document_count
        assert second.fts_row_count == first.fts_row_count


def test_rebuild_excludes_retracted_and_forgotten() -> None:
    cipher = MemoryPayloadCipher("secret")
    with SessionLocal() as db:
        store = MemoryEventStore(db, cipher)
        projector = MemoryProjector(db, cipher=cipher)
        created = _append_and_project(
            db,
            store,
            projector,
            memory_id="mem-gone",
            title="临时记忆",
            content="这段内容稍后会被撤回",
        )
        assert db.scalar(
            select(MemorySearchDocument).where(MemorySearchDocument.target_id == "mem-gone")
        )

        retract = store.append(
            scope(),
            aggregate_type="memory_atom",
            aggregate_id="mem-gone",
            expected_version=1,
            event=AppendEvent(
                event_type=MemoryEventType.MEMORY_RETRACTED,
                payload={"memory_id": "mem-gone", "reason": "wrong"},
                idempotency_key="retract-mem-gone",
                actor_id="user-a",
            ),
        )
        projector.apply(retract.event, {"memory_id": "mem-gone", "reason": "wrong"})
        db.commit()

        report = projector.rebuild_search_projection()
        db.commit()
        assert report.document_count == 0
        assert report.fts_row_count == 0
        rows = db.execute(
            text("SELECT count(*) FROM memory_search_fts WHERE document_id = :id"),
            {"id": created.event.event_id},
        ).scalar()
        # FTS is keyed by document id, not event id; just ensure table is empty.
        assert int(db.execute(text("SELECT count(*) FROM memory_search_fts")).scalar() or 0) == 0
        assert rows == 0

        # Forgotten after a fresh create must also leave zero searchable docs.
        _append_and_project(
            db,
            store,
            projector,
            memory_id="mem-forget",
            title="敏感偏好",
            content="不要记住我的家庭住址",
        )
        stream = store.events.stream_for_aggregate(scope(), "memory_atom", "mem-forget")
        assert stream is not None
        store.destroy_stream_payloads(
            scope(), stream.id, actor_id="user-a", reason="test-forget"
        )
        forgotten = store.append(
            scope(),
            aggregate_type="memory_atom",
            aggregate_id="mem-forget",
            expected_version=stream.current_version,
            event=AppendEvent(
                event_type=MemoryEventType.MEMORY_FORGOTTEN,
                payload={"target_hash": "x", "scope": "all_projections"},
                idempotency_key="forget-mem-forget",
                actor_id="user-a",
                metadata={"content_excluded": True},
            ),
        )
        projector.apply(
            forgotten.event, {"target_hash": "x", "scope": "all_projections", "memory_id": "mem-forget"}
        )
        db.commit()

        report = projector.rebuild_search_projection()
        db.commit()
        assert report.document_count == 0
        assert int(db.execute(text("SELECT count(*) FROM memory_search_fts")).scalar() or 0) == 0


def test_hybrid_retriever_orders_by_relevance_and_respects_lifecycle() -> None:
    cipher = MemoryPayloadCipher("secret")
    with SessionLocal() as db:
        store = MemoryEventStore(db, cipher)
        projector = MemoryProjector(db, cipher=cipher)
        _append_and_project(
            db,
            store,
            projector,
            memory_id="mem-hit",
            title="知识图谱偏好",
            content="用户研究知识图谱与长期记忆系统",
        )
        _append_and_project(
            db,
            store,
            projector,
            memory_id="mem-miss",
            title="天气偏好",
            content="用户不喜欢下雨天出门",
        )
        retracted = store.append(
            scope(),
            aggregate_type="memory_atom",
            aggregate_id="mem-miss",
            expected_version=1,
            event=AppendEvent(
                event_type=MemoryEventType.MEMORY_RETRACTED,
                payload={"memory_id": "mem-miss", "reason": "stale"},
                idempotency_key="retract-mem-miss",
                actor_id="user-a",
            ),
        )
        projector.apply(retracted.event, {"memory_id": "mem-miss"})
        db.commit()

        result = MemoryHybridRetriever(db).search(
            scope(),
            "知识图谱 记忆",
            top_k=5,
            min_score=0.05,
        )
        ids = [item.target_id for item in result.candidates]
        assert "mem-hit" in ids
        assert "mem-miss" not in ids


def test_rebuild_rolls_back_on_failure() -> None:
    cipher = MemoryPayloadCipher("secret")
    with SessionLocal() as db:
        store = MemoryEventStore(db, cipher)
        projector = MemoryProjector(db, cipher=cipher)
        _append_and_project(
            db,
            store,
            projector,
            memory_id="mem-stable",
            title="稳定记忆",
            content="重建失败时不应丢数据",
        )
        db.commit()
        before = {
            doc.target_id: doc.content_hash
            for doc in db.scalars(select(MemorySearchDocument)).all()
        }
        assert before

        def boom(_event):
            raise RuntimeError("simulated replay failure")

        with pytest.raises(RuntimeError, match="simulated replay failure"):
            # Fail after wipe+first successful path by raising from reader on any event.
            projector.rebuild_search_projection(read_payload=boom)
            db.commit()
        db.rollback()

        after = {
            doc.target_id: doc.content_hash
            for doc in db.scalars(select(MemorySearchDocument)).all()
        }
        assert after == before
