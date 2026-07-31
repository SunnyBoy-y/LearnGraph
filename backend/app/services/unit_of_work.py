from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Self

from sqlalchemy.orm import Session


class MemoryUnitOfWork(AbstractContextManager["MemoryUnitOfWork"]):
    """One command/worker-step transaction. Nested services flush, outer code commits."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._complete = False

    def __enter__(self) -> Self:
        return self

    def flush(self) -> None:
        self.db.flush()

    def commit(self) -> None:
        self.db.commit()
        self._complete = True

    def rollback(self) -> None:
        self.db.rollback()
        self._complete = True

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is not None or not self._complete:
            self.db.rollback()
        return False
