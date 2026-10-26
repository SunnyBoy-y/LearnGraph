"""PostgreSQL search adapters; SQLite FTS5 paths remain unchanged."""
from __future__ import annotations

from sqlalchemy import case, false, func, literal, or_, select, text, union


def lexical_expression(columns, terms):
    terms = list(dict.fromkeys(term.casefold() for term in terms if term))[:20]
    matches = [or_(*(func.lower(column).contains(term, autoescape=True) for column in columns)) for term in terms]
    score = sum((case((condition, 1.0), else_=0.0) for condition in matches), literal(0.0))
    return or_(*matches) if matches else false(), score


def install_search(engine) -> None:
    # pg_trgm ships with the official PostgreSQL image. No external search
    # service, tokenizer download or tenant-policy change is introduced.
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        for table, column in [("messages", "content"), ("messages", "role"), ("chat_sessions", "title"), ("chat_sessions", "goal_id"), ("chat_sessions", "graph_id"), ("file_text_chunks", "content"), ("memory_search_documents", "content"), ("memory_search_documents", "subject"), ("memory_search_documents", "keywords_text"), ("memory_search_documents", "entity_aliases_text")]:
            connection.execute(text(f'CREATE INDEX IF NOT EXISTS "lg_pg_search_{table}_{column}" ON "{table}" USING gin (lower("{column}") gin_trgm_ops)'))
        # Existing document ingestion explicitly maintains this projection. Its
        # writes stay valid; PostgreSQL retrieval uses the same fact rows.
        connection.execute(text("CREATE TABLE IF NOT EXISTS document_chunks_fts(chunk_id TEXT,workspace_id TEXT,file_id TEXT,content TEXT)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS lg_pg_document_projection_id ON document_chunks_fts(chunk_id)"))
        connection.execute(text("DELETE FROM document_chunks_fts"))
        connection.execute(text("INSERT INTO document_chunks_fts(chunk_id,workspace_id,file_id,content) SELECT id,workspace_id,file_id,content FROM file_text_chunks WHERE lifecycle_status='active'"))
        # Verify literal wildcard handling and multilingual substring semantics.
        passed = connection.execute(text("SELECT lower('SQLite 数据库 PostgreSQL') LIKE '%数据库%' AND '100%_literal' LIKE '%100\\%\\_literal%' ESCAPE '\\'" )).scalar_one()
        if not passed:
            raise ValueError("PostgreSQL multilingual search verification failed")


def session_candidates(db, workspace_id, session_ids, terms, time_from, time_to, limit):
    from app.domain.models import ChatSession, Message
    _condition, score = lexical_expression([Message.content, ChatSession.title, Message.role, ChatSession.goal_id, ChatSession.graph_id], terms)
    filters = [Message.workspace_id == workspace_id, Message.session_id.in_(session_ids), Message.status == "completed"]
    if time_from is not None:
        filters.append(Message.created_at >= time_from)
    if time_to is not None:
        filters.append(Message.created_at <= time_to)
    statement = select(Message.id.label("message_id"), (1.0 / (score + 1.0)).label("raw_score")).join(ChatSession, ChatSession.id == Message.session_id).where(
        Message.workspace_id == workspace_id, ChatSession.workspace_id == workspace_id, Message.session_id.in_(session_ids), Message.status == "completed")
    if terms:
        # Keep the indexed OR predicates within one table each. An OR across
        # messages and sessions would otherwise defeat trigram index scans.
        message_match, _ = lexical_expression([Message.content, Message.role], terms)
        session_match, _ = lexical_expression([ChatSession.title, ChatSession.goal_id, ChatSession.graph_id], terms)
        sessions = select(ChatSession.id).where(ChatSession.workspace_id == workspace_id, ChatSession.id.in_(session_ids), session_match)
        candidates = union(select(Message.id).where(*filters, message_match), select(Message.id).where(*filters, Message.session_id.in_(sessions))).subquery()
        statement = statement.join(candidates, candidates.c.id == Message.id)
    if time_from is not None:
        statement = statement.where(Message.created_at >= time_from)
    if time_to is not None:
        statement = statement.where(Message.created_at <= time_to)
    return [dict(row) for row in db.execute(statement.order_by(score.desc(), Message.created_at.desc(), Message.id).limit(limit)).mappings()]


def document_candidates(db, workspace_id, file_ids, terms, scoped_chunk_ids, limit):
    from app.domain.models import FileRecord, FileTextChunk
    condition, score = lexical_expression([FileTextChunk.content], terms)
    statement = select(FileTextChunk, FileRecord.original_name, (1.0 / (score + 1.0)).label("raw_score")).join(FileRecord, FileRecord.id == FileTextChunk.file_id).where(
        FileTextChunk.workspace_id == workspace_id, FileTextChunk.file_id.in_(file_ids), FileTextChunk.lifecycle_status == "active", condition)
    if scoped_chunk_ids is not None:
        statement = statement.where(FileTextChunk.id.in_(scoped_chunk_ids))
    result = []
    for chunk, filename, rank in db.execute(statement.order_by(score.desc(), FileTextChunk.ordinal, FileTextChunk.id).limit(limit)):
        result.append({"chunk_id": chunk.id, "file_id": chunk.file_id, "document_revision_id": chunk.document_revision_id,
            "filename": filename, "locator": chunk.locator, "locator_json": chunk.locator_json, "section_path": chunk.section_path,
            "content": chunk.content, "content_hash": chunk.content_hash, "raw_score": float(rank)})
    return result


def memory_scores(db, scope, terms, limit):
    from app.domain.memory_event_models import MemorySearchDocument
    condition, score = lexical_expression([MemorySearchDocument.subject, MemorySearchDocument.content, MemorySearchDocument.keywords_text, MemorySearchDocument.entity_aliases_text], terms)
    statement = select(MemorySearchDocument.id, score).where(
        MemorySearchDocument.tenant_id == scope.tenant_id,
        or_(MemorySearchDocument.workspace_id == scope.workspace_id, MemorySearchDocument.workspace_id.is_(None)),
        or_(MemorySearchDocument.subject_user_id == scope.principal_user_id, MemorySearchDocument.subject_user_id.is_(None)), condition,
    ).order_by(score.desc(), MemorySearchDocument.id).limit(limit)
    return {str(row[0]): float(row[1]) / (float(row[1]) + 1.0) for row in db.execute(statement)}
