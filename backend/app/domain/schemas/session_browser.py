"""Small, separately cached representations for the session navigator."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SessionSummaryView(BaseModel):
    id: str
    workspace_id: str
    title: str
    project_id: str | None
    parent_session_id: str | None
    goal_id: str | None
    graph_id: str | None
    source_message_id: str | None
    memory_enabled: bool
    closed_at: datetime | None
    archived_at: datetime | None
    writeback_policy: str
    status: str
    pinned: bool
    session_kind: str
    activity_summary: str | None
    created_at: datetime
    updated_at: datetime
    has_children: bool


class SessionPageView(BaseModel):
    items: list[SessionSummaryView]
    next_cursor: str | None = None
    previous_cursor: str | None = None
    has_more: bool = False
    has_previous: bool = False


class SessionCountsView(BaseModel):
    total: int
    active: int
    archived: int
    memory_enabled: int
