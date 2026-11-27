"""Turn-level sandbox file checkpoint capture, preview, and conflict-checked rewind."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import logging
import threading
from typing import Any, Iterator
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import SessionWorkspaceEntry
from app.domain.sandbox_checkpoint_models import SandboxFileCheckpoint
from app.providers.remote.sandbox import validate_agent_workspace_path
from app.repositories.audit import AuditRepository
from app.repositories.domain import SessionRepository
from app.services.session_workspace import BlobStore, SessionWorkspaceService

logger = logging.getLogger(__name__)

_turn_state = threading.local()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def checkpoint_turn_context(turn_id: str | None) -> Iterator[None]:
    """Bind the active assistant turn id so sandbox file mutations capture pre-images."""
    stack: list[str | None] = getattr(_turn_state, "stack", None)
    if stack is None:
        stack = []
        _turn_state.stack = stack
    stack.append(turn_id)
    try:
        yield
    finally:
        stack.pop()


def current_checkpoint_turn_id() -> str | None:
    stack: list[str | None] | None = getattr(_turn_state, "stack", None)
    if not stack:
        return None
    for value in reversed(stack):
        if value:
            return value
    return None


class SandboxCheckpointService:
    """Record turn pre-images in BlobStore and safely rewind workspace files."""

    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.blobs = BlobStore(db, workspace_id, settings)
        self.workspace_files = SessionWorkspaceService(db, workspace_id, actor_id, settings)
        self.sessions = SessionRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    def _current_entry(self, chat_session_id: str, path: str) -> SessionWorkspaceEntry | None:
        return self.db.scalar(
            select(SessionWorkspaceEntry).where(
                SessionWorkspaceEntry.workspace_id == self.workspace_id,
                SessionWorkspaceEntry.owner_user_id == self.actor_id,
                SessionWorkspaceEntry.chat_session_id == chat_session_id,
                SessionWorkspaceEntry.path == path,
            )
        )

    def capture_before_state(self, chat_session_id: str, path: str) -> tuple[bool, str | None, int]:
        """Snapshot the current workspace entry hash/size before a mutation occurs."""
        normalized = validate_agent_workspace_path(path)
        entry = self._current_entry(chat_session_id, normalized)
        if entry is None:
            return False, None, 0
        return True, entry.blob_sha256, int(entry.size_bytes or 0)

    def record_mutation(
        self,
        *,
        chat_session_id: str,
        turn_id: str,
        path: str,
        existed_before: bool,
        pre_blob_sha256: str | None,
        pre_size_bytes: int,
        post_blob_sha256: str | None,
        post_size_bytes: int,
        pre_bytes: bytes | None = None,
        post_bytes: bytes | None = None,
    ) -> SandboxFileCheckpoint:
        normalized = validate_agent_workspace_path(path)
        if existed_before and pre_blob_sha256 is None and pre_bytes is not None:
            blob = self.blobs.put_bytes(pre_bytes)
            pre_blob_sha256 = blob.sha256
            pre_size_bytes = len(pre_bytes)
        if post_blob_sha256 is None and post_bytes is not None:
            blob = self.blobs.put_bytes(post_bytes)
            post_blob_sha256 = blob.sha256
            post_size_bytes = len(post_bytes)

        existing = self.db.scalar(
            select(SandboxFileCheckpoint).where(
                SandboxFileCheckpoint.workspace_id == self.workspace_id,
                SandboxFileCheckpoint.chat_session_id == chat_session_id,
                SandboxFileCheckpoint.turn_id == turn_id,
                SandboxFileCheckpoint.path == normalized,
            )
        )
        now = _utc_now()
        if existing is not None:
            existing.post_blob_sha256 = post_blob_sha256
            existing.post_size_bytes = post_size_bytes
            existing.updated_at = now
            self.db.flush()
            return existing

        row = SandboxFileCheckpoint(
            id=str(uuid4()),
            workspace_id=self.workspace_id,
            chat_session_id=chat_session_id,
            turn_id=turn_id,
            path=normalized,
            existed_before=existed_before,
            pre_blob_sha256=pre_blob_sha256,
            pre_size_bytes=pre_size_bytes,
            post_blob_sha256=post_blob_sha256,
            post_size_bytes=post_size_bytes,
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def list_turns(self, chat_session_id: str) -> list[dict[str, Any]]:
        self.sessions.require(chat_session_id, "session")
        rows = list(
            self.db.scalars(
                select(SandboxFileCheckpoint)
                .where(
                    SandboxFileCheckpoint.workspace_id == self.workspace_id,
                    SandboxFileCheckpoint.chat_session_id == chat_session_id,
                )
                .order_by(SandboxFileCheckpoint.created_at.asc())
            ).all()
        )
        by_turn: dict[str, dict[str, Any]] = {}
        for row in rows:
            bucket = by_turn.get(row.turn_id)
            if bucket is None:
                bucket = {
                    "turn_id": row.turn_id,
                    "paths": [],
                    "created_at": row.created_at,
                }
                by_turn[row.turn_id] = bucket
            bucket["paths"].append(row.path)
        return [
            {
                "turn_id": item["turn_id"],
                "file_count": len(item["paths"]),
                "paths": sorted(set(item["paths"])),
                "created_at": item["created_at"],
            }
            for item in by_turn.values()
        ]

    def _scope_checkpoints(
        self,
        chat_session_id: str,
        turn_id: str,
        *,
        include_subsequent_turns: bool,
    ) -> list[SandboxFileCheckpoint]:
        self.sessions.require(chat_session_id, "session")
        target_rows = list(
            self.db.scalars(
                select(SandboxFileCheckpoint)
                .where(
                    SandboxFileCheckpoint.workspace_id == self.workspace_id,
                    SandboxFileCheckpoint.chat_session_id == chat_session_id,
                    SandboxFileCheckpoint.turn_id == turn_id,
                )
                .order_by(SandboxFileCheckpoint.created_at.asc())
            ).all()
        )
        if not target_rows:
            raise AppError(
                404,
                "sandbox_checkpoint_not_found",
                "该轮次未记录可回退的沙箱文件变更。",
            )
        if not include_subsequent_turns:
            return target_rows
        anchor_time = min(row.created_at for row in target_rows)
        return list(
            self.db.scalars(
                select(SandboxFileCheckpoint)
                .where(
                    SandboxFileCheckpoint.workspace_id == self.workspace_id,
                    SandboxFileCheckpoint.chat_session_id == chat_session_id,
                    SandboxFileCheckpoint.created_at >= anchor_time,
                )
                .order_by(SandboxFileCheckpoint.created_at.asc(), SandboxFileCheckpoint.id.asc())
            ).all()
        )

    def _plan_entries(
        self,
        chat_session_id: str,
        rows: list[SandboxFileCheckpoint],
        *,
        expected_current_hashes: dict[str, str | None] | None = None,
    ) -> list[dict[str, Any]]:
        # Earliest pre-image per path is the restore target; latest post-image is the expected live state.
        earliest: dict[str, SandboxFileCheckpoint] = {}
        latest: dict[str, SandboxFileCheckpoint] = {}
        for row in rows:
            if row.path not in earliest:
                earliest[row.path] = row
            latest[row.path] = row

        planned: list[dict[str, Any]] = []
        for path in sorted(earliest.keys()):
            first = earliest[path]
            last = latest[path]
            current = self._current_entry(chat_session_id, path)
            current_sha = current.blob_sha256 if current is not None else None
            expected_sha = last.post_blob_sha256

            conflict = False
            conflict_reason: str | None = None
            if expected_current_hashes is not None and path in expected_current_hashes:
                preview_sha = expected_current_hashes[path]
                if current_sha != preview_sha:
                    conflict = True
                    conflict_reason = "文件在预览之后发生了新的修改，为避免覆盖已跳过。"
            if not conflict and current_sha != expected_sha:
                conflict = True
                conflict_reason = "文件当前内容与记录的轮次终态不一致，存在外部或后续修改，已跳过。"

            if first.existed_before:
                action = "noop" if current_sha == first.pre_blob_sha256 else "restore"
            else:
                action = "noop" if current_sha is None else "delete"

            planned.append(
                {
                    "path": path,
                    "action": action,
                    "existed_before": first.existed_before,
                    "pre_sha256": first.pre_blob_sha256,
                    "expected_sha256": expected_sha,
                    "current_sha256": current_sha,
                    "conflict": conflict,
                    "conflict_reason": conflict_reason,
                }
            )
        return planned

    def preview_rewind(
        self,
        chat_session_id: str,
        turn_id: str,
        *,
        include_subsequent_turns: bool = True,
    ) -> dict[str, Any]:
        rows = self._scope_checkpoints(
            chat_session_id,
            turn_id,
            include_subsequent_turns=include_subsequent_turns,
        )
        planned = self._plan_entries(chat_session_id, rows)
        restore_count = sum(1 for item in planned if not item["conflict"] and item["action"] == "restore")
        delete_count = sum(1 for item in planned if not item["conflict"] and item["action"] == "delete")
        conflict_count = sum(1 for item in planned if item["conflict"])
        noop_count = sum(1 for item in planned if not item["conflict"] and item["action"] == "noop")
        return {
            "chat_session_id": chat_session_id,
            "turn_id": turn_id,
            "restore_count": restore_count,
            "delete_count": delete_count,
            "conflict_count": conflict_count,
            "noop_count": noop_count,
            "expected_current_hashes": {
                item["path"]: item["current_sha256"] for item in planned
            },
            "items": planned,
        }

    def execute_rewind(
        self,
        chat_session_id: str,
        turn_id: str,
        *,
        expected_current_hashes: dict[str, str | None] | None = None,
        include_subsequent_turns: bool = True,
        sandbox_service: Any | None = None,
    ) -> dict[str, Any]:
        rows = self._scope_checkpoints(
            chat_session_id,
            turn_id,
            include_subsequent_turns=include_subsequent_turns,
        )
        planned = self._plan_entries(
            chat_session_id,
            rows,
            expected_current_hashes=expected_current_hashes,
        )
        restored_paths: list[str] = []
        deleted_paths: list[str] = []
        skipped_paths: list[str] = []
        conflicts: list[dict[str, Any]] = []

        for item in planned:
            path = item["path"]
            if item["conflict"]:
                conflicts.append(item)
                skipped_paths.append(path)
                continue
            if item["action"] == "noop":
                skipped_paths.append(path)
                continue
            if item["action"] == "restore":
                pre_sha = item["pre_sha256"]
                if not pre_sha:
                    conflicts.append({**item, "conflict": True, "conflict_reason": "缺少可恢复的前像快照。"})
                    skipped_paths.append(path)
                    continue
                data = self.blobs.read_bytes(pre_sha)
                role = "output" if path.startswith("outputs/") else "work"
                self.workspace_files.put_bytes(
                    chat_session_id=chat_session_id,
                    path=path,
                    data=data,
                    role=role,
                    source="checkpoint_rewind",
                    publish_file=path.startswith("outputs/"),
                )
                self._sync_live_sandbox_file(sandbox_service, chat_session_id, path, data)
                restored_paths.append(path)
            elif item["action"] == "delete":
                try:
                    self.workspace_files.delete_entry(chat_session_id, path)
                except AppError as exc:
                    if exc.code != "session_workspace_entry_not_found":
                        raise
                self._sync_live_sandbox_file(sandbox_service, chat_session_id, path, None)
                deleted_paths.append(path)

        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.checkpoint.rewound",
            resource_type="chat_session",
            resource_id=chat_session_id,
            details={
                "turn_id": turn_id,
                "restored_count": len(restored_paths),
                "deleted_count": len(deleted_paths),
                "conflict_count": len(conflicts),
            },
        )
        self.db.commit()
        return {
            "chat_session_id": chat_session_id,
            "turn_id": turn_id,
            "restored_paths": restored_paths,
            "deleted_paths": deleted_paths,
            "skipped_paths": skipped_paths,
            "conflicts": conflicts,
        }

    def _sync_live_sandbox_file(
        self,
        sandbox_service: Any | None,
        chat_session_id: str,
        path: str,
        data: bytes | None,
    ) -> None:
        if sandbox_service is None:
            return
        try:
            session = sandbox_service._resolve_session(None, chat_session_id)
            if not getattr(session, "backend_session_ref", None):
                return
            handle = sandbox_service._ensure_backend_session(session)
            container_path = (
                sandbox_service._container_path(chat_session_id, path)
                if sandbox_service._pooling_enabled(session)
                else path
            )
            backend = sandbox_service._runtime_backend(session)
            if data is None:
                backend.delete_agent_file(handle, container_path)
            else:
                backend.write_agent_file(handle, container_path, data)
        except Exception:
            logger.debug("Best-effort live sandbox sync skipped during rewind for %s", path, exc_info=True)
