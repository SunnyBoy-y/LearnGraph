from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select, text

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-file-inv-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'file_inv.db').as_posix()}"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.migrations import apply_schema_migrations  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import (  # noqa: E402
    MemoryEvent,
    MemoryScopeContext,
    MemoryStream,
)
from app.domain.memory_event_types import MemoryEventType  # noqa: E402
from app.domain.models import (  # noqa: E402
    DocumentRevision,
    FileRecord,
    FileTextChunk,
    MemoryEvidence,
    new_id,
)
from app.services.memory_crypto import MemoryPayloadCipher  # noqa: E402
from app.services.memory_event_store import MemoryEventStore  # noqa: E402
from app.services.memory_file_invalidation import MemoryFileInvalidationService  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_schema_migrations(conn)
        # document_chunks_fts is an application-managed FTS projection created at
        # startup, not by create_all. Ensure it exists for seeding stale rows.
        conn.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5("
            "chunk_id UNINDEXED, workspace_id UNINDEXED, file_id UNINDEXED, "
            "content, tokenize='trigram')"
        )
    yield


def scope(*, tenant: str = "t1", user: str = "u1", workspace: str = "w1") -> MemoryScopeContext:
    return MemoryScopeContext(
        tenant_id=tenant,
        principal_user_id=user,
        workspace_id=workspace,
    )


def _service(db) -> MemoryFileInvalidationService:
    return MemoryFileInvalidationService(
        db, MemoryEventStore(db, MemoryPayloadCipher("secret"))
    )


def _add_file(db, *, file_id: str = "file-1") -> FileRecord:
    record = FileRecord(
        id=file_id,
        workspace_id="w1",
        original_name="notes.pdf",
        object_key=f"obj/{file_id}",
        size_bytes=100,
        sha256="f" * 64,
        parse_status="indexed",
    )
    db.add(record)
    db.flush()
    return record


def _add_revision(
    db,
    *,
    file_id: str,
    revision_no: int,
    revision_id: str,
) -> DocumentRevision:
    revision = DocumentRevision(
        id=revision_id,
        workspace_id="w1",
        file_id=file_id,
        revision_no=revision_no,
        source_sha256=f"{revision_no}" * 64,
        size_bytes=100,
        mime_detected="application/pdf",
        config_hash="cfg",
        created_by="u1",
        status="succeeded",
        lifecycle_status="active",
        index_status="ready",
    )
    db.add(revision)
    db.flush()
    return revision


def _add_chunk(db, *, chunk_id: str, file_id: str, revision_id: str, ordinal: int, content: str) -> FileTextChunk:
    chunk = FileTextChunk(
        id=chunk_id,
        workspace_id="w1",
        file_id=file_id,
        document_revision_id=revision_id,
        ordinal=ordinal,
        content=content,
        content_hash=f"hash-{content}",
    )
    db.add(chunk)
    db.flush()
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        db.execute(
            text(
                "INSERT INTO document_chunks_fts(chunk_id, workspace_id, file_id, content) "
                "VALUES (:chunk_id, :workspace_id, :file_id, :content)"
            ),
            {
                "chunk_id": chunk.id,
                "workspace_id": "w1",
                "file_id": file_id,
                "content": content,
            },
        )
    db.flush()
    return chunk


def _add_evidence(
    db,
    *,
    evidence_id: str,
    file_id: str,
    source_version_id: str | None,
) -> MemoryEvidence:
    derived = [] if source_version_id is None else [{"source_version_id": source_version_id}]
    evidence = MemoryEvidence(
        id=evidence_id,
        workspace_id="w1",
        source_kind="file",
        source_id=f"src-{evidence_id}",
        file_id=file_id,
        authorship="file_derived",
        derived_from=derived,
        content_hash=f"ev-{evidence_id}",
    )
    db.add(evidence)
    db.flush()
    return evidence


def _events(db) -> list[MemoryEvent]:
    return list(db.scalars(select(MemoryEvent).order_by(MemoryEvent.global_position)))


# ── Activation ────────────────────────────────────────────────────────────────

