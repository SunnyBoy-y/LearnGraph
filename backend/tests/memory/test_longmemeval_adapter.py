from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.memory_event_models import MemoryEvent, MemoryScopeContext, MemorySearchDocument
from app.domain.models import MemoryEvidence, User, Workspace
from app.services.longmemeval_adapter import chunk_text, parse_session_date
from app.services.memory_retrieval import MemoryHybridRetriever
from app.services.longmemeval_adapter import LongMemEvalAdapter


@pytest.fixture()
def benchmark_db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(
            id="longmemeval-user",
            tenant_id="longmemeval-tenant",
            username="longmemeval",
            username_normalized="longmemeval",
            password_hash="test",
        )
        workspace = Workspace(
            id="longmemeval-workspace",
            tenant_id="longmemeval-tenant",
            owner_user_id=user.id,
            name="LongMemEval",
        )
        db.add_all([user, workspace])
        db.commit()
        yield db
    engine.dispose()


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        memory_event_master_key="longmemeval-test-key",
        master_key="longmemeval-test-master",
        master_key_version=1,
        env="test",
    )


def test_session_date_is_historical_and_deterministic() -> None:
    observed = parse_session_date("2023/05/20 (Sat) 02:21", "UTC")
    assert observed == datetime(2023, 5, 20, 2, 21, tzinfo=timezone.utc)
    assert observed.year != datetime.now(timezone.utc).year


def test_date_only_uses_requested_timezone() -> None:
    observed = parse_session_date("2024-01-10", "Asia/Shanghai")
    assert observed.isoformat() == "2024-01-09T16:00:00+00:00"


def test_chunking_preserves_text_and_order() -> None:
    source = "a" * 4_001 + "b"
    chunks = chunk_text(source, max_chars=3_500)
    assert len(chunks) == 2
    assert "".join(chunks) == source
    assert [index for index, _ in enumerate(chunks)] == [0, 1]


def test_ingest_persists_external_source_and_historical_event_time(benchmark_db: Session) -> None:
    user = benchmark_db.get(User, "longmemeval-user")
    workspace = benchmark_db.get(Workspace, "longmemeval-workspace")
    assert user is not None and workspace is not None
    adapter = LongMemEvalAdapter(benchmark_db, _settings(), workspace, user)
    adapter.ingest_session(
        question_id="q-time",
        session_id="answer_historical",
        session_date="2023/05/20 (Sat) 02:21",
        timezone_name="UTC",
        turns=[
            {"role": "user", "content": "I graduated with Business Administration."},
            {"role": "assistant", "content": "Your degree is Business Administration."},
        ],
    )
    evidence = benchmark_db.scalars(select(MemoryEvidence)).all()
    assert {row.authorship for row in evidence} == {"user", "assistant"}
    assert all(row.observed_at.year == 2023 for row in evidence)
    assert {row.derived_from[0]["external_session_id"] for row in evidence} == {"answer_historical"}
    assistant = next(row for row in evidence if row.authorship == "assistant")
    assert assistant.profile_eligible is False
    docs = benchmark_db.scalars(select(MemorySearchDocument)).all()
    assert all(row.metadata_json["external_source"]["question_id"] == "q-time" for row in docs)

    event = benchmark_db.scalar(select(MemoryEvent))
    assert event is not None and event.occurred_at.year == 2023


def test_retrieval_returns_external_session_id(benchmark_db: Session) -> None:
    user = benchmark_db.get(User, "longmemeval-user")
    workspace = benchmark_db.get(Workspace, "longmemeval-workspace")
    assert user is not None and workspace is not None
    LongMemEvalAdapter(benchmark_db, _settings(), workspace, user).ingest_session(
        question_id="q-recall",
        session_id="answer_recall",
        session_date="2023-01-10",
        timezone_name="UTC",
        turns=[{"role": "user", "content": "My graduation degree was Business Administration."}],
    )
    result = MemoryHybridRetriever(benchmark_db).search(
        MemoryScopeContext(
            tenant_id=user.tenant_id,
            principal_user_id=user.id,
            workspace_id=workspace.id,
        ),
        "What degree did I graduate with?",
        top_k=3,
        min_score=0.2,
    )
    assert result.candidates
    assert result.candidates[0].external_source["external_session_id"] == "answer_recall"


def test_latest_historical_observation_wins_equal_score(benchmark_db: Session) -> None:
    user = benchmark_db.get(User, "longmemeval-user")
    workspace = benchmark_db.get(Workspace, "longmemeval-workspace")
    assert user is not None and workspace is not None
    adapter = LongMemEvalAdapter(benchmark_db, _settings(), workspace, user)
    for session_id, date in (("old_session", "2023-01-10"), ("new_session", "2023-02-10")):
        adapter.ingest_session(
            question_id="q-update",
            session_id=session_id,
            session_date=date,
            timezone_name="UTC",
            turns=[{"role": "user", "content": "My preferred degree is Business Administration."}],
        )
    result = MemoryHybridRetriever(benchmark_db).search(
        MemoryScopeContext(
            tenant_id=user.tenant_id,
            principal_user_id=user.id,
            workspace_id=workspace.id,
        ),
        "What is my preferred degree?",
        top_k=1,
        min_score=0.2,
    )
    assert result.candidates[0].external_source["external_session_id"] == "new_session"


def test_chunk_metadata_survives_ingestion(benchmark_db: Session) -> None:
    user = benchmark_db.get(User, "longmemeval-user")
    workspace = benchmark_db.get(Workspace, "longmemeval-workspace")
    assert user is not None and workspace is not None
    LongMemEvalAdapter(benchmark_db, _settings(), workspace, user).ingest_session(
        question_id="q-chunks",
        session_id="answer_chunks",
        session_date="2023-01-10",
        timezone_name="UTC",
        turns=[{"role": "assistant", "content": "x" * 7_001}],
    )
    docs = benchmark_db.scalars(select(MemorySearchDocument)).all()
    assert len(docs) == 3
    chunks = sorted(
        (row.metadata_json["external_source"]["chunk_index"], row.metadata_json["external_source"]["total_chunks"])
        for row in docs
    )
    assert chunks == [(0, 3), (1, 3), (2, 3)]


def test_duplicate_external_session_ids_are_disambiguated_by_session_index(
    benchmark_db: Session,
) -> None:
    user = benchmark_db.get(User, "longmemeval-user")
    workspace = benchmark_db.get(Workspace, "longmemeval-workspace")
    assert user is not None and workspace is not None
    adapter = LongMemEvalAdapter(benchmark_db, _settings(), workspace, user)
    for index in (0, 1):
        adapter.ingest_session(
            question_id="q-duplicate-session",
            session_id="same-external-id",
            session_index=index,
            session_date="2023-01-10",
            timezone_name="UTC",
            turns=[{"role": "user", "content": f"fact from occurrence {index}"}],
        )
    documents = benchmark_db.scalars(select(MemorySearchDocument)).all()
    assert len(documents) == 2
    assert {
        row.metadata_json["external_source"]["session_index"] for row in documents
    } == {0, 1}
