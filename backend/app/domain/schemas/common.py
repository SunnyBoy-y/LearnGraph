from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    database: str
    remote_capabilities_enabled: bool


class ActionResponse(BaseModel):
    status: str
    message: str
    resource_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AuditView(ORMModel):
    id: str
    workspace_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    trace_id: str
    details: dict[str, Any]
    created_at: datetime
