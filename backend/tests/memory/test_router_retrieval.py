from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-router-tests-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'router.db').as_posix()}"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.migrations import apply_schema_migrations  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import (  # noqa: E402
    MemoryRetrievalTrace,
    MemoryScopeContext,
    MemorySearchDocument,
)
from app.services.memory_projector import ensure_memory_search_fts  # noqa: E402
from app.services.memory_retrieval import MemoryHybridRetriever  # noqa: E402
from app.services.memory_router import (  # noqa: E402
    MemoryRouter,
    _detect_intents,
    _routes_from_signals,
)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_schema_migrations(conn)
    yield


def scope(
    *,
    tenant: str = "t1",
    user: str = "u1",
    workspace: str = "w1",
    task_id: str | None = None,
) -> MemoryScopeContext:
    return MemoryScopeContext(
        tenant_id=tenant,
        principal_user_id=user,
        workspace_id=workspace,
        task_id=task_id,
    )


def _seed_doc(
    db,
    *,
    target_id: str = "mem-a",
    subject: str = "title",
    content: str = "body",
    tenant: str = "t1",
    user: str | None = "u1",
    workspace: str | None = "w1",
    task_id: str | None = None,
    layer: str = "L3",
    status: str = "active",
    sensitivity: str = "normal",
    confidence: float = 0.8,
    importance: float = 0.6,
    valid_until: datetime | None = None,
):
    doc = MemorySearchDocument(
        target_type="memory",
        target_id=target_id,
        tenant_id=tenant,
        subject_user_id=user,
        workspace_id=workspace,
        task_id=task_id,
        source_event_id=f"evt:{target_id}",
        content_hash="abc",
        subject=subject,
        content=content,
        memory_layer=layer,
        status=status,
        sensitivity=sensitivity,
        confidence=confidence,
        importance=importance,
        valid_until=valid_until,
    )
    db.add(doc)
    db.flush()
    ensure_memory_search_fts(db)
    return doc


# ── Intent detection ──────────────────────────────────────────────────────────


def test_detect_intents_continue_task():
    signals = _detect_intents("请继续上次的任务")
    intents = {s.intent for s in signals}
    assert "continue_task" in intents
    assert any(s.confidence >= 0.8 for s in signals if s.intent == "continue_task")


def test_detect_intents_decision_recall():
    signals = _detect_intents("为什么决定用 SQLite 而不是 PostgreSQL")
    intents = {s.intent for s in signals}
    assert "decision_recall" in intents


def test_detect_intents_file_reference():
    signals = _detect_intents("上传的 PDF 文件在哪里")
    intents = {s.intent for s in signals}
    assert "file_reference" in intents


def test_detect_intents_learning_mastery():
    signals = _detect_intents("我掌握了多少知识点")
    intents = {s.intent for s in signals}
    assert "learning_mastery" in intents


def test_detect_intents_user_preference():
    signals = _detect_intents("我的习惯是每天早上复习")
    intents = {s.intent for s in signals}
    assert "user_preference" in intents


def test_detect_intents_strategy():
    signals = _detect_intents("上次怎么解决这个 bug 的")
    intents = {s.intent for s in signals}
    assert "strategy_recall" in intents


def test_detect_intents_empty_query_falls_back_to_memory():
    signals = _detect_intents("hello")
    assert signals == []
    routes = _routes_from_signals(signals)
    assert routes == ("memory",)


def test_detect_intents_multi_intent():
    signals = _detect_intents("继续上次的 PDF 文件复习任务")
    intents = {s.intent for s in signals}
    assert "continue_task" in intents
    assert "file_reference" in intents
    assert "learning_mastery" in intents


def test_routes_from_signals_ordering():
    """Higher-confidence intents produce earlier routes."""
    from app.services.memory_router import IntentSignal

    signals = [
        IntentSignal("learning_mastery", 0.85, "掌握"),
        IntentSignal("continue_task", 0.90, "继续"),
    ]
    routes = _routes_from_signals(signals)
    # continue_task maps to (task_state, episode, project_decision)
    # learning_mastery maps to (learning_state,)
    # All continue_task routes (conf=0.90) come before learning_mastery (conf=0.85)
    task_idx = routes.index("task_state")
    learning_idx = routes.index("learning_state")
    assert task_idx < learning_idx


# ── Route layer mapping ───────────────────────────────────────────────────────


def test_router_routes_to_correct_layers():
    with SessionLocal() as db:
        retriever = MemoryHybridRetriever(db)
        router = MemoryRouter(retriever)
        result = router.route(scope(), "请继续上次的任务")
        assert "task_state" in result.routes
        # task_state maps to L2
        from app.services.memory_router import ROUTE_LAYER_MAP

        layers: set[str] = set()
        for route in result.routes:
            layers.update(ROUTE_LAYER_MAP[route])
        assert "L2" in layers


# ── Scope isolation hard filters ──────────────────────────────────────────────


