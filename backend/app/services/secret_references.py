from __future__ import annotations

import re

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.secret_store import SecretStoreUnavailable
from app.core.security import mask_secret
from app.domain.models import WorkspaceSecretReference
from app.repositories.audit import AuditRepository
from app.services.provider_secrets import (
    ProviderSecretUnavailable,
    decrypt_secret_fields,
    encrypt_provider_secret,
)


SECRET_LABEL_RE = re.compile(r"^[a-z][a-z0-9._-]{1,119}$")
SECRET_REFERENCE_PREFIX = "secret://workspace/"


class SecretReferenceService:
    """Write-only secret injection with model-safe labels."""

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
        self.audit = AuditRepository(db, workspace_id)

    @staticmethod
    def normalize_label(value: str) -> str:
        label = value.strip()
        if label.startswith(SECRET_REFERENCE_PREFIX):
            label = label[len(SECRET_REFERENCE_PREFIX) :]
        if not SECRET_LABEL_RE.fullmatch(label):
            raise AppError(
                422,
                "invalid_secret_label",
                "Secret labels must start with a lowercase letter and contain only lowercase letters, digits, '.', '_' or '-'",
            )
        return label

    @staticmethod
    def public_view(item: WorkspaceSecretReference) -> dict:
        return {
            "label": item.label,
            "reference": f"{SECRET_REFERENCE_PREFIX}{item.label}",
            "purpose": item.purpose,
            "secret_masked": item.secret_masked,
            "version": item.version,
            "key_provider": item.key_provider,
            "key_version": item.key_version,
            "updated_at": item.updated_at,
        }

    def list(self) -> list[dict]:
        rows = self.db.scalars(
            select(WorkspaceSecretReference)
            .where(WorkspaceSecretReference.workspace_id == self.workspace_id)
            .order_by(WorkspaceSecretReference.label)
        ).all()
        return [self.public_view(item) for item in rows]

    def upsert(self, label: str, secret: SecretStr, purpose: str) -> dict:
        normalized = self.normalize_label(label)
        plaintext = secret.get_secret_value()
        try:
            encrypted = encrypt_provider_secret(self.settings, plaintext)
        except (SecretStoreUnavailable, ValueError) as exc:
            raise AppError(
                503,
                "secret_store_unavailable",
                "The labelled secret was rejected because the secure store is unavailable",
            ) from exc
        masked, _ = mask_secret(plaintext)
        item = self.db.scalar(
            select(WorkspaceSecretReference).where(
                WorkspaceSecretReference.workspace_id == self.workspace_id,
                WorkspaceSecretReference.label == normalized,
            )
        )
        if item is None:
            item = WorkspaceSecretReference(
                workspace_id=self.workspace_id,
                label=normalized,
                purpose=purpose,
                ciphertext=encrypted.ciphertext,
                algorithm=encrypted.algorithm,
                key_provider=encrypted.key_provider,
                key_version=encrypted.key_version,
                secret_masked=masked,
                version=1,
            )
            self.db.add(item)
        else:
            item.purpose = purpose
            item.ciphertext = encrypted.ciphertext
            item.algorithm = encrypted.algorithm
            item.key_provider = encrypted.key_provider
            item.key_version = encrypted.key_version
            item.secret_masked = masked
            item.version += 1
        self.audit.record(
            actor_id=self.actor_id,
            action="secret_reference.upsert",
            resource_type="secret_reference",
            resource_id=normalized,
            details={"purpose": purpose, "version": item.version},
        )
        self.db.commit()
        self.db.refresh(item)
        return self.public_view(item)

    def resolve(self, reference: str, *, purpose: str | None = None) -> str:
        """Internal-only secret resolution. Callers must never return the result."""

        label = self.normalize_label(reference)
        item = self.db.scalar(
            select(WorkspaceSecretReference).where(
                WorkspaceSecretReference.workspace_id == self.workspace_id,
                WorkspaceSecretReference.label == label,
            )
        )
        if item is None:
            raise AppError(404, "secret_label_not_found", "Secret label was not found")
        if purpose is not None and item.purpose != purpose:
            raise AppError(
                409,
                "secret_label_purpose_mismatch",
                "Secret label is not authorized for this purpose",
            )
        try:
            return decrypt_secret_fields(
                self.settings,
                ciphertext=item.ciphertext,
                algorithm=item.algorithm,
                key_provider=item.key_provider,
                key_version=item.key_version,
            )
        except ProviderSecretUnavailable as exc:
            raise AppError(
                503,
                "secret_label_unavailable",
                "The labelled secret cannot be opened by the secure store",
            ) from exc
