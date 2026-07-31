from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.memory_event_models import (
    MemoryEvent,
    MemoryPayloadKey,
    MemoryProjectionCheckpoint,
    MemoryStream,
)
from app.domain.models import MemoryRecord, Workspace
from app.services.memory_commands import MemoryCommandService
from app.services.memory_event_store import MemoryEventStore


@dataclass(frozen=True, slots=True)
class ReplayValidationReport:
    stream_count: int
    event_count: int
    projection_count: int
    checkpoint_count: int
    deterministic_hash: str
    errors: tuple[str, ...]


class MemoryCutoverService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def architecture_status(self) -> dict[str, Any]:
        return {
            "write_mode": self.settings.memory_write_mode,
            "read_mode": self.settings.memory_read_mode,
            "shadow_sample_rate": self.settings.memory_shadow_sample_rate,
            "context_builder_v2": self.settings.memory_context_builder_v2,
            "task_episode_enabled": self.settings.memory_task_episode_enabled,
            "file_revision_invalidation_enabled": self.settings.memory_file_revision_invalidation_enabled,
            "agent_run_enabled": self.settings.memory_agent_run_enabled,
            "strategy_enabled": self.settings.memory_strategy_enabled,
        }

    def replay_validate(self, workspace_id: str) -> ReplayValidationReport:
        workspace = self.db.get(Workspace, workspace_id)
        if workspace is None:
            raise LookupError("workspace not found")
        streams = self.db.scalars(
            select(MemoryStream).where(MemoryStream.workspace_id == workspace_id)
        ).all()
        events = self.db.scalars(
            select(MemoryEvent)
            .where(MemoryEvent.workspace_id == workspace_id)
            .order_by(MemoryEvent.global_position)
        ).all()
        records = self.db.scalars(
            select(MemoryRecord).where(MemoryRecord.workspace_id == workspace_id)
        ).all()
        checkpoints = self.db.scalars(select(MemoryProjectionCheckpoint)).all()
        errors: list[str] = []
        per_stream: dict[str, list[int]] = {}
        for event in events:
            per_stream.setdefault(event.stream_id, []).append(event.stream_version)
        for stream in streams:
            versions = per_stream.get(stream.id, [])
            expected = list(range(1, stream.current_version + 1))
            if versions != expected:
                errors.append(f"stream_version_gap:{stream.id}")
        envelope = [
            {
                "event_id": event.event_id,
                "stream_id": event.stream_id,
                "stream_version": event.stream_version,
                "event_type": event.event_type,
                "payload_hash": event.payload_hash,
            }
            for event in events
        ]
        digest = hashlib.sha256(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return ReplayValidationReport(
            len(streams), len(events), len(records), len(checkpoints), digest, tuple(errors)
        )

    def can_enable_event_reads(self, report: ReplayValidationReport) -> bool:
        return not report.errors and report.event_count >= report.projection_count

    def export_manifest(self, workspace_id: str) -> dict[str, Any]:
        report = self.replay_validate(workspace_id)
        events = self.db.scalars(
            select(MemoryEvent).where(
                MemoryEvent.workspace_id == workspace_id,
                MemoryEvent.redacted_at.is_(None),
            )
        ).all()
        payload_keys = {
            key.id: key
            for key in self.db.scalars(
                select(MemoryPayloadKey).where(
                    MemoryPayloadKey.id.in_(
                        [item.payload_key_id for item in events if item.payload_key_id]
                    )
                )
            ).all()
        }
        return {
            "format": "learngraph-memory-events-v1",
            "workspace_id": workspace_id,
            "schema_version": 1,
            "event_envelopes": [
                {
                    "event_id": item.event_id,
                    "stream_id": item.stream_id,
                    "stream_version": item.stream_version,
                    "event_type": item.event_type,
                    "event_schema_version": item.event_schema_version,
                    "payload_hash": item.payload_hash,
                    # Ciphertext is omitted when its DEK was destroyed; projections
                    # are always rebuilt after restore rather than trusted.
                    "payload_available": bool(
                        item.payload_key_id
                        and (key := payload_keys.get(item.payload_key_id)) is not None
                        and key.status == "active"
                        and key.wrapped_dek is not None
                    ),
                }
                for item in events
            ],
            "replay_hash": report.deterministic_hash,
            "checkpoint": max((item.global_position for item in events), default=0),
        }
