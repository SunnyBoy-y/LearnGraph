"""Unified session-file collection shared by the Agent tool and the sidebar API.

背景（2026-08-13）：``list_session_files`` 只查 ``FileReference`` 和
``ImageGenerationTask`` 两张表，而 ``download_external_image`` 下载的文件只
落在 ``session_workspace_entries``（不建 FileReference），导致同一批图
``sandbox_list_files`` 能看到、``list_session_files`` 看不到。本模块把三个
来源合并成一份"会话关联文件"视图，Agent 工具与 REST 接口共用，保证两个
"文件区"视角一致。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    FileRecord,
    FileReference,
    ImageGenerationTask,
    Message,
    SessionWorkspaceEntry,
)
from app.services.chat_attachment_policy import is_image_attachment


def _entry_origin(*, relation: str | None, source: str | None) -> str:
    """Map a file to a stable origin label.

    - user_attachment / generated_image mirror the legacy Agent tool values.
    - external_download covers ``download_external_image`` results.
    - agent_workspace_file covers sandbox agent writes (work tree).
    - session_workspace is the fallback for any other workspace entry.
    """

    if relation == "generated_image":
        return "generated_image"
    if relation:
        return "user_attachment"
    if source == "external_download":
        return "external_download"
    if source and source != "upload":
        return "agent_workspace_file"
    return "session_workspace"


def collect_session_files(
    db: Session,
    *,
    workspace_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Merge every durable file tied to a chat session into one ordered list.

    Sources:
    1. ``FileReference`` rows targeting messages of this session (user @
       mentions, chat context / selection attachments).
    2. ``ImageGenerationTask`` rows completed in this session (generated images).
    3. ``SessionWorkspaceEntry`` rows of this session (external downloads,
       agent writes) — previously invisible to ``list_session_files`` because
       they do not create a ``FileReference``.

    Entries are deduplicated by ``file_id`` (workspace entries without a
    ``FileRecord`` are keyed by path). Ordering is by creation time ascending,
    matching the Agent tool's historical ordering.
    """

    entries: dict[str, dict[str, Any]] = {}

    # 1) Message-linked files (user attachments / chat context / selections).
    attachment_rows = db.execute(
        select(FileRecord, Message, FileReference)
        .join(FileReference, FileReference.file_id == FileRecord.id)
        .join(Message, FileReference.target_id == Message.id)
        .where(
            FileReference.workspace_id == workspace_id,
            FileReference.target_type == "message",
            Message.session_id == session_id,
            FileRecord.workspace_id == workspace_id,
        )
        .order_by(Message.created_at)
    ).all()
    for file, message, reference in attachment_rows:
        key = f"file:{file.id}"
        entries.setdefault(
            key,
            {
                "file_id": file.id,
                "filename": file.original_name,
                "mime_type": file.mime_type,
                "size_bytes": file.size_bytes,
                "origin": _entry_origin(
                    relation=reference.relation, source=file.source
                ),
                "relation": reference.relation,
                "path": None,
                "source": file.source,
                "message_id": message.id,
                "is_image": is_image_attachment(file),
                "storage_status": file.storage_status,
                "prompt_summary": None,
                "created_at": (
                    file.created_at.isoformat() if file.created_at else None
                ),
            },
        )

    # 2) ImageGenerationTask images (generated in this session).
    generated_rows = db.execute(
        select(FileRecord, ImageGenerationTask)
        .join(ImageGenerationTask, ImageGenerationTask.file_id == FileRecord.id)
        .where(
            ImageGenerationTask.workspace_id == workspace_id,
            ImageGenerationTask.session_id == session_id,
            ImageGenerationTask.status == "completed",
        )
        .order_by(ImageGenerationTask.created_at)
    ).all()
    for file, task in generated_rows:
        entries.setdefault(
            f"file:{file.id}",
            {
                "file_id": file.id,
                "filename": file.original_name,
                "mime_type": file.mime_type,
                "size_bytes": file.size_bytes,
                "origin": "generated_image",
                "relation": "generated_image",
                "path": None,
                "source": file.source,
                "message_id": task.message_id,
                "is_image": True,
                "storage_status": file.storage_status,
                "prompt_summary": task.prompt_summary or None,
                "created_at": (
                    task.created_at.isoformat() if task.created_at else None
                ),
            },
        )

    # 3) Session workspace entries (external downloads, agent writes).
    workspace_rows = db.execute(
        select(SessionWorkspaceEntry).where(
            SessionWorkspaceEntry.workspace_id == workspace_id,
            SessionWorkspaceEntry.chat_session_id == session_id,
        )
    ).scalars().all()
    for entry in workspace_rows:
        file_record = None
        if entry.file_id:
            file_record = db.get(FileRecord, entry.file_id)
        if file_record is not None:
            key = f"file:{file_record.id}"
            existing = entries.get(key)
            if existing is not None:
                # The workspace entry knows the durable path; enrich the
                # already-listed file instead of duplicating it.
                existing["path"] = entry.path
                existing["source"] = entry.source or existing["source"]
                if existing.get("origin") == "user_attachment" and entry.source:
                    existing["origin"] = _entry_origin(
                        relation=existing.get("relation"), source=entry.source
                    )
                continue
            entries[key] = {
                "file_id": file_record.id,
                "filename": file_record.original_name,
                "mime_type": file_record.mime_type,
                "size_bytes": file_record.size_bytes,
                "origin": _entry_origin(
                    relation=None, source=entry.source or file_record.source
                ),
                "relation": None,
                "path": entry.path,
                "source": entry.source,
                "message_id": None,
                "is_image": is_image_attachment(file_record),
                "storage_status": file_record.storage_status,
                "prompt_summary": None,
                "created_at": (
                    entry.created_at.isoformat() if entry.created_at else None
                ),
            }
            continue
        # Workspace-only entry without a downloadable FileRecord.
        key = f"path:{entry.path}"
        if key in entries:
            continue
        entries[key] = {
            "file_id": None,
            "filename": entry.path.rsplit("/", 1)[-1] or entry.path,
            "mime_type": entry.mime_type,
            "size_bytes": entry.size_bytes,
            "origin": _entry_origin(relation=None, source=entry.source),
            "relation": None,
            "path": entry.path,
            "source": entry.source,
            "message_id": None,
            "is_image": bool(
                entry.mime_type.casefold().startswith("image/")
            ),
            "storage_status": "stored",
            "prompt_summary": None,
            "created_at": (
                entry.created_at.isoformat() if entry.created_at else None
            ),
        }

    return sorted(entries.values(), key=lambda item: item["created_at"] or "")
