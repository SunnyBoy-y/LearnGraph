from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.extension_models import (
    ExtensionInvocation,
    ExtensionPermissionGrant,
    MCPCapabilitySnapshot,
    MCPServer,
    MCPServerCredential,
    SkillRecord,
)
from app.repositories.scoped import ScopedRepository


class MCPServerRepository(ScopedRepository[MCPServer]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MCPServer, workspace_id)


class MCPCapabilitySnapshotRepository(ScopedRepository[MCPCapabilitySnapshot]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MCPCapabilitySnapshot, workspace_id)


class MCPServerCredentialRepository(ScopedRepository[MCPServerCredential]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MCPServerCredential, workspace_id)


class SkillRepository(ScopedRepository[SkillRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, SkillRecord, workspace_id)


class ExtensionPermissionGrantRepository(ScopedRepository[ExtensionPermissionGrant]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ExtensionPermissionGrant, workspace_id)


class ExtensionInvocationRepository(ScopedRepository[ExtensionInvocation]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ExtensionInvocation, workspace_id)
