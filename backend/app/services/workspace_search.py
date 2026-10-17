"""Workspace-local search. Only display text is searched; provider traces stay private."""
from __future__ import annotations

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.domain.models import ChatSession, FileRecord, FileTextChunk, Message
from app.domain.schemas.workspace_search import SearchKind, WorkspaceSearchHit, WorkspaceSearchPage
from app.services.authorization import AuthorizationService


def excerpt(text: str, query: str, size: int = 180) -> str:
    text = " ".join(text.split())
    position = text.lower().find(query.lower()) if query else 0
    start = max(0, position - 50)
    return ("…" if start else "") + text[start:start + size] + ("…" if len(text) > start + size else "")


def search_workspace(
    db: Session, context: WorkspaceContext, query: str, kind: SearchKind,
    *, offset: int = 0, limit: int = 20,
) -> WorkspaceSearchPage:
    query = query.strip()
    workspace_id = context.workspace_id
    auth = AuthorizationService(db, context.principal)
    # Apply collection ACLs before LIMIT, so inaccessible hits neither leak nor
    # hide a matching readable item further down the result set.
    def readable(model, resource_type):
        ids = list(db.scalars(select(model.id).where(model.workspace_id == workspace_id)))
        return auth.filter_accessible_ids(context.workspace, resource_type, ids, "read")

    escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    def matches(column):
        return func.lower(column).like(f"%{escaped}%", escape="\\")

    def ordered(statement, model):
        return statement.order_by(
            case((func.lower(model.id) == query.lower(), 0), else_=1),
            model.updated_at.desc(), model.id,
        ).limit(offset + limit + 1)

    hits: list[WorkspaceSearchHit] = []
    session_ids = readable(ChatSession, "session") if kind in {"all", "session", "message"} else set()
    if kind in {"all", "session"}:
        statement = select(ChatSession).where(ChatSession.id.in_(session_ids))
        if query:
            statement = statement.where(or_(matches(ChatSession.id), matches(ChatSession.title)))
        for session in db.scalars(ordered(statement, ChatSession)):
            by_id = bool(query and query.lower() in session.id.lower())
            hits.append(WorkspaceSearchHit(
                kind="session", id=session.id, title=session.title or "新会话",
                snippet=f"会话 ID：{session.id}" if by_id else "",
                session_id=session.id, updated_at=session.updated_at,
                match_field="id" if by_id else "title",
            ))
    if query and kind in {"all", "message"}:
        statement = select(Message, ChatSession.title).join(
            ChatSession, ChatSession.id == Message.session_id,
        ).where(
            Message.workspace_id == workspace_id, Message.session_id.in_(session_ids),
            Message.role.in_(["user", "assistant"]),
            or_(matches(Message.id), matches(Message.content)),
        )
        for message, title in db.execute(ordered(statement, Message)):
            by_id = query.lower() in message.id.lower()
            hits.append(WorkspaceSearchHit(
                kind="message", id=message.id, title=title or "新会话",
                snippet=(f"消息 ID：{message.id} · " if by_id else "") + excerpt(message.content, query),
                session_id=message.session_id, updated_at=message.updated_at,
                match_field="id" if by_id else "content",
            ))
    if query and kind in {"all", "file"}:
        file_ids = readable(FileRecord, "file")
        # Only the current parse revision contributes text, never a superseded
        # revision or a deleted file/chunk.
        chunk = select(FileTextChunk.content).where(
            FileTextChunk.workspace_id == workspace_id,
            FileTextChunk.file_id == FileRecord.id,
            FileTextChunk.lifecycle_status == "active",
            or_(FileTextChunk.document_revision_id == FileRecord.active_revision_id,
                FileTextChunk.document_revision_id.is_(None) & FileRecord.active_revision_id.is_(None)),
            matches(FileTextChunk.content),
        ).order_by(FileTextChunk.ordinal).limit(1).correlate(FileRecord).scalar_subquery()
        statement = select(FileRecord, chunk.label("matching_text")).where(
            FileRecord.id.in_(file_ids), FileRecord.lifecycle_status == "active",
            or_(matches(FileRecord.id), matches(FileRecord.original_name), chunk.is_not(None)),
        )
        for file, text in db.execute(ordered(statement, FileRecord)):
            by_id = query.lower() in file.id.lower()
            by_title = query.lower() in file.original_name.lower()
            hits.append(WorkspaceSearchHit(
                kind="file", id=file.id, title=file.original_name,
                snippet=f"文件 ID：{file.id}" if by_id else excerpt(text or "", query),
                updated_at=file.updated_at,
                match_field="id" if by_id else "title" if by_title else "content",
            ))
    hits.sort(key=lambda hit: (
        0 if hit.id.lower() == query.lower() else 1,
        -hit.updated_at.timestamp(), hit.kind, hit.id,
    ))
    items = hits[offset:offset + limit]
    return WorkspaceSearchPage(items=items, has_more=len(hits) > offset + limit, next_offset=offset + len(items))
