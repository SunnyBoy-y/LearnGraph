from __future__ import annotations

from fastapi import APIRouter, File, Form, Response, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.api.deps import AppSettings, CurrentPrincipal, CurrentWorkspace, DB
from app.core.errors import AppError
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import verify_password
from app.domain.models import User
from app.domain.schemas.migrations import (
    AdapterStatusView,
    DatabaseConfigurationUpsertRequest,
    DatabaseConfigurationView,
    MigrationConfirmRequest,
    MigrationJobView,
    MigrationPreflightRequest,
    BackupRestoreView,
    SiteBackupRequest,
)
from app.services.migrations import MigrationService
from app.services.site_backup import SiteBackupService


router = APIRouter(prefix="/migrations", tags=["migrations"])
_backup_password_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60)


def _verify_backup_admin(db: DB, principal: CurrentPrincipal, password: str) -> None:
    if not principal.is_system_admin:
        raise AppError(403, "deployment_admin_required", "仅系统管理员可备份或恢复全站数据")
    if principal.must_change_password:
        raise AppError(403, "password_change_required", "请先修改初始管理员密码")
    if not _backup_password_limiter.allow(principal.user_id):
        raise AppError(429, "backup_password_rate_limited", "密码验证过于频繁，请一分钟后重试")
    user = db.get(User, principal.user_id)
    if user is None or not user.is_system_admin or user.status != "active":
        raise AppError(403, "deployment_admin_required", "仅系统管理员可备份或恢复全站数据")
    if not verify_password(password, user.password_hash):
        # A wrong reauthentication password must not expire the login session.
        raise AppError(403, "backup_password_incorrect", "当前管理员密码不正确")


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


@router.get("/backup")
def legacy_full_backup(principal: CurrentPrincipal) -> Response:
    if not principal.is_system_admin:
        raise AppError(403, "deployment_admin_required", "仅系统管理员可备份全站数据")
    raise AppError(405, "backup_password_required", "请使用 POST 并验证管理员密码后下载加密全站备份")


@router.post("/backup")
def full_backup(
    payload: SiteBackupRequest,
    db: DB,
    principal: CurrentPrincipal,
    settings: AppSettings,
) -> Response:
    _verify_backup_admin(db, principal, payload.current_password.get_secret_value())
    archive = SiteBackupService(db, settings).export(payload.backup_password.get_secret_value())
    return Response(
        content=archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="learngraph-site-backup.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/restore", response_model=BackupRestoreView, status_code=status.HTTP_200_OK)
async def restore_full_backup(
    db: DB,
    principal: CurrentPrincipal,
    settings: AppSettings,
    backup: UploadFile = File(...),
    confirm: bool = Form(False),
    current_password: str = Form(..., min_length=1, max_length=1024),
    backup_password: str = Form(..., min_length=1, max_length=1024),
) -> BackupRestoreView:
    _verify_backup_admin(db, principal, current_password)
    if not confirm:
        raise AppError(400, "restore_confirmation_required", "Restore requires explicit confirmation")
    payload = await backup.read(settings.max_backup_bytes + 1)
    result = await run_in_threadpool(SiteBackupService(db, settings).stage_restore, payload, backup_password)
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
