from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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


class ArtifactCardShareTokenRevealed(BaseModel):
    """Raw token of an existing share, returned only by an explicit reveal call."""

    token: str


class ArtifactCardShareManagementView(ArtifactCardShareTokenView):
    """Workspace-wide share row enriched for the share management page."""

    card_id: str
    card_title: str
    card_version: int
    card_type: str
    # False for rows created before the raw token was stored encrypted; the
    # management view keeps 「复制链接」 disabled for those.
    share_token_available: bool = False


class ArtifactCardShareBatchAction(BaseModel):
    """Batch revoke / purge request from the share management view."""

    token_ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["revoke", "purge"]

    @field_validator("token_ids")
    @classmethod
    def normalize_token_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            token_id = value.strip()
            if not token_id or len(token_id) > 36:
                raise ValueError("Each share token ID must contain 1 to 36 characters")
            if token_id not in seen:
                normalized.append(token_id)
                seen.add(token_id)
        if not normalized:
            raise ValueError("At least one share token ID is required")
        return normalized


class ArtifactCardShareBatchSkipped(BaseModel):
    """One selected share the batch left untouched, with the reason why."""

    id: str
    reason: Literal["not_found", "already_revoked", "still_active"]


class ArtifactCardShareBatchResult(BaseModel):
    """Outcome of a batch action: what changed, what was skipped."""

    action: Literal["revoke", "purge"]
    requested_count: int
    affected_count: int
    skipped: list[ArtifactCardShareBatchSkipped] = []


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
