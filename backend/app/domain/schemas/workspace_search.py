from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SearchKind = Literal["all", "session", "message", "file"]


class WorkspaceSearchHit(BaseModel):
    kind: Literal["session", "message", "file"]
    id: str
    title: str
    snippet: str = ""
    session_id: str | None = None
    updated_at: datetime
    match_field: Literal["id", "title", "content"]


class WorkspaceSearchPage(BaseModel):
    items: list[WorkspaceSearchHit]
    has_more: bool
    next_offset: int
