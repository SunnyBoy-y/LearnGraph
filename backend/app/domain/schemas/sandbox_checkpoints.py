from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CheckpointTurnSummaryView(BaseModel):
    turn_id: str
    file_count: int
    paths: list[str]
    created_at: datetime


class CheckpointTurnListResponse(BaseModel):
    items: list[CheckpointTurnSummaryView]


class CheckpointRewindItemView(BaseModel):
    path: str
    action: Literal["restore", "delete", "noop"]
    existed_before: bool
    pre_sha256: str | None = None
    expected_sha256: str | None = None
    current_sha256: str | None = None
    conflict: bool = False
    conflict_reason: str | None = None


class CheckpointRewindPreviewView(BaseModel):
    chat_session_id: str
    turn_id: str
    restore_count: int
    delete_count: int
    conflict_count: int
    noop_count: int
    expected_current_hashes: dict[str, str | None] = Field(default_factory=dict)
    items: list[CheckpointRewindItemView] = Field(default_factory=list)


class CheckpointRewindRequest(BaseModel):
    expected_current_hashes: dict[str, str | None] | None = None
    include_subsequent_turns: bool = True


class CheckpointRewindResultView(BaseModel):
    chat_session_id: str
    turn_id: str
    restored_paths: list[str] = Field(default_factory=list)
    deleted_paths: list[str] = Field(default_factory=list)
    skipped_paths: list[str] = Field(default_factory=list)
    conflicts: list[CheckpointRewindItemView] = Field(default_factory=list)