def test_cross_tenant_excluded():
    with SessionLocal() as db:
        _seed_doc(db, tenant="t1", target_id="mem-t1")
        _seed_doc(db, tenant="t2", target_id="mem-t2")
        db.commit()
        result = MemoryHybridRetriever(db).search(scope(tenant="t1"), "title body")
        ids = [c.target_id for c in result.candidates]
        assert "mem-t1" in ids
        assert "mem-t2" not in ids


def test_cross_user_excluded():
    with SessionLocal() as db:
        _seed_doc(db, user="u1", target_id="mem-u1")
        _seed_doc(db, user="u2", target_id="mem-u2")
        db.commit()
        result = MemoryHybridRetriever(db).search(scope(user="u1"), "title body")
        ids = [c.target_id for c in result.candidates]
        assert "mem-u1" in ids
        assert "mem-u2" not in ids


def test_cross_workspace_excluded():
    with SessionLocal() as db:
        _seed_doc(db, workspace="w1", target_id="mem-w1")
        _seed_doc(db, workspace="w2", target_id="mem-w2")
        db.commit()
        result = MemoryHybridRetriever(db).search(scope(workspace="w1"), "title body")
        ids = [c.target_id for c in result.candidates]
        assert "mem-w1" in ids
        assert "mem-w2" not in ids


def test_sensitivity_hard_filter():
    with SessionLocal() as db:
        _seed_doc(db, sensitivity="normal", target_id="mem-norm")
        _seed_doc(db, sensitivity="restricted", target_id="mem-restr")
        db.commit()
        s = scope()
        # scope allows public/normal/private by default; restricted is excluded
        # by the SQL WHERE IN filter, so the doc never reaches Python scoring.
        result = MemoryHybridRetriever(db).search(s, "title body")
        ids = [c.target_id for c in result.candidates]
        assert "mem-norm" in ids
        assert "mem-restr" not in ids


def test_expired_document_excluded():
    with SessionLocal() as db:
        _seed_doc(db, target_id="mem-alive")
        _seed_doc(
            db,
            target_id="mem-expired",
            valid_until=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.commit()
        result = MemoryHybridRetriever(db).search(scope(), "title body")
        ids = [c.target_id for c in result.candidates]
        assert "mem-alive" in ids
        assert "mem-expired" not in ids
        assert result.excluded["expired"] >= 1


def test_non_active_status_excluded():
    with SessionLocal() as db:
        _seed_doc(db, status="active", target_id="mem-active")
        _seed_doc(db, status="suppressed", target_id="mem-suppressed")
        _seed_doc(db, status="retracted", target_id="mem-retracted")
        db.commit()
        result = MemoryHybridRetriever(db).search(scope(), "title body")
        ids = [c.target_id for c in result.candidates]
        assert "mem-active" in ids
        assert "mem-suppressed" not in ids
        assert "mem-retracted" not in ids


def test_empty_result_returns_zero_candidates():
    with SessionLocal() as db:
        db.commit()
        result = MemoryHybridRetriever(db).search(scope(), "完全不存在的内容")
        assert len(result.candidates) == 0
        assert result.excluded.get("quality", 0) == 0


# ── Trace persistence ─────────────────────────────────────────────────────────


def test_router_persists_trace():
    with SessionLocal() as db:
        _seed_doc(db)
        db.commit()
        retriever = MemoryHybridRetriever(db)
        router = MemoryRouter(retriever, db=db)
        result = router.route(scope(), "请继续上次的任务")
        assert result.trace_id is not None
        trace = db.get(MemoryRetrievalTrace, result.trace_id)
        assert trace is not None
        assert trace.tenant_id == "t1"
        assert trace.workspace_id == "w1"
        assert trace.subject_user_id == "u1"
        assert trace.query_hash != ""  # hash, not plaintext
        assert "continue_task" in [s["intent"] for s in trace.signals_json]
        assert trace.candidate_count >= 0
        assert trace.strategy == "hybrid_memory_v2"
        assert trace.status == "completed"
        assert trace.latency_ms >= 0
        assert trace.excluded_counts_json != {}


def test_trace_never_stores_plaintext_query():
    with SessionLocal() as db:
        _seed_doc(db)
        db.commit()
        retriever = MemoryHybridRetriever(db)
        router = MemoryRouter(retriever, db=db)
        result = router.route(scope(), "这是一个秘密查询")
        assert result.trace_id is not None
        trace = db.get(MemoryRetrievalTrace, result.trace_id)
        assert trace is not None
        # The only string fields are query_hash (sha256 hex) and agent_id
        assert "秘密" not in trace.query_hash
        assert "秘密" not in (trace.agent_id or "")


def test_trace_without_db_does_not_crash():
    with SessionLocal() as db:
        _seed_doc(db)
        db.commit()
        retriever = MemoryHybridRetriever(db)
        router = MemoryRouter(retriever)  # no db
        result = router.route(scope(), "请继续上次的任务")
        assert result.trace_id is None
        assert len(result.routes) > 0


def test_router_returns_signals():
    with SessionLocal() as db:
        retriever = MemoryHybridRetriever(db)
        router = MemoryRouter(retriever)
        result = router.route(scope(), "请继续上次的任务")
        assert len(result.signals) > 0
        intents = {s.intent for s in result.signals}
        assert "continue_task" in intents