def test_activation_stales_old_revision_and_chunks_and_emits_event():
    with SessionLocal() as db:
        _add_file(db, file_id="file-1")
        _add_revision(db, file_id="file-1", revision_no=1, revision_id="rev-1")
        _add_chunk(db, chunk_id="chunk-1", file_id="file-1", revision_id="rev-1", ordinal=1, content="old text")
        _add_revision(db, file_id="file-1", revision_no=2, revision_id="rev-2")
        _add_chunk(db, chunk_id="chunk-2", file_id="file-1", revision_id="rev-2", ordinal=2, content="new text")
        db.commit()

        report = _service(db).activate_revision(
            scope(),
            file_id="file-1",
            revision_id="rev-2",
            actor_id="u1",
        )
        db.commit()

        assert report.stale_revision_ids == ("rev-1",)
        assert report.stale_chunk_count == 1
        old_rev = db.get(DocumentRevision, "rev-1")
        assert old_rev.lifecycle_status == "stale"
        old_chunk = db.get(FileTextChunk, "chunk-1")
        assert old_chunk.lifecycle_status == "stale"
        new_chunk = db.get(FileTextChunk, "chunk-2")
        assert new_chunk.lifecycle_status == "active"
        file = db.get(FileRecord, "file-1")
        assert file.active_revision_id == "rev-2"
        assert file.logical_version == 1
        events = _events(db)
        assert any(
            event.event_type == MemoryEventType.ARTIFACT_REVISION_ACTIVATED
            for event in events
        )
        # Stale chunk's FTS row must be gone; the new chunk's row remains.
        if db.bind is not None and db.bind.dialect.name == "sqlite":
            fts_count = db.execute(
                text("SELECT count(*) FROM document_chunks_fts WHERE chunk_id = :id"),
                {"id": "chunk-1"},
            ).scalar()
            assert fts_count == 0
            new_fts_count = db.execute(
                text("SELECT count(*) FROM document_chunks_fts WHERE chunk_id = :id"),
                {"id": "chunk-2"},
            ).scalar()
            assert new_fts_count >= 1


def test_activation_with_existing_active_revision_stales_both_and_deletes_fts():
    with SessionLocal() as db:
        _add_file(db, file_id="file-1")
        _add_revision(db, file_id="file-1", revision_no=1, revision_id="rev-1")
        _add_chunk(db, chunk_id="chunk-1", file_id="file-1", revision_id="rev-1", ordinal=1, content="old")
        file = db.get(FileRecord, "file-1")
        file.active_revision_id = "rev-1"
        _add_revision(db, file_id="file-1", revision_no=2, revision_id="rev-2")
        _add_chunk(db, chunk_id="chunk-2", file_id="file-1", revision_id="rev-2", ordinal=2, content="new")
        db.commit()

        report = _service(db).activate_revision(
            scope(),
            file_id="file-1",
            revision_id="rev-2",
            actor_id="u1",
        )
        db.commit()

        assert report.stale_revision_ids == ("rev-1",)
        assert report.stale_chunk_count == 1
        assert db.get(DocumentRevision, "rev-1").lifecycle_status == "stale"
        assert db.get(FileTextChunk, "chunk-1").lifecycle_status == "stale"
        assert db.get(FileRecord, "file-1").active_revision_id == "rev-2"
        # rev-2 activation is idempotent — no second stale event payload drift.
        events = _events(db)
        activated = [e for e in events if e.event_type == MemoryEventType.ARTIFACT_REVISION_ACTIVATED]
        assert len(activated) == 1
        if db.bind is not None and db.bind.dialect.name == "sqlite":
            fts_count = db.execute(
                text("SELECT count(*) FROM document_chunks_fts WHERE chunk_id = :id"),
                {"id": "chunk-1"},
            ).scalar()
            assert fts_count == 0


def test_default_retrieval_excludes_stale_chunks():
    """Active-only filter: stale chunks must not appear in the default read path."""
    with SessionLocal() as db:
        _add_file(db, file_id="file-1")
        _add_revision(db, file_id="file-1", revision_no=1, revision_id="rev-1")
        _add_chunk(db, chunk_id="chunk-1", file_id="file-1", revision_id="rev-1", ordinal=1, content="old")
        file = db.get(FileRecord, "file-1")
        file.active_revision_id = "rev-1"
        _add_revision(db, file_id="file-1", revision_no=2, revision_id="rev-2")
        _add_chunk(db, chunk_id="chunk-2", file_id="file-1", revision_id="rev-2", ordinal=2, content="new")
        db.commit()

        _service(db).activate_revision(
            scope(),
            file_id="file-1",
            revision_id="rev-2",
            actor_id="u1",
        )
        db.commit()

        active_ids = list(
            db.scalars(
                select(FileTextChunk.id)
                .where(
                    FileTextChunk.workspace_id == "w1",
                    FileTextChunk.file_id == "file-1",
                    FileTextChunk.lifecycle_status == "active",
                )
                .order_by(FileTextChunk.ordinal)
            )
        )
        assert active_ids == ["chunk-2"]
        # The FTS projection excludes stale rows too (production queries always
        # use MATCH, so a bare full-scan fan-out is not representative; assert
        # per-chunk presence instead).
        if db.bind is not None and db.bind.dialect.name == "sqlite":
            stale_fts = db.execute(
                text("SELECT count(*) FROM document_chunks_fts WHERE chunk_id = :id"),
                {"id": "chunk-1"},
            ).scalar()
            active_fts = db.execute(
                text("SELECT count(*) FROM document_chunks_fts WHERE chunk_id = :id"),
                {"id": "chunk-2"},
            ).scalar()
            assert stale_fts == 0
            assert active_fts >= 1


