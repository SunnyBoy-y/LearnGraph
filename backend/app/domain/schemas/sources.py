from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.schemas.common import ORMModel


class FetchSourceRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4_000)
    authorized_domains: list[str] = Field(min_length=1, max_length=50)
    research_job_id: str | None = Field(default=None, max_length=36)


class SourceRecordView(ORMModel):
    id: str
    workspace_id: str
    provider_id: str
    source_url: str
    final_url: str
    title: str
    content: str
    content_hash: str
    content_type: str
    authorized_domain: str
    cache_status: str
    research_job_id: str | None
    metadata_json: dict[str, Any]
    created_at: datetime
