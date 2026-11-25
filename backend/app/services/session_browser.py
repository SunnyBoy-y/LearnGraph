"""Keyset session browsing. Authorization is part of SQL, before LIMIT.

Never materialize the workspace's IDs or the large context/model JSON just to
paint the navigator. Legacy /sessions remains a separate compatibility API.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Date, and_, cast, exists, false, func, or_, select, true
from sqlalchemy.orm import Session, aliased

from app.core.errors import AppError
from app.core.security import Principal
from app.domain.models import ChatSession, Membership, ResourceACL, Workspace
from app.domain.schemas.chat import SessionView
from app.domain.schemas.session_browser import SessionCountsView, SessionPageView, SessionSummaryView
from app.services.authorization import AuthorizationService

SUMMARY_FIELDS = (
    "id", "workspace_id", "title", "project_id", "parent_session_id", "goal_id",
    "graph_id", "source_message_id", "memory_enabled", "closed_at", "archived_at", "writeback_policy",
    "status", "pinned", "session_kind", "activity_summary", "created_at", "updated_at",
)


class SessionBrowser:
    def __init__(self, db: Session, workspace: Workspace, principal: Principal):
        self.db, self.workspace, self.principal = db, workspace, principal
        self.authz = AuthorizationService(db, principal)
        self.owner = workspace.owner_user_id == principal.user_id
        self.readable = "workspace.read" in self.authz.workspace_permissions(workspace)
        self.membership = db.scalar(select(Membership).where(
            Membership.organization_id == workspace.organization_id,
            Membership.user_id == principal.user_id,
            Membership.status == "active",
        )) if workspace.organization_id and not self.owner else None

    def readable_predicate(self, model=ChatSession):
        if not self.readable:
            return false()
        if self.owner:
            return true()
        acl_scope = [
            ResourceACL.workspace_id == self.workspace.id,
            ResourceACL.tenant_id == self.workspace.tenant_id,
            ResourceACL.resource_type == "session", ResourceACL.resource_id == model.id,
            ResourceACL.revoked_at.is_(None),
        ]
        grantees = [and_(ResourceACL.grantee_type == "user", ResourceACL.grantee_id == self.principal.user_id)]
        if self.workspace.organization_id:
            grantees.append(and_(ResourceACL.grantee_type == "organization", ResourceACL.grantee_id == self.workspace.organization_id))
        if self.membership:
            grantees.append(and_(ResourceACL.grantee_type == "role", ResourceACL.grantee_id == self.membership.role_id))
        if self.db.get_bind().dialect.name == "postgresql":
            permissions = func.json_array_elements_text(ResourceACL.permissions).table_valued("value").render_derived(name="acl_permission")
        else:
            permissions = func.json_each(ResourceACL.permissions).table_valued("value").alias("acl_permission")
        permits = exists(select(1).select_from(permissions).where(permissions.c.value == "read").correlate(ResourceACL))
        any_acl = exists(select(1).select_from(ResourceACL).where(*acl_scope).correlate(model))
        allowed = exists(select(1).select_from(ResourceACL).where(*acl_scope, or_(*grantees), permits).correlate(model))
        return or_(~any_acl, allowed)

    def scope(self, model=ChatSession, *, include_archived=False, project_id=None, ungrouped=False):
        predicates = [model.workspace_id == self.workspace.id, self.readable_predicate(model)]
        if not include_archived:
            predicates.append(model.status != "archived")
        if ungrouped:
            predicates.append(model.project_id.is_(None))
        elif project_id:
            predicates.append(model.project_id == project_id)
        return predicates

    def detail(self, session_id: str) -> SessionView:
        item = self.db.scalar(select(ChatSession).where(
            *self.scope(include_archived=True), ChatSession.id == session_id,
        ))
        if item is None:
            raise AppError(404, "session_not_found", "Session not found")
        return SessionView.model_validate(item)

    def require_summary(self, session_id: str) -> SessionSummaryView:
        row = self.db.execute(select(*[getattr(ChatSession, key) for key in SUMMARY_FIELDS]).where(
            *self.scope(include_archived=True), ChatSession.id == session_id,
        )).mappings().first()
        if row is None:
            raise AppError(404, "session_not_found", "Session not found")
        return SessionSummaryView(**row, has_children=False)

    def ancestors(self, session_id: str) -> list[SessionSummaryView]:
        """Bounded deep-link path only; never loads JSON or a sibling collection."""
        item = self.require_summary(session_id)
        result, seen = [], {item.id}
        while item.parent_session_id and len(result) < 32:
            if item.parent_session_id in seen:
                break
            try:
                parent = self.require_summary(item.parent_session_id)
            except AppError:
                break
            if parent.status == "archived" or parent.project_id != item.project_id:
                break
            seen.add(parent.id)
            data = {key: getattr(parent, key) for key in SUMMARY_FIELDS}
            result.append(SessionSummaryView(**data, has_children=True))
            item = parent
        return result

    def activity(self, start: datetime, end: datetime, timezone_offset: int, timezone_name: str | None = None) -> dict[str, int]:
        start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
        end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
        if end <= start or end - start > timedelta(days=370):
            raise AppError(422, "invalid_activity_range", "Activity range must be within 370 days")
        zone = None
        if timezone_name:
            try:
                zone = ZoneInfo(timezone_name)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise AppError(422, "invalid_activity_timezone", "Invalid activity timezone") from exc
        if self.db.get_bind().dialect.name == "postgresql":
            day = cast(func.timezone(timezone_name, ChatSession.created_at), Date) if zone else cast(ChatSession.created_at - timedelta(minutes=timezone_offset), Date)
        elif zone:
            # Group in SQL without returning individual session records. An IANA
            # zone, rather than today's fixed offset, preserves historical DST.
            def local_day(value):
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(zone).date().isoformat()
            connection = self.db.connection().connection.driver_connection
            function_name = 'session_day_' + hashlib.sha256(timezone_name.encode()).hexdigest()[:12]
            connection.create_function(function_name, 1, local_day, deterministic=True)
            day = getattr(func, function_name)(ChatSession.created_at)
        else:
            day = func.strftime("%Y-%m-%d", ChatSession.created_at, f"{-timezone_offset} minutes")
        rows = self.db.execute(select(day.label("day"), func.count()).where(
            *self.scope(include_archived=True), ChatSession.created_at >= start, ChatSession.created_at < end,
        ).group_by(day)).all()
        return {str(day): count for day, count in rows}

    def counts(self) -> SessionCountsView:
        rows = self.db.execute(select(ChatSession.status, func.count()).where(
            *self.scope(include_archived=True),
        ).group_by(ChatSession.status)).all()
        counts = dict(rows)
        memory_enabled = self.db.scalar(select(func.count()).select_from(ChatSession).where(
            *self.scope(include_archived=True), ChatSession.memory_enabled.is_(True),
        )) or 0
        return SessionCountsView(total=sum(counts.values()), active=sum(value for key, value in counts.items() if key != "archived"), archived=counts.get("archived", 0), memory_enabled=memory_enabled)

    def page(self, *, limit=20, cursor=None, project_id=None, ungrouped=False,
             parent_session_id=None, roots_only=True, include_archived=False, archived_only=False,
             graph_id=None, node_id=None, session_kind=None, query=None, direction="next", created_after=None, created_before=None, pinned_first=True) -> SessionPageView:
        if project_id and ungrouped:
            raise AppError(422, "invalid_session_scope", "project_id and ungrouped are mutually exclusive")
        if parent_session_id:
            parent = self.require_summary(parent_session_id)
            # The view and its parent must have the same grouping boundary.
            if (project_id and parent.project_id != project_id) or (ungrouped and parent.project_id is not None):
                raise AppError(404, "session_not_found", "Session not found")
            project_id, ungrouped = parent.project_id, parent.project_id is None
        scope = dict(project_id=project_id, ungrouped=ungrouped, include_archived=include_archived or archived_only)
        def display_scope(model=ChatSession):
            filters = self.scope(model, **scope)
            if archived_only:
                filters.append(model.status == "archived")
            if created_after:
                filters.append(model.created_at >= created_after)
            if created_before:
                filters.append(model.created_at < created_before)
            if graph_id:
                filters.append(model.graph_id == graph_id)
            if node_id:
                filters.append(model.context_capsule["current_node_id"].as_string() == node_id)
            if session_kind:
                filters.append(model.session_kind == session_kind)
            if query:
                escaped = query.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                filters.append(or_(func.lower(model.title).like(f"%{escaped}%", escape="\\"), model.id == query.strip()))
            return filters
        predicates = display_scope()
        Parent, Child = aliased(ChatSession), aliased(ChatSession)
        same_group_parent = or_(Parent.project_id == ChatSession.project_id, and_(Parent.project_id.is_(None), ChatSession.project_id.is_(None)))
        if parent_session_id:
            predicates.extend([ChatSession.parent_session_id == parent_session_id, ChatSession.id != parent_session_id])
        elif roots_only:
            visible_parent = exists(select(1).select_from(Parent).where(
                *display_scope(Parent), Parent.id == ChatSession.parent_session_id,
                Parent.id != ChatSession.id, same_group_parent,
            ).correlate(ChatSession))
            predicates.append(~visible_parent)
        child_group = or_(Child.project_id == ChatSession.project_id, and_(Child.project_id.is_(None), ChatSession.project_id.is_(None)))
        has_children = exists(select(1).select_from(Child).where(
            *display_scope(Child), Child.parent_session_id == ChatSession.id,
            Child.id != ChatSession.id, child_group,
        ).correlate(ChatSession)).label("has_children")
        scope_hash = hashlib.sha256(json.dumps([
            self.principal.tenant_id, self.principal.user_id, self.workspace.id,
            project_id, ungrouped, parent_session_id, roots_only, include_archived,
            graph_id, node_id, session_kind, query, pinned_first,
            created_after.isoformat() if created_after else None, created_before.isoformat() if created_before else None,
            *(["archived_only"] if archived_only else []),
        ], separators=(",", ":")).encode()).hexdigest()[:24]
        if cursor:
            try:
                payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if not isinstance(payload, dict):
                    raise ValueError("payload")
                if payload["scope"] != scope_hash or type(payload["pinned"]) is not bool:
                    raise ValueError("scope")
                updated_at = datetime.fromisoformat(payload["updated_at"])
                item_id = payload["id"]
                if not isinstance(item_id, str) or not item_id or len(item_id) > 64:
                    raise ValueError("id")
                pinned = payload["pinned"]
            except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
                raise AppError(422, "invalid_session_cursor", "Invalid or mismatched session cursor") from exc
            if not pinned_first:
                comparison = ChatSession.updated_at > updated_at if direction == "previous" else ChatSession.updated_at < updated_at
                tie = ChatSession.id > item_id if direction == "previous" else ChatSession.id < item_id
                predicates.append(or_(comparison, and_(ChatSession.updated_at == updated_at, tie)))
            elif direction == "previous":
                predicates.append(or_(
                    and_(ChatSession.pinned.is_(True), not pinned),
                    and_(ChatSession.pinned == pinned, ChatSession.updated_at > updated_at),
                    and_(ChatSession.pinned == pinned, ChatSession.updated_at == updated_at, ChatSession.id > item_id),
                ))
            else:
                predicates.append(or_(
                    and_(ChatSession.pinned.is_(False), pinned),
                    and_(ChatSession.pinned == pinned, ChatSession.updated_at < updated_at),
                    and_(ChatSession.pinned == pinned, ChatSession.updated_at == updated_at, ChatSession.id < item_id),
                ))
        backward = direction == "previous" and cursor is not None
        ordering = [ChatSession.pinned, ChatSession.updated_at, ChatSession.id] if pinned_first else [ChatSession.updated_at, ChatSession.id]
        statement = select(*[getattr(ChatSession, key) for key in SUMMARY_FIELDS], has_children).where(
            *predicates,
        ).order_by(*[column.asc() if backward else column.desc() for column in ordering]).limit(limit + 1)
        rows = self.db.execute(statement).mappings().all()
        items = [SessionSummaryView(**row) for row in rows[:limit]]
        if backward:
            items.reverse()
        def encode(item):
            return base64.urlsafe_b64encode(json.dumps({
                "scope": scope_hash, "pinned": item.pinned, "updated_at": item.updated_at.isoformat(), "id": item.id,
            }, separators=(",", ":")).encode()).decode().rstrip("=")
        has_more = bool(cursor) if backward else len(rows) > limit
        has_previous = len(rows) > limit if backward else bool(cursor)
        return SessionPageView(
            items=items, has_more=has_more, has_previous=has_previous,
            next_cursor=encode(items[-1]) if items and has_more else None,
            previous_cursor=encode(items[0]) if items and has_previous else None,
        )
