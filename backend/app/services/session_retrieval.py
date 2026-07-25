from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import bindparam, or_, select, text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import (
    ChatSession,
    GraphChangeSet,
    GraphEdge,
    Message,
    Workspace,
)
from app.domain.schemas.chat import (
    SessionFragmentSearchHit,
    SessionFragmentSearchRequest,
    SessionFragmentSearchResponse,
)
from app.repositories.audit import AuditRepository
from app.services.authorization import AuthorizationService


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _quoted_fts_term(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class SessionRetrievalService:
    """ACL-scoped sparse retrieval over the durable chat fact chain."""

    def __init__(
        self,
        db: Session,
        workspace: Workspace,
        actor_id: str,
        authorization: AuthorizationService,
    ) -> None:
        self.db = db
        self.workspace = workspace
        self.workspace_id = workspace.id
        self.actor_id = actor_id
        self.authorization = authorization
        self.audit = AuditRepository(db, workspace.id)

    def search(
        self,
        *,
        current_session_id: str,
        payload: SessionFragmentSearchRequest,
    ) -> SessionFragmentSearchResponse:
        current = self._require_authorized_session(current_session_id)
        sessions = self._authorized_sessions()
        scoped = self._scope_sessions(current, sessions, payload)
        if not scoped:
            return self._response(payload, [])

        terms = self._search_terms(payload)
        rows = self._candidate_rows(
            session_ids=list(scoped),
            terms=terms,
            time_from=payload.time_range.from_ if payload.time_range else None,
            time_to=payload.time_range.to if payload.time_range else None,
            limit=max(40, payload.top_k * 10),
        )
        hits = self._assemble_hits(
            current=current,
            sessions=scoped,
            rows=rows,
            terms=terms,
            payload=payload,
        )
        response = self._response(payload, hits[: payload.top_k])
        query_fingerprint = hashlib.sha256(
            "\n".join(terms).encode("utf-8")
        ).hexdigest() if terms else None
        self.audit.record(
            actor_id=self.actor_id,
            action="session_fragment.search",
            resource_type="session",
            resource_id=current.id,
            details={
                "scope": payload.scope,
                "reason": payload.reason,
                "explicit_session_count": len(payload.session_ids),
                "authorized_session_count": len(scoped),
                "query_fingerprint": query_fingerprint,
                "hit_count": len(response.hits),
                "retrieval_strategy": response.retrieval_strategy,
            },
        )
        self.db.commit()
        return response

    def _response(
        self,
        payload: SessionFragmentSearchRequest,
        hits: list[SessionFragmentSearchHit],
    ) -> SessionFragmentSearchResponse:
        has_terms = bool(self._search_terms(payload))
        strategy = (
            "mixed"
            if (has_terms and payload.session_ids)
            or (payload.graph_node_ids and not has_terms)
            else "fts5_bm25_rules"
            if has_terms
            else "session_id"
        )
        return SessionFragmentSearchResponse(
            query=payload.query,
            scope=payload.scope,
            reason=payload.reason,
            retrieval_strategy=strategy,
            hits=hits,
        )

    def _can_read(self, item: ChatSession) -> bool:
        return (
            self.authorization.can_access_resource(
                self.workspace, "session", item.id, "read"
            )
            and self.authorization.can_access_bindings(
                self.workspace,
                "read",
                project_id=item.project_id,
                goal_id=item.goal_id,
                graph_id=item.graph_id,
            )
        )

    def _require_authorized_session(self, session_id: str) -> ChatSession:
        item = self.db.scalar(
            select(ChatSession).where(
                ChatSession.workspace_id == self.workspace_id,
                ChatSession.id == session_id,
            )
        )
        if item is None or not self._can_read(item):
            raise AppError(404, "not_found", "Session not found in this workspace")
        return item

    def _authorized_sessions(self) -> dict[str, ChatSession]:
        items = self.db.scalars(
            select(ChatSession).where(ChatSession.workspace_id == self.workspace_id)
        ).all()
        return {item.id: item for item in items if self._can_read(item)}

    def _scope_sessions(
        self,
        current: ChatSession,
        authorized: dict[str, ChatSession],
        payload: SessionFragmentSearchRequest,
    ) -> dict[str, ChatSession]:
        if payload.session_ids:
            requested: dict[str, ChatSession] = {}
            for session_id in payload.session_ids:
                item = authorized.get(session_id)
                if item is None:
                    raise AppError(404, "not_found", "Session not found in this workspace")
                requested[session_id] = item
            return requested
        if payload.scope in {"workspace", "all_authorized"}:
            return authorized

        linked_ids = {current.id}
        changed = True
        while changed:
            changed = False
            for item in authorized.values():
                if (
                    item.id in linked_ids
                    or item.parent_session_id in linked_ids
                    or (current.parent_session_id and item.id == current.parent_session_id)
                ):
                    before = len(linked_ids)
                    linked_ids.add(item.id)
                    if item.parent_session_id in authorized:
                        linked_ids.add(item.parent_session_id)
                    changed = changed or len(linked_ids) != before
        return {item_id: authorized[item_id] for item_id in linked_ids if item_id in authorized}

    @staticmethod
    def _search_terms(payload: SessionFragmentSearchRequest) -> list[str]:
        values: list[str] = []
        if payload.query:
            values.append(payload.query)
        values.extend(payload.keywords)
        values.extend(payload.phrases)
        values.extend(entity.value for entity in payload.entities)
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    def _candidate_rows(
        self,
        *,
        session_ids: list[str],
        terms: list[str],
        time_from: datetime | None,
        time_to: datetime | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        dialect = self.db.get_bind().dialect.name
        if dialect != "sqlite":
            raise AppError(
                503,
                "session_search_backend_unavailable",
                "Session sparse retrieval currently requires the SQLite FTS5 projection",
            )
        if not terms:
            statement = (
                select(Message.id.label("message_id"), Message.created_at)
                .where(
                    Message.workspace_id == self.workspace_id,
                    Message.session_id.in_(session_ids),
                    Message.status == "completed",
                )
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
            statement = self._apply_time_filter(statement, time_from, time_to)
            return [
                {"message_id": row.message_id, "raw_score": float(index)}
                for index, row in enumerate(self.db.execute(statement), start=1)
            ]

        fts_terms = [term for term in terms if len(term) >= 3]
        rows: list[dict[str, Any]] = []
        if fts_terms:
            match_query = " OR ".join(_quoted_fts_term(term) for term in fts_terms)
            time_clauses = []
            parameters: dict[str, Any] = {
                "query": match_query,
                "workspace_id": self.workspace_id,
                "session_ids": session_ids,
                "limit": limit,
            }
            if time_from is not None:
                time_clauses.append("AND m.created_at >= :time_from")
                parameters["time_from"] = time_from
            if time_to is not None:
                time_clauses.append("AND m.created_at <= :time_to")
                parameters["time_to"] = time_to
            statement = text(
                f"""
                SELECT m.id AS message_id,
                       bm25(session_messages_fts, 0.0, 0.0, 0.0, 8.0, 5.0, 2.0)
                         AS raw_score
                  FROM session_messages_fts
                  JOIN messages AS m ON m.id = session_messages_fts.message_id
                 WHERE session_messages_fts MATCH :query
                   AND m.workspace_id = :workspace_id
                   AND m.session_id IN :session_ids
                   AND m.status = 'completed'
                   {' '.join(time_clauses)}
                 ORDER BY raw_score ASC, m.created_at DESC
                 LIMIT :limit
                """
            ).bindparams(bindparam("session_ids", expanding=True))
            try:
                rows = [dict(row) for row in self.db.execute(statement, parameters).mappings()]
            except Exception as exc:
                raise AppError(
                    503,
                    "session_search_index_unavailable",
                    "The SQLite session FTS5 projection is unavailable",
                ) from exc

        found_ids = {str(row["message_id"]) for row in rows}
        short_terms = [term for term in terms if len(term) < 3]
        if short_terms or not rows:
            like_terms = (short_terms or terms)[:20]
            like_statement = (
                select(Message.id.label("message_id"), Message.created_at)
                .join(ChatSession, ChatSession.id == Message.session_id)
                .where(
                    Message.workspace_id == self.workspace_id,
                    Message.session_id.in_(session_ids),
                    Message.status == "completed",
                    or_(
                        *(
                            condition
                            for term in like_terms
                            for condition in (
                                Message.content.contains(term),
                                ChatSession.title.contains(term),
                            )
                        )
                    ),
                )
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
            like_statement = self._apply_time_filter(like_statement, time_from, time_to)
            for index, row in enumerate(self.db.execute(like_statement), start=1):
                if row.message_id not in found_ids:
                    rows.append(
                        {"message_id": row.message_id, "raw_score": float(index)}
                    )
                    found_ids.add(row.message_id)
                if len(rows) >= limit:
                    break
        return rows[:limit]

    @staticmethod
    def _apply_time_filter(statement, time_from: datetime | None, time_to: datetime | None):
        if time_from is not None:
            statement = statement.where(Message.created_at >= time_from)
        if time_to is not None:
            statement = statement.where(Message.created_at <= time_to)
        return statement

    def _assemble_hits(
        self,
        *,
        current: ChatSession,
        sessions: dict[str, ChatSession],
        rows: list[dict[str, Any]],
        terms: list[str],
        payload: SessionFragmentSearchRequest,
    ) -> list[SessionFragmentSearchHit]:
        candidate_ids = [str(row["message_id"]) for row in rows]
        if not candidate_ids:
            return []
        candidates = list(
            self.db.scalars(
                select(Message).where(
                    Message.workspace_id == self.workspace_id,
                    Message.id.in_(candidate_ids),
                )
            ).all()
        )
        by_id = {item.id: item for item in candidates}
        parent_ids = {
            item.parent_message_id for item in candidates if item.parent_message_id
        }
        extra = list(
            self.db.scalars(
                select(Message).where(
                    Message.workspace_id == self.workspace_id,
                    or_(
                        Message.id.in_(parent_ids) if parent_ids else Message.id == "",
                        Message.parent_message_id.in_(candidate_ids),
                    ),
                    Message.status == "completed",
                )
            ).all()
        )
        all_messages = {item.id: item for item in [*candidates, *extra]}
        children: dict[str, list[Message]] = {}
        for item in all_messages.values():
            if item.parent_message_id:
                children.setdefault(item.parent_message_id, []).append(item)

        confirmed_ids = set(
            self.db.scalars(
                select(GraphChangeSet.source_user_message_id).where(
                    GraphChangeSet.workspace_id == self.workspace_id,
                    GraphChangeSet.status == "confirmed",
                    GraphChangeSet.source_user_message_id.in_(list(all_messages)),
                )
            ).all()
        )
        confirmed_ids.update(
            self.db.scalars(
                select(GraphChangeSet.source_assistant_message_id).where(
                    GraphChangeSet.workspace_id == self.workspace_id,
                    GraphChangeSet.status == "confirmed",
                    GraphChangeSet.source_assistant_message_id.in_(list(all_messages)),
                )
            ).all()
        )
        adjacent_nodes = self._adjacent_nodes(payload.graph_node_ids)
        rank_by_id = {message_id: index for index, message_id in enumerate(candidate_ids)}
        now = datetime.now(timezone.utc)
        seen_fragments: set[str] = set()
        hits: list[SessionFragmentSearchHit] = []

        for candidate_id in candidate_ids:
            message = by_id.get(candidate_id)
            if message is None:
                continue
            user_message = (
                all_messages.get(message.parent_message_id)
                if message.role == "assistant" and message.parent_message_id
                else message
                if message.role == "user"
                else None
            )
            assistant_message = None
            if user_message is not None:
                assistant_message = next(
                    (
                        item
                        for item in sorted(
                            children.get(user_message.id, []),
                            key=lambda child: child.created_at,
                        )
                        if item.role == "assistant"
                    ),
                    None,
                )
            fragment_messages = [
                item for item in (user_message, assistant_message) if item is not None
            ] or [message]
            fragment_id = "fragment:" + ":".join(item.id for item in fragment_messages)
            if fragment_id in seen_fragments:
                continue
            seen_fragments.add(fragment_id)

            session = sessions.get(message.session_id)
            if session is None:
                continue
            content = "\n".join(
                f"{item.role}: {(item.content or '').strip()}"
                for item in fragment_messages
                if (item.content or "").strip()
            ).strip()
            if not content:
                continue
            message_ids = [item.id for item in fragment_messages]
            node_ids = set().union(
                *(self._message_node_ids(item) for item in fragment_messages)
            )
            relation = self._relation(
                current=current,
                candidate=session,
                node_ids=node_ids,
                requested_nodes=set(payload.graph_node_ids),
                adjacent_nodes=adjacent_nodes,
            )
            status = self._status(session, message_ids, confirmed_ids)
            if payload.status and status not in payload.status:
                continue
            searchable_text = f"{session.title}\n{content}".casefold()
            matched_terms = [
                term for term in terms if term.casefold() in searchable_text
            ]
            lexical_rank = min(rank_by_id.get(item.id, len(rows)) for item in fragment_messages)
            lexical = max(0.0, 1.0 - lexical_rank / max(1, len(rows)))
            if matched_terms:
                lexical = min(1.0, lexical + min(0.2, 0.04 * len(matched_terms)))
            relation_score = {
                "current_session": 1.0,
                "parent": 0.9,
                "child": 0.9,
                "same_graph_node": 1.0,
                "adjacent_graph_node": 0.75,
                "same_workspace": 0.35,
            }[relation]
            same_graph = 1.0 if current.graph_id and current.graph_id == session.graph_id else 0.0
            state_score = {
                "confirmed": 1.0,
                "current": 0.9,
                "possibly_current": 0.55,
                "superseded": 0.1,
            }[status]
            created_at = max(_utc(item.created_at) for item in fragment_messages)
            age_days = max(0.0, (now - created_at).total_seconds() / 86_400)
            recency = math.exp(-age_days / 90.0) if payload.prefer_recent else 0.5
            score = (
                lexical * 0.40
                + max(relation_score, same_graph * 0.8) * 0.35
                + state_score * 0.20
                + recency * 0.05
            )
            hits.append(
                SessionFragmentSearchHit(
                    result_id=fragment_id,
                    source_session_id=session.id,
                    session_title=session.title,
                    fragment_type=self._fragment_type(fragment_messages, status),
                    snippet=content[:1_200],
                    matched_terms=matched_terms,
                    relation=relation,
                    status=status,
                    score=round(min(1.0, max(0.0, score)), 6),
                    created_at=created_at,
                    message_ids=message_ids,
                )
            )
        hits.sort(key=lambda item: (item.score, item.created_at), reverse=True)
        return hits

    def _adjacent_nodes(self, node_ids: list[str]) -> set[str]:
        if not node_ids:
            return set()
        rows = self.db.execute(
            select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
                GraphEdge.workspace_id == self.workspace_id,
                or_(
                    GraphEdge.source_node_id.in_(node_ids),
                    GraphEdge.target_node_id.in_(node_ids),
                ),
            )
        ).all()
        return {
            target if source in node_ids else source
            for source, target in rows
        }

    @staticmethod
    def _message_node_ids(message: Message) -> set[str]:
        values: set[str] = set()
        for part in message.parts or []:
            if not isinstance(part, dict):
                continue
            data = part.get("data") if isinstance(part.get("data"), dict) else part
            for key in ("node_ids", "graph_node_ids"):
                raw = data.get(key)
                if isinstance(raw, list):
                    values.update(item for item in raw if isinstance(item, str))
            for key in ("node_id", "graph_node_id"):
                raw = data.get(key)
                if isinstance(raw, str):
                    values.add(raw)
        return values

    @staticmethod
    def _relation(
        *,
        current: ChatSession,
        candidate: ChatSession,
        node_ids: set[str],
        requested_nodes: set[str],
        adjacent_nodes: set[str],
    ) -> str:
        if requested_nodes and node_ids.intersection(requested_nodes):
            return "same_graph_node"
        if adjacent_nodes and node_ids.intersection(adjacent_nodes):
            return "adjacent_graph_node"
        if candidate.id == current.id:
            return "current_session"
        if candidate.id == current.parent_session_id:
            return "parent"
        if candidate.parent_session_id == current.id:
            return "child"
        return "same_workspace"

    @staticmethod
    def _status(
        session: ChatSession,
        message_ids: list[str],
        confirmed_ids: set[str],
    ) -> str:
        if any(message_id in confirmed_ids for message_id in message_ids):
            return "confirmed"
        if session.status == "archived" or session.archived_at is not None:
            return "superseded"
        if session.status in {"closed", "completed"} or session.closed_at is not None:
            return "possibly_current"
        return "current"

    @staticmethod
    def _fragment_type(messages: list[Message], status: str) -> str:
        part_types = {
            str(part.get("type") or part.get("part_type") or "")
            for message in messages
            for part in (message.parts or [])
            if isinstance(part, dict)
        }
        if "quiz" in part_types:
            return "assessment"
        if status == "confirmed":
            return "decision"
        if any(message.role == "system" for message in messages):
            return "summary"
        return "conversation"
