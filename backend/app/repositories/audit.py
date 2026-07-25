from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.models import AuditEvent
from app.repositories.scoped import ScopedRepository


class AuditRepository(ScopedRepository[AuditEvent]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, AuditEvent, workspace_id)

    def record(
        self,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str = "success",
        details: dict | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            workspace_id=self.workspace_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details or {},
        )
        return self.add(event)

