from __future__ import annotations

from fastapi import APIRouter, File, Form, Response, UploadFile, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.migrations import (
    AdapterStatusView,
    DatabaseConfigurationUpsertRequest,
    DatabaseConfigurationView,
    MigrationConfirmRequest,
    MigrationJobView,
    MigrationPreflightRequest,
    BackupRestoreView,
)
from app.services.migrations import MigrationService
from app.services.workspace_export import WorkspaceExportService
from app.services.workspace_restore import WorkspaceRestoreService


router = APIRouter(prefix="/migrations", tags=["migrations"])


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> MigrationService:
    return MigrationService(
        db,
        context.workspace_id,
        context.principal.user_id,
        settings,
    )


@router.get("", response_model=list[MigrationJobView])
def list_migrations(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[MigrationJobView]:
    return [MigrationJobView.model_validate(item) for item in service(db, context, settings).list()]


@router.get("/adapters", response_model=list[AdapterStatusView])
def list_migration_adapters(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[AdapterStatusView]:
    return [AdapterStatusView.model_validate(item) for item in service(db, context, settings).adapters()]


@router.get(
    "/database-configurations",
    response_model=list[DatabaseConfigurationView],
)
def list_database_configurations(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[DatabaseConfigurationView]:
    context.require_permission("workspace.manage")
    return [
        DatabaseConfigurationView.model_validate(item)
        for item in service(
            db, context, settings
        ).database_configurations()
    ]


@router.put(
    "/database-configurations/{provider_kind}",
    response_model=DatabaseConfigurationView,
)
def save_database_configuration(
    provider_kind: str,
    payload: DatabaseConfigurationUpsertRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> DatabaseConfigurationView:
    context.require_permission("workspace.manage")
    return DatabaseConfigurationView.model_validate(
        service(db, context, settings).save_database_configuration(
            provider_kind, payload
        )
    )


def _safe_backup_name(value: str) -> str:
    safe = "".join(
        character if character.isascii() and (character.isalnum() or character in "-_") else "_"
        for character in value
    )[:64]
    return safe or "workspace"


@router.get("/backup")
def full_backup(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> Response:
    context.require_permission("workspace.manage")
    payload = WorkspaceExportService(
        db,
        context.workspace,
        context.principal.user_id,
        settings.storage_root,
        settings.max_upload_bytes,
        settings.memory_root,
    ).export_zip()
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="learngraph-full-backup-{_safe_backup_name(context.workspace_id)}.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/restore", response_model=BackupRestoreView, status_code=status.HTTP_200_OK)
async def restore_full_backup(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    backup: UploadFile = File(...),
    confirm: bool = Form(False),
) -> BackupRestoreView:
    if not confirm:
        from app.core.errors import AppError

        raise AppError(400, "restore_confirmation_required", "Restore requires explicit confirmation")
    payload = await backup.read(settings.max_backup_bytes + 1)
    context.require_permission("workspace.manage")
    result = WorkspaceRestoreService(
        db,
        context.workspace,
        context.principal.user_id,
        settings.storage_root,
        settings.memory_root,
        settings.max_backup_bytes,
    ).restore(payload)
    return BackupRestoreView.model_validate(result)


@router.post("/preflight", response_model=MigrationJobView, status_code=status.HTTP_201_CREATED)
def migration_preflight(
    payload: MigrationPreflightRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MigrationJobView:
    return MigrationJobView.model_validate(service(db, context, settings).preflight(payload))


@router.get("/{job_id}", response_model=MigrationJobView)
def get_migration(
    job_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MigrationJobView:
    migration_service = service(db, context, settings)
    return MigrationJobView.model_validate(migration_service.view(migration_service._job(job_id)))


@router.post("/{job_id}/start", response_model=MigrationJobView)
def start_migration(
    job_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MigrationJobView:
    return MigrationJobView.model_validate(service(db, context, settings).start(job_id))


@router.post("/{job_id}/commit", response_model=MigrationJobView)
def commit_migration(
    job_id: str,
    payload: MigrationConfirmRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MigrationJobView:
    del payload
    return MigrationJobView.model_validate(service(db, context, settings).commit(job_id))


@router.post("/{job_id}/rollback", response_model=MigrationJobView)
def rollback_migration(
    job_id: str,
    payload: MigrationConfirmRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MigrationJobView:
    del payload
    return MigrationJobView.model_validate(service(db, context, settings).rollback(job_id))
