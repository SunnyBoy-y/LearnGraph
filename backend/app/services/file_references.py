from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import (
    ChatSession,
    Evidence,
    FileReference,
    FileTextChunk,
    Goal,
    Graph,
    GraphNode,
    Message,
    Project,
    SourceLink,
    SourceRecord,
)
from app.domain.schemas.files import FileReferenceCreate
from app.repositories.domain import FileReferenceRepository, FileRepository


TARGET_MODELS = {
    "project": Project,
    "goal": Goal,
    "graph": Graph,
    "node": GraphNode,
    "session": ChatSession,
    "message": Message,
    "evidence": Evidence,
    "source": SourceRecord,
    "source_link": SourceLink,
}


class FileReferenceService:
    """Validate and persist polymorphic file relationships within one workspace."""

    def __init__(self, db: Session, workspace_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.files = FileRepository(db, workspace_id)
        self.references = FileReferenceRepository(db, workspace_id)

    def list_for_file(self, file_id: str) -> list[FileReference]:
        self.files.require(file_id, "file")
        return list(
            self.db.scalars(
                self.references.query()
                .where(FileReference.file_id == file_id)
                .order_by(FileReference.created_at, FileReference.id)
            ).all()
        )

    def add(self, file_id: str, payload: FileReferenceCreate) -> FileReference:
        self.files.require(file_id, "file")
        model = TARGET_MODELS[payload.target_type]
        target_exists = self.db.scalar(
            select(model.id).where(
                model.workspace_id == self.workspace_id,
                model.id == payload.target_id,
            )
        )
        if target_exists is None:
            raise AppError(
                404,
                "file_reference_target_not_found",
                "File reference target was not found in this workspace",
                {"target_type": payload.target_type, "target_id": payload.target_id},
            )
        locator = payload.locator.strip()
        if locator:
            chunk_exists = self.db.scalar(
                select(FileTextChunk.id).where(
                    FileTextChunk.workspace_id == self.workspace_id,
                    FileTextChunk.file_id == file_id,
                    FileTextChunk.locator == locator,
                )
            )
            if chunk_exists is None:
                raise AppError(
                    404,
                    "file_locator_not_found",
                    "The requested locator does not belong to this parsed file",
                    {"file_id": file_id, "locator": locator},
                )
        relation = payload.relation.strip()
        existing = self.db.scalar(
            self.references.query().where(
                FileReference.file_id == file_id,
                FileReference.target_type == payload.target_type,
                FileReference.target_id == payload.target_id,
                FileReference.relation == relation,
                FileReference.locator == locator,
            )
        )
        if existing is not None:
            return existing
        return self.references.add(
            FileReference(
                workspace_id=self.workspace_id,
                file_id=file_id,
                target_type=payload.target_type,
                target_id=payload.target_id,
                relation=relation,
                locator=locator,
                metadata_json=dict(payload.metadata),
            )
        )
