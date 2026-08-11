from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.schemas.common import ORMModel


class ArtifactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=2000)


class ArtifactUpdate(BaseModel):
    """Partial update of an artifact's mutable metadata (name/description)."""

    name: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=2000)


class ArtifactVersionCreate(BaseModel):
    file_id: str
    source_chat_session_id: str | None = None
    release_notes: str = Field(default="", max_length=4000)


class ArtifactVersionUpdate(BaseModel):
    """Partial update of a version's release notes (content itself is immutable)."""

    release_notes: str | None = Field(default=None, max_length=4000)


class ArtifactShareTokenCreate(BaseModel):
    label: str = Field(default="", max_length=120)
    expires_at: datetime | None = None
    max_downloads: int | None = Field(default=None, ge=1)


class ArtifactCardPublish(BaseModel):
    """Publish a card draft as the next immutable version."""

    release_notes: str = Field(default="", max_length=4000)


class ArtifactCardShareTokenCreate(BaseModel):
    label: str = Field(default="", max_length=120)
    expires_at: datetime | None = None
    max_views: int | None = Field(default=None, ge=1)


class ArtifactView(ORMModel):
    id: str
    tenant_id: str
    workspace_id: str
    created_by: str
    name: str
    description: str
    status: str
    created_at: datetime


class ArtifactCardView(ORMModel):
    """List/summary view of an indexed chat card (no preview payload)."""

    id: str
    card_id: str
    card_instance_id: str
    card_type: str
    interactive: bool
    title: str
    status: str
    chat_session_id: str | None = None
    message_id: str | None = None
    version_count: int = 0
    latest_version: int = 0
    # True when the card has published versions but the draft changed after the
    # latest publish (an unpublished draft update exists).
    draft_dirty: bool = False
    created_at: datetime
    updated_at: datetime


class ArtifactCardPreviewView(ArtifactCardView):
    """Full render data for previewing a card in the artifacts page."""

    preview_snapshot: dict


class ArtifactCardVersionView(ORMModel):
    """One immutable published snapshot of a card."""

    id: str
    card_id: str
    version: int
    release_notes: str
    published_by: str
    publish_source: str
    status: str
    created_at: datetime


class ArtifactCardShareTokenView(ORMModel):
    id: str
    artifact_card_version_id: str
    token_prefix: str
    label: str
    expires_at: datetime | None = None
    max_views: int | None = None
    view_count: int
    revoked_at: datetime | None = None
    created_at: datetime


class ArtifactCardShareTokenCreated(ArtifactCardShareTokenView):
    token: str


class ArtifactSummaryView(ArtifactView):
    version_count: int = 0


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
    created_at: datetime


class ArtifactShareTokenView(ORMModel):
    id: str
    artifact_version_id: str
    token_prefix: str
    label: str
    expires_at: datetime | None = None
    max_downloads: int | None = None
    download_count: int
    revoked_at: datetime | None = None
    created_at: datetime


class ArtifactShareTokenCreated(ArtifactShareTokenView):
    token: str
