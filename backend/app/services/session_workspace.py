"""Content-addressed blob store + session workspace references."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import (
    ContentBlob,
    FileRecord,
    SessionWorkspaceEntry,
    new_id,
    utc_now,
)
from app.providers.remote.sandbox import validate_agent_workspace_path
from app.providers.storage_factory import object_storage_provider
from app.repositories.audit import AuditRepository


class BlobStore:
    """Workspace-scoped content-addressed storage on top of ObjectStorage."""

    def __init__(self, db: Session, workspace_id: str, settings: Settings) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.settings = settings
        self.storage = object_storage_provider(db, workspace_id, settings)

    def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str = "application/octet-stream",
    ) -> ContentBlob:
        digest = hashlib.sha256(data).hexdigest()
        existing = self.db.scalar(
            select(ContentBlob).where(
                ContentBlob.workspace_id == self.workspace_id,
                ContentBlob.sha256 == digest,
            )
        )
        if existing is not None:
            existing.ref_count = int(existing.ref_count or 0) + 1
            if mime_type and existing.mime_type == "application/octet-stream":
                existing.mime_type = mime_type
            self.db.flush()
            return existing

        object_key = f"blobs/{self.workspace_id[:8]}/{digest[:2]}/{digest}"
        root = Path(self.settings.storage_root).expanduser().resolve()
        target = (root / object_key).resolve()
        if root not in target.parents and target != root:
            raise AppError(400, "invalid_object_key", "Blob path escapes storage root")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            target.write_bytes(data)

        blob = ContentBlob(
            id=new_id(),
            workspace_id=self.workspace_id,
            sha256=digest,
            object_key=object_key.replace("\\", "/"),
            size_bytes=len(data),
            mime_type=mime_type or "application/octet-stream",
            ref_count=1,
        )
        self.db.add(blob)
        self.db.flush()
        return blob

    def read_bytes(self, sha256: str, *, limit_bytes: int | None = None) -> bytes:
        blob = self.db.scalar(
            select(ContentBlob).where(
                ContentBlob.workspace_id == self.workspace_id,
                ContentBlob.sha256 == sha256,
            )
        )
        if blob is None:
            raise AppError(404, "blob_not_found", "Content blob was not found")
        limit = limit_bytes or self.settings.max_document_parse_bytes
        return self.storage.read_bytes(blob.object_key, limit_bytes=limit)

    def ensure_file_record(
        self,
        *,
        blob: ContentBlob,
        original_name: str,
        actor_id: str,
    ) -> FileRecord:
        """Create a downloadable FileRecord that reuses the blob's object_key.

        Dedup rules (workspace-scoped):
        - same name + same sha256 → return the existing first record
        - same sha256 + different name → keep both (new name, shared object)
        - same name + different sha256 → auto-suffix name with (1)/(2)/…
        """

        name = Path(original_name).name[:255] or f"{blob.sha256[:12]}.bin"
        # Prefer any existing logical file already bound to this blob object
        # with the same display name (exact name + hash).
        by_hash_name = self.db.scalar(
            select(FileRecord).where(
                FileRecord.workspace_id == self.workspace_id,
                FileRecord.sha256 == blob.sha256,
                FileRecord.original_name == name,
            )
        )
        if by_hash_name is not None:
            return by_hash_name
        # Same object_key + same name is also an exact duplicate.
        existing = self.db.scalar(
            select(FileRecord).where(
                FileRecord.workspace_id == self.workspace_id,
                FileRecord.object_key == blob.object_key,
                FileRecord.original_name == name,
            )
        )
        if existing is not None:
            return existing

        # Resolve name collisions when content differs (or name is reused).
        unique_name = self._unique_original_name(name, blob.sha256)
        record = FileRecord(
            id=new_id(),
            workspace_id=self.workspace_id,
            original_name=unique_name,
            object_key=blob.object_key,
            mime_type=blob.mime_type,
            size_bytes=blob.size_bytes,
            sha256=blob.sha256,
            storage_status="stored",
            parse_capability="attachment_only",
            parse_status="not_requested",
        )
        self.db.add(record)
        self.db.flush()
        return record

    def _unique_original_name(self, name: str, sha256: str) -> str:
        """Return `name` or `stem (n).ext` when a different-hash file already uses it."""

        clash = self.db.scalar(
            select(FileRecord).where(
                FileRecord.workspace_id == self.workspace_id,
                FileRecord.original_name == name,
            )
        )
        if clash is None:
            return name
        # Same content under the same name is handled by the caller.
        if clash.sha256 == sha256:
            return name
        path = Path(name)
        stem = path.stem or "file"
        suffix = path.suffix
        for index in range(1, 10_000):
            candidate = f"{stem} ({index}){suffix}"
            if len(candidate) > 255:
                candidate = f"{stem[: max(1, 240 - len(suffix) - len(str(index)) - 3)]} ({index}){suffix}"
            taken = self.db.scalar(
                select(FileRecord).where(
                    FileRecord.workspace_id == self.workspace_id,
                    FileRecord.original_name == candidate,
                )
            )
            if taken is None:
                return candidate
            if taken.sha256 == sha256:
                return candidate
        return f"{stem}-{sha256[:8]}{suffix}"[:255]


class SessionWorkspaceService:
    """Session-scoped logical workspace paths over content blobs."""

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
        self.audit = AuditRepository(db, workspace_id)

    def list_entries(self, chat_session_id: str) -> list[SessionWorkspaceEntry]:
        return list(
            self.db.scalars(
                select(SessionWorkspaceEntry)
                .where(
                    SessionWorkspaceEntry.workspace_id == self.workspace_id,
                    SessionWorkspaceEntry.owner_user_id == self.actor_id,
                    SessionWorkspaceEntry.chat_session_id == chat_session_id,
                )
                .order_by(SessionWorkspaceEntry.path)
            ).all()
        )

    def put_bytes(
        self,
        *,
        chat_session_id: str,
        path: str,
        data: bytes,
        role: str = "work",
        sandbox_session_id: str | None = None,
        source: str = "agent",
        mime_type: str | None = None,
        publish_file: bool = False,
    ) -> dict[str, Any]:
        safe_path = validate_agent_workspace_path(path)
        if role not in {"input", "work", "output"}:
            raise AppError(422, "invalid_workspace_role", "Workspace role must be input, work, or output")
        guessed, _ = mimetypes.guess_type(safe_path)
        mime = mime_type or guessed or "application/octet-stream"
        blob = self.blobs.put_bytes(data, mime_type=mime)
        file_id: str | None = None
        if publish_file or role == "output" or safe_path.startswith("outputs/"):
            file_record = self.blobs.ensure_file_record(
                blob=blob, original_name=PurePosixPath(safe_path).name, actor_id=self.actor_id
            )
            file_id = file_record.id
            role = "output"

        entry = self.db.scalar(
            select(SessionWorkspaceEntry).where(
                SessionWorkspaceEntry.workspace_id == self.workspace_id,
                SessionWorkspaceEntry.owner_user_id == self.actor_id,
                SessionWorkspaceEntry.chat_session_id == chat_session_id,
                SessionWorkspaceEntry.path == safe_path,
            )
        )
        if entry is None:
            entry = SessionWorkspaceEntry(
                id=new_id(),
                workspace_id=self.workspace_id,
                owner_user_id=self.actor_id,
                chat_session_id=chat_session_id,
                sandbox_session_id=sandbox_session_id,
                path=safe_path,
                role=role,
                blob_sha256=blob.sha256,
                file_id=file_id,
                size_bytes=blob.size_bytes,
                mime_type=mime,
                source=source,
            )
            self.db.add(entry)
        else:
            entry.sandbox_session_id = sandbox_session_id or entry.sandbox_session_id
            entry.role = role
            entry.blob_sha256 = blob.sha256
            entry.file_id = file_id or entry.file_id
            entry.size_bytes = blob.size_bytes
            entry.mime_type = mime
            entry.source = source
            entry.updated_at = utc_now()
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.workspace.entry_put",
            resource_type="session_workspace_entry",
            resource_id=entry.id,
            details={
                "path": safe_path,
                "sha256": blob.sha256,
                "role": role,
                "file_id": file_id,
                "size_bytes": blob.size_bytes,
            },
        )
        self.db.commit()
        self.db.refresh(entry)
        return self._view(entry)

    def get_entry(self, chat_session_id: str, path: str) -> SessionWorkspaceEntry:
        safe_path = validate_agent_workspace_path(path)
        entry = self.db.scalar(
            select(SessionWorkspaceEntry).where(
                SessionWorkspaceEntry.workspace_id == self.workspace_id,
                SessionWorkspaceEntry.owner_user_id == self.actor_id,
                SessionWorkspaceEntry.chat_session_id == chat_session_id,
                SessionWorkspaceEntry.path == safe_path,
            )
        )
        if entry is None:
            raise AppError(404, "session_workspace_entry_not_found", "Workspace path was not found")
        return entry

    def materialize_bytes(self, chat_session_id: str, path: str) -> bytes:
        entry = self.get_entry(chat_session_id, path)
        return self.blobs.read_bytes(entry.blob_sha256)

    def link_file_record(
        self,
        *,
        chat_session_id: str,
        file: FileRecord,
        path: str | None = None,
        role: str = "input",
        source: str = "chat_attachment",
        sandbox_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Register an existing FileRecord under the session workspace tree.

        Used when chat attachments (including unparseable legacy Office bytes)
        must be visible to Agent sandbox tools without re-uploading. Content is
        content-addressed into BlobStore and referenced by path.

        Does not commit; callers that own a broader transaction (e.g. ChatService)
        should commit after seeding.
        """

        name = Path(file.original_name).name[:200] or f"{file.sha256[:12]}.bin"
        if role not in {"input", "work", "output"}:
            raise AppError(422, "invalid_workspace_role", "Workspace role must be input, work, or output")
        mime = file.mime_type or "application/octet-stream"
        blob = self.db.scalar(
            select(ContentBlob).where(
                ContentBlob.workspace_id == self.workspace_id,
                ContentBlob.sha256 == file.sha256,
            )
        )
        if blob is None:
            # Chat attachments already have a durable FileRecord/object key.
            # Register that object directly instead of reading and duplicating
            # the complete file through backend memory and workspace storage.
            blob = ContentBlob(
                id=new_id(),
                workspace_id=self.workspace_id,
                sha256=file.sha256,
                object_key=file.object_key,
                size_bytes=file.size_bytes,
                mime_type=mime,
                ref_count=1,
            )
            self.db.add(blob)
            self.db.flush()
        else:
            blob.ref_count = int(blob.ref_count or 0) + 1
            self.db.flush()
        # Prefer the caller's FileRecord id so chat attachments stay linked.
        file_id = file.id if file.sha256 == blob.sha256 else None
        if file_id is None:
            linked = self.blobs.ensure_file_record(
                blob=blob, original_name=name, actor_id=self.actor_id
            )
            file_id = linked.id

        # Prefer an existing entry already bound to this file_id so re-attaches
        # are idempotent; otherwise choose a stable inputs/ path.
        entry = self.db.scalar(
            select(SessionWorkspaceEntry).where(
                SessionWorkspaceEntry.workspace_id == self.workspace_id,
                SessionWorkspaceEntry.owner_user_id == self.actor_id,
                SessionWorkspaceEntry.chat_session_id == chat_session_id,
                SessionWorkspaceEntry.file_id == file_id,
            )
        )
        if entry is not None:
            safe_path = entry.path
        else:
            preferred = path or f"inputs/{name}"
            safe_path = validate_agent_workspace_path(preferred)
            occupied = self.db.scalar(
                select(SessionWorkspaceEntry).where(
                    SessionWorkspaceEntry.workspace_id == self.workspace_id,
                    SessionWorkspaceEntry.owner_user_id == self.actor_id,
                    SessionWorkspaceEntry.chat_session_id == chat_session_id,
                    SessionWorkspaceEntry.path == safe_path,
                )
            )
            if occupied is not None and occupied.file_id != file_id:
                stem = PurePosixPath(safe_path).stem
                suffix = PurePosixPath(safe_path).suffix
                safe_path = validate_agent_workspace_path(
                    f"inputs/{stem}-{file.id[:8]}{suffix}"
                )
                entry = None
            else:
                entry = occupied

        if entry is None:
            entry = SessionWorkspaceEntry(
                id=new_id(),
                workspace_id=self.workspace_id,
                owner_user_id=self.actor_id,
                chat_session_id=chat_session_id,
                sandbox_session_id=sandbox_session_id,
                path=safe_path,
                role=role,
                blob_sha256=blob.sha256,
                file_id=file_id,
                size_bytes=blob.size_bytes,
                mime_type=mime,
                source=source,
            )
            self.db.add(entry)
        else:
            entry.sandbox_session_id = sandbox_session_id or entry.sandbox_session_id
            entry.role = role
            entry.blob_sha256 = blob.sha256
            entry.file_id = file_id
            entry.size_bytes = blob.size_bytes
            entry.mime_type = mime
            entry.source = source
            entry.updated_at = utc_now()
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.workspace.entry_linked",
            resource_type="session_workspace_entry",
            resource_id=entry.id,
            details={
                "path": entry.path,
                "sha256": blob.sha256,
                "role": role,
                "file_id": file_id,
                "size_bytes": blob.size_bytes,
                "source": source,
            },
        )
        return self._view(entry)

    def publish_path(
        self,
        *,
        chat_session_id: str,
        path: str,
        data: bytes,
        sandbox_session_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        safe = validate_agent_workspace_path(path)
        # Normalize published artifacts under outputs/
        out_path = safe if safe.startswith("outputs/") else f"outputs/{PurePosixPath(safe).name}"
        view = self.put_bytes(
            chat_session_id=chat_session_id,
            path=out_path,
            data=data,
            role="output",
            sandbox_session_id=sandbox_session_id,
            source="agent_publish",
            publish_file=True,
        )
        return {
            **view,
            "title": title or PurePosixPath(out_path).name,
            "download_path": f"/api/v1/files/{view['file_id']}/content" if view.get("file_id") else None,
            "part": {
                "type": "sandbox_artifact",
                "status": "completed",
                "data": {
                    "kind": "file",
                    "title": title or PurePosixPath(out_path).name,
                    "path": out_path,
                    "file_id": view.get("file_id"),
                    "size_bytes": view.get("size_bytes"),
                    "sha256": view.get("blob_sha256"),
                    "mime_type": view.get("mime_type"),
                    "sandbox_session_id": sandbox_session_id,
                    "chat_session_id": chat_session_id,
                },
            },
        }

    def delete_entry(self, chat_session_id: str, path: str) -> None:
        entry = self.get_entry(chat_session_id, path)
        self.db.delete(entry)
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.workspace.entry_deleted",
            resource_type="session_workspace_entry",
            resource_id=entry.id,
            details={"path": entry.path, "sha256": entry.blob_sha256},
        )
        self.db.commit()

    @staticmethod
    def _view(entry: SessionWorkspaceEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "chat_session_id": entry.chat_session_id,
            "sandbox_session_id": entry.sandbox_session_id,
            "path": entry.path,
            "role": entry.role,
            "blob_sha256": entry.blob_sha256,
            "file_id": entry.file_id,
            "size_bytes": entry.size_bytes,
            "mime_type": entry.mime_type,
            "source": entry.source,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
        }

    def list_views(self, chat_session_id: str) -> list[dict[str, Any]]:
        return [self._view(item) for item in self.list_entries(chat_session_id)]
