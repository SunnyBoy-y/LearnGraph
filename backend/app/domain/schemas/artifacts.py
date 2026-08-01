from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.schemas.common import ORMModel


class ArtifactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=2000)


class ArtifactVersionCreate(BaseModel):
    file_id: str
    source_chat_session_id: str | None = None
    release_notes: str = Field(default="", max_length=4000)


class ArtifactShareTokenCreate(BaseModel):
    label: str = Field(default="", max_length=120)
    expires_at: datetime | None = None
    max_downloads: int | None = Field(default=None, ge=1)


class ArtifactView(ORMModel):
    id: str
    tenant_id: str
    workspace_id: str
    created_by: str
    name: str
    description: str
    status: str


class ArtifactVersionView(ORMModel):
    id: str
    artifact_id: str
    version: int
    file_id: str
    original_name: str
    sha256: str
    size_bytes: int
    mime_type: str
    source_workspace_id: str
    source_chat_session_id: str | None = None
    published_by: str
    release_notes: str
    status: str


class ArtifactShareTokenView(ORMModel):
    id: str
    artifact_version_id: str
    token_prefix: str
    label: str
    expires_at: datetime | None = None
    max_downloads: int | None = None
    download_count: int
    revoked_at: datetime | None = None


class ArtifactShareTokenCreated(ArtifactShareTokenView):
    token: str
