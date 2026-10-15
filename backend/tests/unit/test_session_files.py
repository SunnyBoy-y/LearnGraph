"""Regression tests for unified session-file collection.

背景（2026-08-13）：``list_session_files`` 只查 FileReference +
ImageGenerationTask，download_external_image 下载的文件只落在
session_workspace_entries（不建 FileReference），导致 sandbox_list_files 能
看到、list_session_files 看不到。修复后 collect_session_files 合并三个来源，
Agent 工具与 REST 接口共用。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

from app.domain import extension_models, memory_event_models, models  # noqa: F401
from app.domain.models import (
    ChatSession,
    ContentBlob,
    FileRecord,
    FileReference,
    ImageGenerationTask,
    Message,
    MessageVersion,
    SessionWorkspaceEntry,
    new_id,
)
from app.services.session_files import collect_session_files


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 3},
    )

    @sa_event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=300")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    from app.core.database import Base

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(engine):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        yield session


def _blob(db, sha: str, name: str) -> ContentBlob:
    blob = ContentBlob(
        id=new_id(),
        workspace_id="ws-1",
        sha256=sha,
        object_key=f"blobs/ws-1/{sha}",
        size_bytes=len(sha),
        mime_type="image/png",
        ref_count=1,
    )
    db.add(blob)
    db.flush()
    return blob


def _file(db, name: str, sha: str, source: str = "upload") -> FileRecord:
    blob = _blob(db, sha, name)
    file = FileRecord(
        id=new_id(),
        workspace_id="ws-1",
        original_name=name,
        object_key=f"obj/{sha}",
        mime_type="image/png" if name.endswith(".png") else "text/markdown",
        size_bytes=len(sha),
        sha256=sha,
        source=source,
    )
    db.add(file)
    db.flush()
    return file


def _session(db) -> ChatSession:
    session = ChatSession(
        workspace_id="ws-1", title="测试", session_kind="main"
    )
    db.add(session)
    db.flush()
    return session


def _message(db, session: ChatSession) -> tuple[Message, MessageVersion]:
    message = Message(
        workspace_id="ws-1",
        session_id=session.id,
        role="user",
        content="hello",
    )
    db.add(message)
    db.flush()
    version = MessageVersion(
        workspace_id="ws-1",
        message_id=message.id,
        version=1,
        status="completed",
    )
    db.add(version)
    db.flush()
    return message, version


def test_collect_merges_all_three_sources(db) -> None:
    session = _session(db)
    message, version = _message(db, session)

    # 1) message-linked attachment (FileReference)
    attached = _file(db, "user-note.md", "a" * 64)
    db.add(
        FileReference(
            workspace_id="ws-1",
            file_id=attached.id,
            target_type="message",
            target_id=message.id,
            relation="chat_context",
        )
    )
    # 2) generated image (ImageGenerationTask)
    generated = _file(db, "gen.png", "b" * 64)
    db.add(
        ImageGenerationTask(
            id=new_id(),
            workspace_id="ws-1",
            session_id=session.id,
            file_id=generated.id,
            status="completed",
            provider_id="p",
            model_id="m",
            prompt_summary="一张梗图",
            prompt_hash="hash",
            message_id=message.id,
            message_version_id=version.id,
            source_message_id=message.id,
        )
    )
    # 3a) external download with a FileRecord (publish_file=True)
    download = _file(db, "1739525776376.png", "c" * 64, source="agent")
    db.add(
        SessionWorkspaceEntry(
            id=new_id(),
            workspace_id="ws-1",
            owner_user_id="user-1",
            chat_session_id=session.id,
            path="inputs/images/1739525776376.png",
            role="input",
            blob_sha256="c" * 64,
            file_id=download.id,
            size_bytes=8,
            mime_type="image/png",
            source="external_download",
        )
    )
    # 3b) workspace-only entry without FileRecord
    db.add(
        SessionWorkspaceEntry(
            id=new_id(),
            workspace_id="ws-1",
            owner_user_id="user-1",
            chat_session_id=session.id,
            path="work/scratch.txt",
            role="work",
            blob_sha256="d" * 64,
            file_id=None,
            size_bytes=4,
            mime_type="text/plain",
            source="agent",
        )
    )
    db.commit()

    files = collect_session_files(db, workspace_id="ws-1", session_id=session.id)
    by_id = {item["file_id"]: item for item in files}
    by_path = {item["path"]: item for item in files}

    assert by_id[attached.id]["origin"] == "user_attachment"
    assert by_id[generated.id]["origin"] == "generated_image"
    assert by_id[generated.id]["prompt_summary"] == "一张梗图"
    assert by_id[download.id]["origin"] == "external_download"
    assert by_id[download.id]["path"] == "inputs/images/1739525776376.png"
    # workspace-only entry keyed by path, no file_id
    assert by_path["work/scratch.txt"]["file_id"] is None
    assert by_path["work/scratch.txt"]["filename"] == "scratch.txt"
    assert len(files) == 4


def test_collect_dedups_same_file_across_sources(db) -> None:
    session = _session(db)
    message, _version = _message(db, session)

    shared = _file(db, "shared.png", "e" * 64)
    # Same file referenced by a message AND present in the workspace entries.
    db.add(
        FileReference(
            workspace_id="ws-1",
            file_id=shared.id,
            target_type="message",
            target_id=message.id,
            relation="chat_context",
        )
    )
    db.add(
        SessionWorkspaceEntry(
            id=new_id(),
            workspace_id="ws-1",
            owner_user_id="user-1",
            chat_session_id=session.id,
            path="inputs/images/shared.png",
            role="input",
            blob_sha256="e" * 64,
            file_id=shared.id,
            size_bytes=8,
            mime_type="image/png",
            source="external_download",
        )
    )
    db.commit()

    files = collect_session_files(db, workspace_id="ws-1", session_id=session.id)
    assert len(files) == 1
    # The workspace entry enriched the path while keeping the attachment origin.
    assert files[0]["file_id"] == shared.id
    assert files[0]["path"] == "inputs/images/shared.png"
