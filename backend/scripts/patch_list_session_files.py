"""Patch agent_runtime._list_session_files to merge session_workspace_entries.

The Agent tool previously listed only FileReference + ImageGenerationTask rows,
so download_external_image results (which live in session_workspace_entries
without a FileReference) were invisible. The body now delegates to
app.services.session_files.collect_session_files, which merges all three
sources; this method keeps only the cross-session audit, truncation and hint.
"""
from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "app" / "services" / "agent_runtime.py"

MARKER = """    def _list_session_files(
        self, arguments: dict[str, Any], *, chat_session_id: str
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        raw_session = arguments.get("session_id")
        target_session_id = (
            raw_session.strip()
            if isinstance(raw_session, str) and raw_session.strip()
            else chat_session_id
        )
        self._require_workspace_session(target_session_id)
        cross_session = target_session_id != chat_session_id
        db = self.extensions.db

        entries: dict[str, dict[str, Any]] = {}
        attachment_rows = db.execute(
            select(FileRecord, Message, FileReference)
            .join(FileReference, FileReference.file_id == FileRecord.id)
            .join(Message, FileReference.target_id == Message.id)
            .where(
                FileReference.workspace_id == self.workspace_id,
                FileReference.target_type == "message",
                Message.session_id == target_session_id,
                FileRecord.workspace_id == self.workspace_id,
            )
            .order_by(Message.created_at)
        ).all()
        for file, message, reference in attachment_rows:
            entries.setdefault(
                file.id,
                {
                    "file_id": file.id,
                    "filename": file.original_name,
                    "mime_type": file.mime_type,
                    "size_bytes": file.size_bytes,
                    # ImageGenerationService also records generated files as
                    # message references; keep their origin distinguishable
                    # from files the user uploaded.
                    "origin": (
                        "generated_image"
                        if reference.relation == "generated_image"
                        else "user_attachment"
                    ),
                    "relation": reference.relation,
                    "message_id": message.id,
                    "is_image": is_image_attachment(file),
                    "storage_status": file.storage_status,
                    "created_at": (
                        file.created_at.isoformat() if file.created_at else None
                    ),
                },
            )
        generated_rows = db.execute(
            select(FileRecord, ImageGenerationTask)
            .join(ImageGenerationTask, ImageGenerationTask.file_id == FileRecord.id)
            .where(
                ImageGenerationTask.workspace_id == self.workspace_id,
                ImageGenerationTask.session_id == target_session_id,
                ImageGenerationTask.status == "completed",
            )
            .order_by(ImageGenerationTask.created_at)
        ).all()
        for file, task in generated_rows:
            entries.setdefault(
                file.id,
                {
                    "file_id": file.id,
                    "filename": file.original_name,
                    "mime_type": file.mime_type,
                    "size_bytes": file.size_bytes,
                    "origin": "generated_image",
                    "message_id": task.message_id,
                    "prompt_summary": task.prompt_summary or None,
                    "is_image": True,
                    "storage_status": file.storage_status,
                    "created_at": (
                        task.created_at.isoformat() if task.created_at else None
                    ),
                },
            )
        listed = sorted(entries.values(), key=lambda item: item["created_at"] or "")
"""

REPLACEMENT = """    def _list_session_files(
        self, arguments: dict[str, Any], *, chat_session_id: str
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        raw_session = arguments.get("session_id")
        target_session_id = (
            raw_session.strip()
            if isinstance(raw_session, str) and raw_session.strip()
            else chat_session_id
        )
        self._require_workspace_session(target_session_id)
        cross_session = target_session_id != chat_session_id
        db = self.extensions.db

        # Merge FileReference + ImageGenerationTask + session_workspace_entries
        # so download_external_image results (which never create a
        # FileReference) are visible exactly like sandbox_list_files sees them.
        from app.services.session_files import collect_session_files

        listed = collect_session_files(
            db,
            workspace_id=self.workspace_id,
            session_id=target_session_id,
        )
"""


def apply() -> None:
    text = TARGET.read_text(encoding="utf-8")
    count = text.count(MARKER)
    if count != 1:
        raise SystemExit(
            f"[FAIL] _list_session_files marker found {count} times (expected 1)"
        )
    text = text.replace(MARKER, REPLACEMENT)
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print("[OK] patched _list_session_files -> collect_session_files")


if __name__ == "__main__":
    apply()
