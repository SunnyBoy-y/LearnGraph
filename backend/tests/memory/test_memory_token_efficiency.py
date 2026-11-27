"""Regression tests for memory token-efficiency and wasted-work fixes."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import Base
from app.core.scheduler import _stale_profile_owner_ids, _sweep_workspace_ids
from app.domain import models as m
from app.domain.memory_event_models import (
    MemoryProjectionOutbox,
    MemoryScopeContext,
    MemorySearchDocument,
)
from app.domain.schemas.context_builds import ContextBuildRequest
from app.providers.local.memory import LocalWorkspaceMemoryProvider
from app.providers.remote.openai import ProviderHTTPError
from app.services import memory_enhancement as enhancement
from app.services.context_builder import ContextBuilder
from app.services.memory import MemoryService
from app.services.memory_event_ingestor import MemoryEventIngestor
from app.services.memory_profile import (
    MemoryProfileService,
    _parse_refresh_failure_meta,
    _profile_failure_cooldown,
)
from app.services.memory_retrieval import MemoryHybridRetriever
from app.services.memory_router import MemoryRouter

TENANT = "t-eff"
WORKSPACE = "ws-eff"
ACTOR = "u-eff"


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
                name="efficiency",
            )
        )
        session.add(
            m.WorkspaceSetting(
                workspace_id=WORKSPACE,
                key=enhancement.MEMORY_POLICY_KEY,
                value={"workspace_enabled": True},
            )
        )
        session.add(
            m.ChatSession(
                id="s1",
                workspace_id=WORKSPACE,
                title="session 1",
                memory_enabled=True,
                memory_learning_enabled=True,
                updated_at=m.utc_now() - timedelta(minutes=30),
            )
        )
        session.add(
            m.ChatSession(
                id="s2",
                workspace_id=WORKSPACE,
                title="session 2",
                memory_enabled=True,
                memory_learning_enabled=True,
                updated_at=m.utc_now() - timedelta(minutes=30),
            )
        )
        session.commit()
        yield session
    engine.dispose()


def _add_user_message(db: Session, session_id: str, message_id: str, content: str, *, minutes_ago: int = 30) -> m.Message:
    msg = m.Message(
        id=message_id,
        workspace_id=WORKSPACE,
        session_id=session_id,
        role="user",
        status="completed",
        content=content,
        created_at=m.utc_now() - timedelta(minutes=minutes_ago),
    )
    db.add(msg)
    db.commit()
    return msg


def test_empty_context_builder_returns_zero_tokens_and_empty_prompt(db: Session) -> None:
    scope = MemoryScopeContext(
        tenant_id=TENANT,
        principal_user_id=ACTOR,
        workspace_id=WORKSPACE,
        conversation_id="s1",
    )
    built = ContextBuilder(
        db, MemoryRouter(MemoryHybridRetriever(db), db=db)
    ).build(scope, ContextBuildRequest(query="你好", token_budget=2000))
    assert built.prompt_block == ""
    assert built.view.total_tokens == 0
    assert built.view.provider_messages == []
    assert built.view.manifest_status == "excluded"


@pytest.mark.parametrize(
    "utterance",
    [
        "你好",
        "你是谁",
        "用svg画出来",
        "请阅读并结合附件内容回答。",
    ],
)
def test_low_signal_extraction_skips_llm_and_advances_cursor(
    db: Session, monkeypatch: pytest.MonkeyPatch, utterance: str
) -> None:
    workspace = db.get(m.Workspace, WORKSPACE)
    msg = _add_user_message(db, "s1", f"m-{hashlib.md5(utterance.encode()).hexdigest()[:8]}", utterance)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Low-signal message must not contact extraction LLM")

    monkeypatch.setattr(enhancement, "model_provider_for_workspace", forbidden)
    result = enhancement.extract_session_memories(
        db, workspace, "s1", get_settings(), actor_id=ACTOR, force=False
    )
    assert result["status"] == "ok"
    assert result["completion_reason"] == "skipped_low_signal"
    assert result["drafts_created"] == 0
    state = db.scalar(
        select(m.MemoryExtractionState).where(
            m.MemoryExtractionState.workspace_id == WORKSPACE,
            m.MemoryExtractionState.session_id == "s1",
        )
    )
    assert state is not None
    assert state.last_message_id == msg.id
    assert state.last_status == "skipped_low_signal"


def test_extraction_prompt_prefix_is_cache_stable_and_caps_existing_records(db: Session) -> None:
    session = db.get(m.ChatSession, "s1")
    for idx in range(25):
        db.add(
            m.MemoryRecord(
                id=f"mem-{idx:02d}",
                workspace_id=WORKSPACE,
                namespace="workspace",
                record_kind="semantic_memory",
                title=f"数据库索引知识 {idx}" if idx == 20 else f"普通记录 {idx}",
                content_hash=f"hash-{idx}",
                relative_path=f"topics/mem-{idx:02d}.md",
                state="active",
                updated_at=m.utc_now() - timedelta(minutes=idx),
            )
        )
    db.commit()

    records, lines = enhancement._existing_memory_context(
        db, WORKSPACE, "s1", query_text="我最近在复习数据库索引"
    )
    assert len(records) == 25
    prompt_lines = [line for line in lines.splitlines() if line.strip()]
    assert len(prompt_lines) == enhancement._EXISTING_MEMORY_PROMPT_CAP
    # Relevant record (#20) is ranked into the prompt window despite older timestamp.
    assert any("mem-20" in line for line in prompt_lines)

    p1 = enhancement._extraction_prompt(
        "<evidence id='e1'>内容一</evidence>",
        lines,
        session,
        observed_date="2026-11-26",
    )
    p2 = enhancement._extraction_prompt(
        "<evidence id='e2'>内容二</evidence>",
        lines,
        session,
        observed_date="2026-11-27",
    )
    # Static rules prefix before "会话上下文：" must be byte-identical for prefix caching.
    marker = "会话上下文："
    assert p1.index(marker) == p2.index(marker)
    assert p1[: p1.index(marker)] == p2[: p2.index(marker)]


def test_cross_session_pending_dedup_and_retention_expiry(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = db.get(m.Workspace, WORKSPACE)
    # Existing PENDING draft in session s1.
    db.add(
        m.MemoryDraft(
            id="draft-s1",
            workspace_id=WORKSPACE,
            session_id="s1",
            operation="CREATE",
            memory_type="learning_preference",
            title="偏好以流式 SVG 方式即时绘制图表",
            content="用户偏好以流式 SVG 方式即时绘制图表",
            status="PENDING",
            confidence=0.6,
            created_by="memory_extraction",
            created_at=m.utc_now() - timedelta(days=2),
        )
    )
    # Expired PENDING draft (>30 days old).
    db.add(
        m.MemoryDraft(
            id="draft-old",
            workspace_id=WORKSPACE,
            session_id="s1",
            operation="CREATE",
            memory_type="event_summary",
            title="三十天前的未确认草稿",
            content="用户三十天前看过一次视频",
            status="PENDING",
            confidence=0.5,
            created_by="memory_extraction",
            created_at=m.utc_now() - timedelta(days=35),
        )
    )
    db.commit()

    # Session s2 proposes a near-duplicate of draft-s1: must be skipped.
    content_s2 = "我更偏好以流式 SVG 方式绘制图表来学习"
    _add_user_message(db, "s2", "m-s2", content_s2)
    ev = enhancement._user_evidence_for_messages(
        db, WORKSPACE, [db.get(m.Message, "m-s2")]
    )["m-s2"]
    model = SimpleNamespace(
        available=True,
        provider_id="fake",
        model_id="fake",
        remote_capability=False,
        last_usage={"input_tokens": 100, "output_tokens": 50},
        max_output_tokens=2048,
        generate_json=lambda *_: {
            "has_more": False,
            "memories": [
                {
                    "operation": "CREATE",
                    "memory_type": "learning_preference",
                    "title": "偏好流式 SVG 方式绘制图表",
                    "content": "用户偏好以流式 SVG 方式绘制图表",
                    "confidence": 0.6,
                    "evidence_ids": [ev.id],
                    "source_quote": content_s2,
                }
            ],
        },
    )
    monkeypatch.setattr(enhancement, "model_provider_for_workspace", lambda *a, **kw: model)
    monkeypatch.setattr(
        enhancement,
        "memory_provider_for_workspace",
        lambda *a, **kw: LocalWorkspaceMemoryProvider(tmp_path, WORKSPACE),
    )
    monkeypatch.setattr(enhancement, "BillingService", MagicMock())

    result = enhancement.extract_session_memories(
        db, workspace, "s2", get_settings(), actor_id=ACTOR, force=True
    )
    assert result["status"] == "ok"
    assert result["drafts_created"] == 0
    assert result["skipped"] == 1

    # Retention closes the >30d draft while leaving the 2d draft PENDING.
    service = MemoryService(
        db, workspace, ACTOR, LocalWorkspaceMemoryProvider(tmp_path, WORKSPACE), tmp_path
    )
    purged = service.purge_expired()
    assert purged["expired_drafts_closed"] == 1
    assert db.get(m.MemoryDraft, "draft-old").status == "REJECTED"
    assert db.get(m.MemoryDraft, "draft-old").rejection_reason == "expired_unreviewed"
    assert db.get(m.MemoryDraft, "draft-s1").status == "PENDING"


def test_embedding_failure_releases_reservation_and_opens_circuit_breaker(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    enhancement.reset_embedding_circuit_breaker()
    db.add(
        m.ProviderConfig(
            id="prov-embed",
            workspace_id=WORKSPACE,
            provider_type="ollama_embedding",
            display_name="Ollama-embedding",
            base_url="http://127.0.0.1:11434/v1",
            enabled=True,
            remote_capability=True,
        )
    )
    db.add(
        m.WorkspaceSetting(
            workspace_id=WORKSPACE,
            key=enhancement.MEMORY_ENHANCEMENT_KEY,
            value={
                "extraction": {"enabled": True, "provider_id": "", "model_id": "", "follow_conversation": False, "auto_commit": True},
                "embedding": {"enabled": True, "provider_id": "prov-embed", "model_id": "qwen3-embedding:0.6b", "semantic_weight": 0.8},
                "summarization": {"enabled": False, "provider_id": "", "model_id": "", "follow_conversation": False},
            },
        )
    )
    doc = MemorySearchDocument(
        id="doc-1",
        target_type="memory",
        target_id="mem-1",
        target_version=1,
        memory_layer="L3",
        memory_type="semantic_memory",
        subject="Rust",
        content="Rust ownership",
        tenant_id=TENANT,
        subject_user_id=ACTOR,
        workspace_id=WORKSPACE,
        status="active",
        sensitivity="normal",
        importance=0.8,
        confidence=0.9,
        source_event_id="evt-1",
        content_hash="h-1",
    )
    db.add(doc)
    db.commit()

    embed_calls = {"count": 0}

    class FailingEmbedProvider:
        provider_id = "prov-embed"
        model_id = "qwen3-embedding:0.6b"
        last_usage: dict = {}

        def embed(self, _texts):
            embed_calls["count"] += 1
            raise ProviderHTTPError("Embedding request failed: [Errno 111] Connection refused")

    monkeypatch.setattr(
        enhancement,
        "embedding_provider_for_workspace",
        lambda *a, **kw: FailingEmbedProvider(),
    )

    first = enhancement.semantic_boosts_for_documents(
        db, WORKSPACE, get_settings(), "Rust ownership", [doc]
    )
    assert first == {}
    assert embed_calls["count"] == 1
    usage_rows = list(
        db.scalars(
            select(m.UsageEvent).where(m.UsageEvent.feature == "memory_embedding")
        ).all()
    )
    assert len(usage_rows) == 1
    assert usage_rows[0].record_stage == "released"
    assert usage_rows[0].cost_status == "embedding_failed"

    # Second immediate call must short-circuit without calling provider or creating another reservation.
    second = enhancement.semantic_boosts_for_documents(
        db, WORKSPACE, get_settings(), "Rust ownership", [doc]
    )
    assert second == {}
    assert embed_calls["count"] == 1
    assert (
        len(
            list(
                db.scalars(
                    select(m.UsageEvent).where(m.UsageEvent.feature == "memory_embedding")
                ).all()
            )
        )
        == 1
    )
    enhancement.reset_embedding_circuit_breaker()


def test_scheduler_skips_ready_profile_with_stale_history_and_skips_eval_workspaces(
    db: Session,
) -> None:
    # Eval workspace should be excluded from background sweeps.
    db.add(
        m.Workspace(
            id="ws-eval",
            tenant_id=TENANT,
            owner_user_id="u-eval",
            name="eval sandbox",
        )
    )
    db.add(
        m.WorkspaceSetting(
            workspace_id="ws-eval",
            key="memory.eval_context",
            value={"workspace_id": "ws-eval"},
        )
    )
    # Owner u1 has v1=stale, v2=ready -> must NOT be selected.
    db.add(
        m.MemoryProfileSnapshot(
            workspace_id=WORKSPACE,
            owner_subject_id="u1",
            version=1,
            status="stale",
            source_fingerprint="fp1",
            stale_reason="superseded_by_new_profile",
        )
    )
    db.add(
        m.MemoryProfileSnapshot(
            workspace_id=WORKSPACE,
            owner_subject_id="u1",
            version=2,
            status="ready",
            source_fingerprint="fp2",
        )
    )
    # Owner u2 has latest v1=stale -> MUST be selected.
    db.add(
        m.MemoryProfileSnapshot(
            workspace_id=WORKSPACE,
            owner_subject_id="u2",
            version=1,
            status="stale",
            source_fingerprint="fp3",
        )
    )
    # Owner u3 has latest v2=failed -> must NOT be selected by stale scan.
    db.add(
        m.MemoryProfileSnapshot(
            workspace_id=WORKSPACE,
            owner_subject_id="u3",
            version=1,
            status="stale",
            source_fingerprint="fp4",
        )
    )
    db.add(
        m.MemoryProfileSnapshot(
            workspace_id=WORKSPACE,
            owner_subject_id="u3",
            version=2,
            status="failed",
            source_fingerprint="fp4",
            stale_reason="refresh_failed|1|permanent|HTTP 404|2026-11-26T00:00:00+00:00",
        )
    )
    db.commit()

    sweep_ids = _sweep_workspace_ids(db)
    assert WORKSPACE in sweep_ids
    assert "ws-eval" not in sweep_ids

    stale_owners = _stale_profile_owner_ids(db, WORKSPACE, limit=10)
    assert stale_owners == ["u2"]


def test_profile_failure_persistent_backoff_distinguishes_permanent_and_transient(
    db: Session,
) -> None:
    workspace = db.get(m.Workspace, WORKSPACE)
    service = MemoryProfileService(db, workspace, ACTOR, get_settings())

    # 1st transient failure -> 5 min backoff; 2nd -> 10 min backoff.
    service._note_refresh_failure(RuntimeError("temporary timeout"))
    snap = db.scalar(select(m.MemoryProfileSnapshot))
    attempts, permanent, failed_at = _parse_refresh_failure_meta(snap.stale_reason)
    assert attempts == 1 and not permanent and failed_at is not None
    assert _profile_failure_cooldown(attempts, permanent) == timedelta(seconds=300)

    service._note_refresh_failure(RuntimeError("temporary timeout"))
    db.refresh(snap)
    attempts, permanent, _ = _parse_refresh_failure_meta(snap.stale_reason)
    assert attempts == 2 and not permanent
    assert _profile_failure_cooldown(attempts, permanent) == timedelta(seconds=600)

    # Permanent HTTP 404 -> 24h backoff.
    service._note_refresh_failure(
        ProviderHTTPError("Provider returned HTTP 404", status_code=404)
    )
    db.refresh(snap)
    attempts, permanent, _ = _parse_refresh_failure_meta(snap.stale_reason)
    assert attempts == 3 and permanent
    assert _profile_failure_cooldown(attempts, permanent) == timedelta(seconds=86_400)


def test_outbox_producers_do_not_enqueue_unimplemented_profile_or_embedding() -> None:
    assert set(MemoryEventIngestor._outbox_kinds("memory.created")) == {
        "markdown",
        "mem0",
        "index",
    }
    assert set(MemoryEventIngestor._outbox_kinds("artifact.updated")) == {"index"}
