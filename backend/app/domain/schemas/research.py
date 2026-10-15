from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.schemas.common import ORMModel
from app.domain.schemas.management import Money, MoneyInput


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
    model_config = ConfigDict(extra="forbid")
    currency: Literal["USD", "CNY"] = "USD"
    budget_amount: MoneyInput = "0"
    source_scope: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    approved: bool = False


class ResearchJobView(ORMModel):
    id: str
    workspace_id: str
    question: str
    status: str
    provider_id: str
    currency: Literal["USD", "CNY"] | None
    budget_amount: Money | None
    estimated_cost_amount: Money | None
    actual_cost_amount: Money | None
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
    quote_id: str | None = None


class ResearchPlanView(BaseModel):
    provider_id: str
    provider_capabilities: dict[str, Any]
    question: str
    currency: Literal["USD", "CNY"]
    budget_amount: Money
    estimated_cost_amount: Money | None
    requires_approval: bool


class ResearchJobEventView(ORMModel):
    id: str
    workspace_id: str
    research_job_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
