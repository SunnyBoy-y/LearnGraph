from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import Artifact, ArtifactShareToken, ArtifactVersion, FileRecord, utc_now


class ArtifactGatewayService:
    """Creates immutable artifact versions and resolves read-only share tokens."""

    def __init__(self, db: Session, workspace_id: str, actor_id: str, tenant_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.tenant_id = tenant_id

    def create_artifact(self, name: str, description: str = "") -> Artifact:
        artifact = Artifact(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            created_by=self.actor_id,
            name=name[:240],
            description=description[:2000],
        )
        self.db.add(artifact)
        self.db.commit()
        self.db.refresh(artifact)
        return artifact

    def list_artifact_summaries(self) -> list[tuple[Artifact, int]]:
        rows = (
            self.db.execute(
                select(Artifact, func.count(ArtifactVersion.id))
                .outerjoin(
                    ArtifactVersion,
                    ArtifactVersion.artifact_id == Artifact.id,
                )
                .where(
                    Artifact.workspace_id == self.workspace_id,
                    Artifact.tenant_id == self.tenant_id,
                    Artifact.status == "active",
                )
                .group_by(Artifact.id)
                .order_by(Artifact.created_at.desc())
            )
            .all()
        )
        return [(artifact, count) for artifact, count in rows]

    def list_versions(self, artifact_id: str) -> list[ArtifactVersion]:
        artifact = self.db.scalar(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.workspace_id == self.workspace_id,
                Artifact.tenant_id == self.tenant_id,
            )
        )
        if artifact is None:
            raise AppError(404, "artifact_not_found", "Artifact was not found")
        return list(
            self.db.scalars(
                select(ArtifactVersion)
                .where(ArtifactVersion.artifact_id == artifact.id)
                .order_by(ArtifactVersion.version.desc())
            )
        )

    def list_share_tokens(self, version_id: str) -> list[ArtifactShareToken]:
        version = self._version_for_workspace(version_id)
        return list(
            self.db.scalars(
                select(ArtifactShareToken)
                .where(ArtifactShareToken.artifact_version_id == version.id)
                .order_by(ArtifactShareToken.created_at.desc())
            )
        )

    def publish_version(
        self,
        artifact_id: str,
        file_id: str,
        *,
        source_chat_session_id: str | None = None,
        release_notes: str = "",
    ) -> ArtifactVersion:
        artifact = self.db.scalar(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.workspace_id == self.workspace_id,
                Artifact.tenant_id == self.tenant_id,
                Artifact.status == "active",
            )
        )
        if artifact is None:
            raise AppError(404, "artifact_not_found", "Artifact was not found")
        file = self.db.scalar(
            select(FileRecord).where(
                FileRecord.id == file_id,
                FileRecord.workspace_id == self.workspace_id,
            )
        )
        if file is None:
            raise AppError(404, "artifact_file_not_found", "Source file was not found")
        current = self.db.scalar(
            select(func.max(ArtifactVersion.version)).where(
                ArtifactVersion.artifact_id == artifact.id
            )
        )
        version = ArtifactVersion(
            artifact_id=artifact.id,
            version=(current or 0) + 1,
            file_id=file.id,
            original_name=file.original_name,
            sha256=file.sha256,
            size_bytes=file.size_bytes,
            mime_type=file.mime_type,
            source_workspace_id=self.workspace_id,
            source_chat_session_id=source_chat_session_id,
            published_by=self.actor_id,
            release_notes=release_notes[:4000],
        )
        self.db.add(version)
        self.db.commit()
        self.db.refresh(version)
        return version

    def create_share_token(
        self,
        version_id: str,
        *,
        label: str = "",
        expires_at: datetime | None = None,
        max_downloads: int | None = None,
    ) -> tuple[str, ArtifactShareToken]:
        version = self._version_for_workspace(version_id)
        raw_token = secrets.token_urlsafe(32)
        record = ArtifactShareToken(
            artifact_version_id=version.id,
            created_by=self.actor_id,
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            token_prefix=raw_token[:12],
            label=label[:120],
            expires_at=expires_at,
            max_downloads=max_downloads if max_downloads and max_downloads > 0 else None,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return raw_token, record

    def resolve_share_token(self, raw_token: str) -> ArtifactVersion:
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        token = self.db.scalar(
            select(ArtifactShareToken).where(ArtifactShareToken.token_hash == digest)
        )
        now = utc_now()
        if (
            token is None
            or token.revoked_at is not None
            or (token.expires_at is not None and token.expires_at <= now)
            or (token.max_downloads is not None and token.download_count >= token.max_downloads)
        ):
            raise AppError(404, "artifact_share_not_found", "Artifact share was not found")
        version = self.db.get(ArtifactVersion, token.artifact_version_id)
        if version is None or version.status != "published":
            raise AppError(404, "artifact_share_not_found", "Artifact share was not found")
        token.download_count += 1
        self.db.commit()
        return version

    def revoke_share_token(self, token_id: str) -> ArtifactShareToken:
        token = self.db.scalar(
            select(ArtifactShareToken)
            .join(ArtifactVersion, ArtifactVersion.id == ArtifactShareToken.artifact_version_id)
            .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
            .where(
                ArtifactShareToken.id == token_id,
                Artifact.workspace_id == self.workspace_id,
                Artifact.tenant_id == self.tenant_id,
            )
        )
        if token is None:
            raise AppError(404, "artifact_share_not_found", "Artifact share was not found")
        token.revoked_at = utc_now()
        self.db.commit()
        self.db.refresh(token)
        return token

    def _version_for_workspace(self, version_id: str) -> ArtifactVersion:
        version = self.db.scalar(
            select(ArtifactVersion)
            .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
            .where(
                ArtifactVersion.id == version_id,
                Artifact.workspace_id == self.workspace_id,
                Artifact.tenant_id == self.tenant_id,
            )
        )
        if version is None:
            raise AppError(404, "artifact_version_not_found", "Artifact version was not found")
        return version
