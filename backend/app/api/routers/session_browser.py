"""Bounded session navigator endpoints, separate from the legacy list API."""
from __future__ import annotations

from typing import Literal
from datetime import datetime

from fastapi import APIRouter, Query, Response

from app.api.deps import CurrentWorkspace, DB
from app.domain.schemas.chat import SessionView
from app.domain.schemas.session_browser import SessionCountsView, SessionPageView, SessionSummaryView
from app.services.session_browser import SessionBrowser

router = APIRouter(prefix="/sessions", tags=["chat"])


def browser(db: DB, context: CurrentWorkspace) -> SessionBrowser:
    return SessionBrowser(db, context.workspace, context.principal)


@router.get("/page", response_model=SessionPageView)
def page_sessions(
    db: DB, context: CurrentWorkspace, response: Response,
    limit: int = Query(20, ge=1, le=100), cursor: str | None = Query(None, max_length=2048),
    project_id: str | None = Query(None, min_length=1, max_length=64), ungrouped: bool = False,
    parent_session_id: str | None = Query(None, min_length=1, max_length=64),
    roots_only: bool = True, include_archived: bool = False, pinned_first: bool = True,
    graph_id: str | None = Query(None, min_length=1, max_length=64),
    node_id: str | None = Query(None, min_length=1, max_length=64),
    session_kind: str | None = Query(None, min_length=1, max_length=32),
    q: str | None = Query(None, max_length=240), direction: Literal["next", "previous"] = "next",
    created_after: datetime | None = None, created_before: datetime | None = None,
) -> SessionPageView:
    response.headers["Cache-Control"] = "private, no-store"
    return browser(db, context).page(
        limit=limit, cursor=cursor, project_id=project_id, ungrouped=ungrouped,
        parent_session_id=parent_session_id, roots_only=roots_only,
        include_archived=include_archived, graph_id=graph_id, node_id=node_id, session_kind=session_kind, query=q, direction=direction,
        created_after=created_after, created_before=created_before, pinned_first=pinned_first,
    )


@router.get("/summary", response_model=SessionCountsView)
def session_counts(db: DB, context: CurrentWorkspace, response: Response) -> SessionCountsView:
    response.headers["Cache-Control"] = "private, no-store"
    return browser(db, context).counts()


@router.get("/activity", response_model=dict[str, int])
def session_activity(db: DB, context: CurrentWorkspace, start: datetime, end: datetime,
                     timezone_offset: int = Query(0, ge=-840, le=840),
                     timezone_name: str | None = Query(None, max_length=80)) -> dict[str, int]:
    return browser(db, context).activity(start, end, timezone_offset, timezone_name)


@router.get("/{session_id}/ancestors", response_model=list[SessionSummaryView])
def session_ancestors(session_id: str, db: DB, context: CurrentWorkspace) -> list[SessionSummaryView]:
    return browser(db, context).ancestors(session_id)


@router.get("/{session_id}", response_model=SessionView)
def get_session(session_id: str, db: DB, context: CurrentWorkspace, response: Response) -> SessionView:
    response.headers["Cache-Control"] = "private, no-store"
    return browser(db, context).detail(session_id)
