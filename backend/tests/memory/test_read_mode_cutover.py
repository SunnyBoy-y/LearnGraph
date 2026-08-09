"""M5 read-mode cutover, manifest receipts, and degraded retrieval tests."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain import models as m
from app.domain.memory_event_models import MemoryScopeContext, MemorySearchDocument
from app.domain.schemas.context_builds import ContextBuildRequest
from app.services.context_builder import ContextBuilder
from app.services.memory_retrieval import MemoryHybridRetriever
from app.services.memory_router import MemoryRouter

WORKSPACE = "ws-read-mode"
ACTOR = "user-read-mode"
TENANT = "local-tenant"


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        session.add(
            m.Workspace(
                id=WORKSPACE,
                tenant_id=TENANT,
                owner_user_id=ACTOR,
                name="read mode",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def _doc(
    db: Session,
    *,
    doc_id: str,
    target_id: str = "mem-rust",
    status: str = "active",
    content: str = "Rust 所有权规定一个值同时只能有一个所有者",
) -> MemorySearchDocument:
    doc = MemorySearchDocument(
        id=doc_id,
        target_type="memory",
        target_id=target_id,
        target_version=1,
        memory_layer="L3",
        memory_type="semantic_memory",
        subject="Rust 所有权",
        content=content,
        tenant_id=TENANT,
        subject_user_id=ACTOR,
        workspace_id=WORKSPACE,
        status=status,
        sensitivity="normal",
        importance=0.8,
        confidence=0.9,
        source_event_id=f"event-{doc_id}",
        content_hash=f"hash-{doc_id}",
    )
    db.add(doc)
    db.commit()
    return doc


def test_defaults_use_event_reads() -> None:
    settings = Settings()
    assert settings.memory_read_mode == "events"
    assert settings.memory_context_builder_v2 is True


def test_exclusion_reason_codes_cover_six_cases() -> None:
    cases = {
        "out_of_scope": ["CROSS_WORKSPACE"],
        "expired": ["EXPIRED"],
        "cancelled": ["SUPERSEDED"],
        "suppressed": ["NO_PERMISSION"],
    }
    for status, expected in cases.items():
        item = type(
            "C",
            (),
            {
                "status": status,
                "score": 0.9,
            },
        )()
        assert ContextBuilder._reason_codes_for(item) == expected
    low = type("C", (), {"status": "active", "score": 0.1})()
    assert ContextBuilder._reason_codes_for(low) == ["LOW_RELEVANCE"]
    budget = type("C", (), {"status": "active", "score": 0.9})()
    assert "BUDGET_EXCEEDED" not in ContextBuilder._reason_codes_for(budget)


def test_budget_excluded_manifest_records_real_receipt(db: Session) -> None:
    _doc(db, doc_id="doc-budget", target_id="mem-budget", content="Rust 所有权" * 300)
    scope = MemoryScopeContext(
        tenant_id=TENANT,
        principal_user_id=ACTOR,
        workspace_id=WORKSPACE,
    )
    built = ContextBuilder(
        db, MemoryRouter(MemoryHybridRetriever(db), db=db)
    ).build(
        scope,
        ContextBuildRequest(
            query="Rust 所有权",
            token_budget=256,
            agent_id="main_agent",
        ),
    )
    assert built.view.excluded_memories
    assert any(
        "BUDGET_EXCEEDED" in item.reason_codes
        for item in built.view.excluded_memories
    )
    assert built.view.manifest_status == "excluded"
    assert built.view.injected_count == 0
    assert built.view.excluded_count >= 1


def test_fts_still_recalls_when_embedding_is_disabled(db: Session) -> None:
    _doc(db, doc_id="doc-fts", target_id="mem-fts")
    scope = MemoryScopeContext(
        tenant_id=TENANT,
        principal_user_id=ACTOR,
        workspace_id=WORKSPACE,
    )
    result = MemoryHybridRetriever(db).search(
        scope,
        "Rust 所有权",
        top_k=5,
        min_score=0.0,
    )
    assert any(item.target_id == "mem-fts" for item in result.candidates)
    assert result.degraded_modes == ()
