from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.schemas.common import ORMModel


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1_000)
    max_results: int = Field(default=5, ge=1, le=20)
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    search_route: Literal["external", "local", "auto"] = "auto"


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    source_type: str
    fetched_at: datetime
    # Optional provider-supplied thumbnail. Untrusted display reference only —
    # the backend never proxies/downloads it.
    image_url: str | None = None


class SearchResponse(BaseModel):
    provider_id: str
    remote_capability: bool
    query: str
    results: list[SearchResult]
    notice: str


class ResearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=4_000)
    budget_cny: float = Field(default=0, ge=0, le=10_000)
    source_scope: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    approved: bool = False


class ResearchJobView(ORMModel):
    id: str
    workspace_id: str
    question: str
    status: str
    provider_id: str
    budget_cny: float
    estimated_cost_cny: float
    actual_cost_cny: float
    provider_task_id: str | None
    approval_status: str
    source_scope: list[str]
    allowed_domains: list[str]
    error_message: str | None
    billing_snapshot: dict[str, Any]
    evidence_pack: dict[str, Any]
    created_at: datetime


class ResearchApprovalRequest(BaseModel):
    approved: bool


class ResearchPlanView(BaseModel):
    provider_id: str
    provider_capabilities: dict[str, Any]
    question: str
    budget_cny: float
    estimated_cost_cny: float
    requires_approval: bool


class ResearchJobEventView(ORMModel):
    id: str
    workspace_id: str
    research_job_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
