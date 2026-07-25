from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import (
    ComponentAuthorization,
    ComponentCheckRecord,
    ComponentManifestVersion,
    PluginRecord,
)
from app.repositories.scoped import ScopedRepository


class ComponentManifestRepository(ScopedRepository[ComponentManifestVersion]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ComponentManifestVersion, workspace_id)

    def list_for_plugin(self, plugin_id: str) -> list[ComponentManifestVersion]:
        return list(
            self.db.scalars(
                self.query()
                .where(ComponentManifestVersion.plugin_id == plugin_id)
                .order_by(ComponentManifestVersion.created_at.desc())
            ).all()
        )

    def get_for_plugin(
        self, plugin_id: str, manifest_version_id: str
    ) -> ComponentManifestVersion | None:
        return self.db.scalar(
            self.query().where(
                ComponentManifestVersion.plugin_id == plugin_id,
                ComponentManifestVersion.id == manifest_version_id,
            )
        )

    def require_for_plugin(
        self, plugin_id: str, manifest_version_id: str
    ) -> ComponentManifestVersion:
        manifest = self.get_for_plugin(plugin_id, manifest_version_id)
        if manifest is None:
            raise AppError(
                404,
                "component_manifest_not_found",
                "Component manifest version was not found in this workspace",
            )
        return manifest

    def current(self, plugin: PluginRecord) -> ComponentManifestVersion | None:
        return self.db.scalar(
            self.query().where(
                ComponentManifestVersion.plugin_id == plugin.id,
                ComponentManifestVersion.version == plugin.version,
            )
        )


class ComponentAuthorizationRepository(ScopedRepository[ComponentAuthorization]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ComponentAuthorization, workspace_id)

    def active_for_plugin(self, plugin_id: str) -> ComponentAuthorization | None:
        return self.db.scalar(
            self.query()
            .where(
                ComponentAuthorization.plugin_id == plugin_id,
                ComponentAuthorization.status == "authorized",
            )
            .order_by(ComponentAuthorization.authorized_at.desc())
        )

    def list_for_plugin(self, plugin_id: str) -> list[ComponentAuthorization]:
        return list(
            self.db.scalars(
                self.query()
                .where(ComponentAuthorization.plugin_id == plugin_id)
                .order_by(ComponentAuthorization.authorized_at.desc())
            ).all()
        )


class ComponentCheckRepository(ScopedRepository[ComponentCheckRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ComponentCheckRecord, workspace_id)

    def list_for_plugin(self, plugin_id: str) -> list[ComponentCheckRecord]:
        return list(
            self.db.scalars(
                self.query()
                .where(ComponentCheckRecord.plugin_id == plugin_id)
                .order_by(ComponentCheckRecord.checked_at.desc())
            ).all()
        )

    def latest(
        self,
        plugin_id: str,
        manifest_version_id: str,
        check_type: str,
    ) -> ComponentCheckRecord | None:
        return self.db.scalar(
            self.query()
            .where(
                ComponentCheckRecord.plugin_id == plugin_id,
                ComponentCheckRecord.manifest_version_id == manifest_version_id,
                ComponentCheckRecord.check_type == check_type,
            )
            .order_by(ComponentCheckRecord.checked_at.desc())
        )
