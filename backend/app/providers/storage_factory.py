from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.migration_models import InfrastructureBinding
from app.providers.local.storage import LocalObjectStorageProvider
from app.providers.minio_storage import MinioObjectStorageProvider
from app.providers.ports.storage import ObjectStoragePort


def object_storage_provider(
    db: Session,
    workspace_id: str,
    settings: Settings,
) -> ObjectStoragePort:
    binding = db.scalar(
        select(InfrastructureBinding).where(
            InfrastructureBinding.workspace_id == workspace_id,
            InfrastructureBinding.capability == "object_storage",
            InfrastructureBinding.role == "active",
            InfrastructureBinding.status == "active",
            InfrastructureBinding.write_enabled.is_(True),
        )
    )
    if binding is None:
        return LocalObjectStorageProvider(settings.storage_root)
    if binding.provider_kind == "local":
        root = binding.locator.get("root")
        if not isinstance(root, str) or not root:
            raise AppError(503, "storage_binding_invalid", "Active local storage binding has no root")
        return LocalObjectStorageProvider(Path(root))
    if binding.provider_kind == "minio":
        return MinioObjectStorageProvider()
    raise AppError(503, "storage_provider_unavailable", "Active object-storage provider is unsupported")

