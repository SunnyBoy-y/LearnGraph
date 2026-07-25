from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.schemas.common import ORMModel


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    primary_goal_id: str | None = None
    primary_graph_id: str | None = None


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    primary_goal_id: str | None = None
    primary_graph_id: str | None = None
    position: int | None = Field(default=None, ge=0)


class ProjectView(ORMModel):
    id: str
    workspace_id: str
    title: str
    status: str
    primary_goal_id: str | None
    primary_graph_id: str | None
    position: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ImpactItem(BaseModel):
    resource_type: str
    count: int
    action: str


class DeleteImpact(BaseModel):
    resource_type: str
    resource_id: str
    title: str
    impacts: list[ImpactItem]
    confirmation_text: str


class DeleteConfirm(BaseModel):
    confirmation_text: str


class SessionBatchSelection(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("session_ids")
    @classmethod
    def normalize_session_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            session_id = value.strip()
            if not session_id or len(session_id) > 36:
                raise ValueError("Each session ID must contain 1 to 36 characters")
            if session_id not in seen:
                normalized.append(session_id)
                seen.add(session_id)
        if not normalized:
            raise ValueError("At least one session ID is required")
        return normalized


class SessionBatchDeleteConfirm(SessionBatchSelection):
    confirmation_text: str = Field(min_length=1, max_length=128)


class SessionBatchDeleteImpact(DeleteImpact):
    session_ids: list[str]


class SessionBatchDeleteResponse(BaseModel):
    status: Literal["deleted"] = "deleted"
    deleted_session_ids: list[str]
    deleted_count: int
    impacts: list[ImpactItem]


class SessionProjectUpdate(BaseModel):
    project_id: str | None = None


TargetType = Literal["project", "goal", "graph", "node"]


class SourceLinkCreate(BaseModel):
    target_type: TargetType
    target_id: str = Field(min_length=1, max_length=36)
    relation: str = Field(default="reference", min_length=1, max_length=40)


class SourceLinkView(ORMModel):
    id: str
    workspace_id: str
    source_id: str
    target_type: str
    target_id: str
    relation: str
    created_at: datetime


class ActionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    action_type: str = Field(default="todo", min_length=1, max_length=48)
    project_id: str | None = None
    goal_id: str | None = None
    graph_id: str | None = None
    node_id: str | None = None
    due_at: datetime | None = None
    priority: int = Field(default=50, ge=0, le=100)


class ActionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    status: Literal["pending", "in_progress", "completed", "archived"] | None = None
    due_at: datetime | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    position: int | None = Field(default=None, ge=0)


class ActionView(ORMModel):
    id: str
    workspace_id: str
    title: str
    description: str
    status: str
    source: str
    action_type: str
    project_id: str | None
    goal_id: str | None
    graph_id: str | None
    node_id: str | None
    roadmap_id: str | None
    day_index: int
    duration_minutes: int
    due_at: datetime | None
    priority: int
    position: int
    completed_at: datetime | None
    metadata_json: dict
    created_at: datetime
    updated_at: datetime


class CompositeCreate(BaseModel):
    target_message_id: str = Field(min_length=1, max_length=36)
    source_version_ids: list[str] = Field(min_length=2, max_length=8)


class CompositeView(ORMModel):
    id: str
    workspace_id: str
    target_message_id: str
    source_version_ids: list[str]
    content: str
    parts: list[dict]
    status: str
    confirmed_version_id: str | None
    created_at: datetime
    updated_at: datetime


class RoadmapView(ORMModel):
    id: str
    workspace_id: str
    goal_id: str
    graph_id: str | None
    graph_revision: int | None
    title: str
    version: int
    status: str
    rationale: str
    planning_snapshot: dict
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[ActionView] = Field(default_factory=list)


class RoadmapVersionView(ORMModel):
    id: str
    workspace_id: str
    goal_id: str
    graph_id: str | None
    graph_revision: int | None
    title: str
    version: int
    status: str
    rationale: str
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RoadmapItemReschedule(BaseModel):
    base_version: int = Field(ge=1)
    day_index: int = Field(ge=1, le=365)
    position: int = Field(default=0, ge=0, le=500)
    duration_minutes: int | None = Field(default=None, ge=15, le=1_440)
    rationale: str = Field(min_length=1, max_length=2_000)

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A roadmap revision rationale is required")
        return normalized


class RoadmapReject(BaseModel):
    base_version: int = Field(ge=1)
    rationale: str = Field(min_length=1, max_length=2_000)

    @field_validator("rationale")
    @classmethod
    def normalize_rejection_rationale(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A roadmap rejection rationale is required")
        return normalized
