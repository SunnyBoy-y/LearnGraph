from __future__ import annotations

from collections.abc import Sequence
from typing import Generic, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.database import Base


ModelT = TypeVar("ModelT", bound=Base)


class ScopedRepository(Generic[ModelT]):
    """Minimal repository that makes workspace scoping impossible to omit."""

    def __init__(self, db: Session, model: type[ModelT], workspace_id: str) -> None:
        self.db = db
        self.model = model
        self.workspace_id = workspace_id

    def query(self) -> Select[tuple[ModelT]]:
        return select(self.model).where(self.model.workspace_id == self.workspace_id)  # type: ignore[attr-defined]

    def list(self, *, limit: int = 100) -> Sequence[ModelT]:
        return self.db.scalars(self.query().limit(limit)).all()

    def get(self, resource_id: str) -> ModelT | None:
        return self.db.scalar(self.query().where(self.model.id == resource_id))  # type: ignore[attr-defined]

    def require(self, resource_id: str, resource_name: str = "resource") -> ModelT:
        resource = self.get(resource_id)
        if resource is None:
            raise AppError(404, "not_found", f"{resource_name} not found in this workspace")
        return resource

    def add(self, instance: ModelT) -> ModelT:
        setattr(instance, "workspace_id", self.workspace_id)
        self.db.add(instance)
        self.db.flush()
        return instance

    def delete(self, instance: ModelT) -> None:
        self.db.delete(instance)
        self.db.flush()
