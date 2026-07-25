from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ImageGenerationTaskView(BaseModel):
    id: str
    workspace_id: str
    session_id: str
    message_id: str
    message_version_id: str
    source_message_id: str
    provider_id: str
    model_id: str
    prompt_summary: str
    status: str
    progress_mode: str
    partial_index: int | None
    file_id: str | None
    provider_trace: dict[str, Any]
    error_code: str | None
    error_message: str | None
    cancel_requested: bool
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