# ── Explicit invalidation ─────────────────────────────────────────────────────

def test_invalidate_revision_stales_and_emits_event():
    with SessionLocal() as db:
        _add_file(db, file_id="file-1")
        _add_revision(db, file_id="file-1", revision_no=1, revision_id="rev-1")
        _add_chunk(db, chunk_id="chunk-1", file_id="file-1", revision_id="rev-1", ordinal=1, content="old")
        db.commit()

        _service(db).invalidate_revision(
            scope(),
            file_id="file-1",
            revision_id="rev-1",
            actor_id="u1",
            reason="obsolete",
        )
        db.commit()

        assert db.get(DocumentRevision, "rev-1").lifecycle_status == "stale"
        assert db.get(FileTextChunk, "chunk-1").lifecycle_status == "stale"
        events = _events(db)
        assert any(
            event.event_type == MemoryEventType.ARTIFACT_REVISION_INVALIDATED
            for event in events
        )


# ── Evidence invalidation preserves independent sources ───────────────────────

def test_evidence_derived_from_stale_revision_is_invalidated_but_independent_is_kept():
    with SessionLocal() as db:
        _add_file(db, file_id="file-1")
        _add_revision(db, file_id="file-1", revision_no=1, revision_id="rev-1")
        _add_chunk(db, chunk_id="chunk-1", file_id="file-1", revision_id="rev-1", ordinal=1, content="old")
        file = db.get(FileRecord, "file-1")
        file.active_revision_id = "rev-1"
        _add_revision(db, file_id="file-1", revision_no=2, revision_id="rev-2")
        _add_chunk(db, chunk_id="chunk-2", file_id="file-1", revision_id="rev-2", ordinal=2, content="new")
        _add_evidence(db, evidence_id="ev-stale", file_id="file-1", source_version_id="rev-1")
        _add_evidence(db, evidence_id="ev-independent", file_id="file-1", source_version_id="rev-2")
        db.commit()

        _service(db).activate_revision(
            scope(),
            file_id="file-1",
            revision_id="rev-2",
            actor_id="u1",
        )
        db.commit()

        stale = db.get(MemoryEvidence, "ev-stale")
        assert stale.deleted_at is not None
        assert stale.eligibility_reason == "source_revision_stale"
        independent = db.get(MemoryEvidence, "ev-independent")
        assert independent.deleted_at is None


# ── Scope isolation ───────────────────────────────────────────────────────────

def test_activation_is_scoped_to_workspace():
    with SessionLocal() as db:
        _add_file(db, file_id="file-1")
        _add_revision(db, file_id="file-1", revision_no=1, revision_id="rev-1")
        db.commit()
        with pytest.raises(LookupError):
            _service(db).activate_revision(
                scope(workspace="w-other"),
                file_id="file-1",
                revision_id="rev-1",
                actor_id="u1",
            )


def test_explicit_version_query_can_read_stale_revision():
    with SessionLocal() as db:
        _add_file(db, file_id="file-1")
        _add_revision(db, file_id="file-1", revision_no=1, revision_id="rev-1")
        _add_chunk(db, chunk_id="chunk-1", file_id="file-1", revision_id="rev-1", ordinal=1, content="old")
        file = db.get(FileRecord, "file-1")
        file.active_revision_id = "rev-1"
        _add_revision(db, file_id="file-1", revision_no=2, revision_id="rev-2")
        _add_chunk(db, chunk_id="chunk-2", file_id="file-1", revision_id="rev-2", ordinal=2, content="new")
        db.commit()
        _service(db).activate_revision(scope(), file_id="file-1", revision_id="rev-2", actor_id="u1")
        db.commit()
        active_ids = list(db.scalars(
            select(FileTextChunk.id).where(
                FileTextChunk.workspace_id == "w1", FileTextChunk.file_id == "file-1",
                FileTextChunk.lifecycle_status == "active",
            ).order_by(FileTextChunk.ordinal)
        ))
        assert active_ids == ["chunk-2"]
        stale_rev = db.get(DocumentRevision, "rev-1")
        assert stale_rev is not None and stale_rev.lifecycle_status == "stale"
        stale_chunk = db.get(FileTextChunk, "chunk-1")
        assert stale_chunk is not None and stale_chunk.lifecycle_status == "stale"
        assert stale_chunk.content == "old"
