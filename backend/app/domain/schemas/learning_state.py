from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class LearningEvidenceRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    source_type: str = Field(min_length=1, max_length=50)
    summary: str = Field(min_length=1, max_length=50_000)
    result: Literal["observed", "correct", "incorrect", "passed", "failed", "misconception"] = "observed"
    difficulty: float = Field(default=0.5, ge=0.0, le=1.0)
    assistance_level: float = Field(default=0.0, ge=0.0, le=1.0)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_ref: str = Field(default="", max_length=200)
    source_version_id: str | None = Field(default=None, max_length=64)
    source_content_hash: str = Field(default="", max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=160)


class LearningNodeStateView(BaseModel):
    node_id: str
    status: str
    mastery_score: float
    confidence: float
    evidence_count: int
    misconceptions: list[dict[str, Any]]
    last_assessed_at: datetime | None
    next_review_at: datetime | None
    evidence_ids: list[str]
    algorithm_version: str
