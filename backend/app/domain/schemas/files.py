from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.schemas.common import ORMModel
from app.domain.schemas.workflow import DeleteImpact, ImpactItem


class FileView(ORMModel):
    id: str
    workspace_id: str
    original_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    storage_status: str
    parse_capability: str
    parse_status: str
    parser_name: str | None
    parser_version: str | None
    error_message: str | None
    created_at: datetime


class FileStorageSummary(BaseModel):
    """Workspace-level occupancy for the materials library UI."""

    file_count: int
    total_bytes: int


class FileBatchSelection(BaseModel):
    """Selected files for batch delete preflight or confirmation."""

    file_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("file_ids")
    @classmethod
    def normalize_file_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            file_id = value.strip()
            if not file_id or len(file_id) > 36:
                raise ValueError("Each file ID must contain 1 to 36 characters")
            if file_id not in seen:
                normalized.append(file_id)
                seen.add(file_id)
        if not normalized:
            raise ValueError("At least one file ID is required")
        return normalized


class FileBatchDeleteConfirm(FileBatchSelection):
    confirmation_text: str = Field(min_length=1, max_length=128)


class FileBatchDeleteImpact(DeleteImpact):
    file_ids: list[str]


class FileBatchDeleteResponse(BaseModel):
    status: Literal["deleted"] = "deleted"
    deleted_file_ids: list[str]
    deleted_count: int
    impacts: list[ImpactItem]


class AudioTranscriptionCreate(BaseModel):
    provider_id: str | None = Field(default=None, max_length=36)
    model_id: str | None = Field(default=None, max_length=160)
    language: str | None = Field(default=None, min_length=2, max_length=32)


class AudioTranscriptionView(ORMModel):
    id: str
    workspace_id: str
    file_id: str
    provider_id: str
    model_id: str
    language: str | None
    status: str
    transcript: str
    duration_seconds: float | None
    provider_request_id: str | None
    provider_trace: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class FileTextChunkView(ORMModel):
    id: str
    workspace_id: str
    file_id: str
    document_revision_id: str | None
    ordinal: int
    locator: str
    locator_json: dict[str, Any]
    section_path: list[str]
    token_count: int
    content: str
    content_hash: str
    created_at: datetime


FileReferenceTarget = Literal[
    "project",
    "goal",
    "graph",
    "node",
    "session",
    "message",
    "evidence",
    "source",
    "source_link",
]


class FileReferenceCreate(BaseModel):
    target_type: FileReferenceTarget
    target_id: str = Field(min_length=1, max_length=36)
    relation: str = Field(default="reference", min_length=1, max_length=40)
    locator: str = Field(default="", max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileReferenceView(ORMModel):
    id: str
    workspace_id: str
    file_id: str
    target_type: str
    target_id: str
    relation: str
    locator: str
    metadata_json: dict[str, Any]
    created_at: datetime


class FileParserCapabilityView(BaseModel):
    capability_id: str
    mode: Literal["built_in", "optional", "isolated"]
    extensions: list[str]
    available: bool
    parser_name: str | None
    reason: str


class DocumentRevisionView(ORMModel):
    id: str
    workspace_id: str
    file_id: str
    revision_no: int
    source_sha256: str
    size_bytes: int
    mime_detected: str
    processor_id: str | None
    processor_version: str | None
    config_hash: str
    status: str
    quality_report: dict[str, Any]
    artifact_manifest: dict[str, Any]
    created_by: str
    completed_at: datetime | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class DocumentJobCreate(BaseModel):
    job_type: Literal["parse_index"] = "parse_index"


class DocumentJobView(ORMModel):
    id: str
    workspace_id: str
    file_id: str
    document_revision_id: str | None
    job_type: str
    status: str
    stage: str
    progress: int
    parameters: dict[str, Any]
    created_by: str
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DocumentJobEventView(ORMModel):
    id: str
    workspace_id: str
    job_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class DocumentCollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=2_000)
    file_ids: list[str] = Field(default_factory=list, max_length=100)
    project_id: str | None = Field(default=None, max_length=36)
    goal_id: str | None = Field(default=None, max_length=36)
    graph_id: str | None = Field(default=None, max_length=36)


class DocumentCollectionItemView(ORMModel):
    id: str
    workspace_id: str
    collection_id: str
    file_id: str
    document_revision_id: str | None
    added_by: str
    created_at: datetime


class DocumentCollectionView(ORMModel):
    id: str
    workspace_id: str
    name: str
    description: str
    project_id: str | None
    goal_id: str | None
    graph_id: str | None
    created_by: str
    items: list[DocumentCollectionItemView] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


DocumentQueryScope = Literal["selection", "page", "section", "file", "files"]


class DocumentQueryPreviewRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    file_ids: list[str] = Field(default_factory=list, max_length=100)
    collection_ids: list[str] = Field(default_factory=list, max_length=20)
    scope: DocumentQueryScope = "files"
    locator: dict[str, Any] = Field(default_factory=dict)
    selected_text: str = Field(default="", max_length=50_000)
    selected_text_hash: str | None = Field(default=None, min_length=64, max_length=64)
    max_results: int = Field(default=8, ge=1, le=24)

    @model_validator(mode="after")
    def validate_scope_locator(self):
        if not self.file_ids and not self.collection_ids:
            raise ValueError("at least one file_id or collection_id is required")
        if self.scope == "file" and len(set(self.file_ids)) != 1:
            raise ValueError("file scope requires exactly one file_id")
        if self.scope == "page" and not isinstance(self.locator.get("page"), int):
            raise ValueError("page scope requires locator.page")
        if self.scope == "section" and not (
            self.locator.get("chunk_id") or self.locator.get("section_path")
        ):
            raise ValueError(
                "section scope requires locator.chunk_id or locator.section_path"
            )
        if self.scope == "selection" and not self.selected_text:
            raise ValueError("selection scope requires selected_text")
        return self


class DocumentQueryHitView(BaseModel):
    rank: int
    score: float
    chunk_id: str
    file_id: str
    document_revision_id: str | None
    filename: str
    locator: str
    locator_json: dict[str, Any]
    section_path: list[str]
    quote: str
    content_hash: str


DocumentSelectionStatus = Literal["verified", "unverified_degraded", "none"]


class DocumentQueryPreviewView(BaseModel):
    trace_id: str
    strategy: str
    scope: DocumentQueryScope
    hits: list[DocumentQueryHitView]
    warnings: list[str] = Field(default_factory=list)
    selection_status: DocumentSelectionStatus = "none"
