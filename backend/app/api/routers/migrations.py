from __future__ import annotations
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from anyio import CancelScope
from starlette.datastructures import UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from app.api.deps import AppSettings, CurrentPrincipal, CurrentWorkspace, DB, SystemAdminContext, bearer, current_principal
from app.core.errors import AppError
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import verify_password
from app.core.database_routing import OperationLease, state_root
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
    PrimaryDatabaseMigrationRequest,
    PrimaryDatabaseActivationRequest,
)
from app.services.migrations import MigrationService
from app.services.site_backup import SiteBackupService
from app.services.database_migration import controller
from app.api.backup_upload import BackupUploadRoute, backup_form


router = APIRouter(prefix="/migrations", tags=["migrations"], route_class=BackupUploadRoute)
_backup_password_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60)


class _BackupDownload(FileResponse):
    def __init__(self, artifact):
        self.artifact = artifact
        super().__init__(artifact.path, filename="learngraph-site-backup.zip", media_type="application/zip", headers={"Cache-Control": "no-store"})

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                await run_in_threadpool(self.artifact.close)


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
    return [MigrationJobView.model_validate(item) for item in service(db, context, settings).list() if item["resource_kind"] != "database" or context.principal.is_system_admin]


@router.get("/adapters", response_model=list[AdapterStatusView])
def list_migration_adapters(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[AdapterStatusView]:
    return [AdapterStatusView.model_validate(item) for item in service(db, context, settings).adapters() if item["capability"] != "database" or context.principal.is_system_admin and item["provider_kind"] == "postgresql"]


@router.get(
    "/database-configurations",
    response_model=list[DatabaseConfigurationView],
)
def list_database_configurations(
    db: DB,
    context: SystemAdminContext,
    settings: AppSettings,
) -> list[DatabaseConfigurationView]:
    context.require_permission("workspace.manage")
    return [
        DatabaseConfigurationView.model_validate(item)
        for item in service(
            db, context, settings
        ).database_configurations() if item["provider_kind"] == "postgresql"
    ]


@router.put(
    "/database-configurations/{provider_kind}",
    response_model=DatabaseConfigurationView,
)
def save_database_configuration(
    provider_kind: str,
    payload: DatabaseConfigurationUpsertRequest,
    db: DB,
    context: SystemAdminContext,
    settings: AppSettings,
) -> DatabaseConfigurationView:
    context.require_permission("workspace.manage")
    if provider_kind.strip().casefold() not in {"postgres", "postgresql"}:
        raise AppError(422, "unsupported_database_provider", "主业务库仅支持 SQLite → PostgreSQL")
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
    archive = SiteBackupService(db, settings).export_file(payload.backup_password.get_secret_value())
    return _BackupDownload(archive)


@router.post('/backup/browser-grants')
def prepare_browser_backup(payload: SiteBackupRequest, request: Request, db: DB, principal: CurrentPrincipal, settings: AppSettings):
    from app.services.backup_download import COOKIE, TTL, prepare
    from starlette.responses import JSONResponse
    _verify_backup_admin(db,principal,payload.current_password.get_secret_value())
    artifact=SiteBackupService(db,settings).export_file(payload.backup_password.get_secret_value())
    try:
        ticket,token=prepare(artifact,settings,principal)
    finally:
        artifact.close()
    path=request.url_for('browser_full_backup',ticket=ticket).path
    response=JSONResponse({'ticket_id':ticket,'expires_in':TTL},headers={'Cache-Control':'no-store'})
    response.set_cookie(COOKIE,token,max_age=TTL,path=path,httponly=True,secure=request.url.scheme=='https',samesite='strict')
    return response


@router.get('/backup/browser/{ticket}')
def browser_full_backup(ticket: str, request: Request, db: DB, settings: AppSettings,
    credentials: Annotated[HTTPAuthorizationCredentials | None,Depends(bearer)]):
    from app.services.backup_download import COOKIE, claim
    if request.headers.get('range'):
        raise AppError(416,'site_backup_download_range_unsupported','一次性备份请完整下载')
    principal=current_principal(credentials,db) if credentials is not None else None
    artifact=claim(settings,db,ticket,token=request.cookies.get(COOKIE),principal=principal)
    response=_BackupDownload(artifact)
    response.headers['Accept-Ranges']='none'
    response.headers['Referrer-Policy']='no-referrer'
    response.delete_cookie(COOKIE,path=request.url.path)
    return response


@router.post("/restore", response_model=BackupRestoreView, status_code=status.HTTP_200_OK,
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["backup", "current_password", "backup_password"], "properties": {
            "backup": {"type": "string", "format": "binary"}, "confirm": {"type": "boolean", "default": False},
            "current_password": {"type": "string", "minLength": 1, "maxLength": 1024},
            "backup_password": {"type": "string", "minLength": 1, "maxLength": 1024}}}}}}})
async def restore_full_backup(
    request: Request,
    db: DB,
    principal: CurrentPrincipal,
    settings: AppSettings,
) -> BackupRestoreView:
    # Authorize BEFORE parsing/spooling a multi-GiB body. Declaring FastAPI File
    # parameters would parse it before endpoint permission checks run.
    user = db.get(User, principal.user_id)
    if not principal.is_system_admin or user is None or not user.is_system_admin or user.status != 'active':
        raise AppError(403, "deployment_admin_required", "仅系统管理员可备份或恢复全站数据")
    if principal.must_change_password:
        raise AppError(403, "password_change_required", "请先修改初始管理员密码")
    if hasattr(db, 'rollback'):
        db.rollback()  # Do not pin the old database while receiving a large file.
    async with backup_form(request,settings) as form:
        backup = form.get('backup')
        current_password, backup_password = form.get('current_password'), form.get('backup_password')
        if not isinstance(backup, UploadFile) or any(not isinstance(value, str) or not 1 <= len(value) <= 1024 for value in (current_password, backup_password)):
            raise AppError(422, "site_restore_fields_invalid", "请提供备份文件、当前管理员密码和备份密码")
        _verify_backup_admin(db, principal, current_password)
        if str(form.get('confirm', '')).casefold() not in {'true', '1', 'on', 'yes'}:
            raise AppError(400, "restore_confirmation_required", "Restore requires explicit confirmation")
        result = await run_in_threadpool(SiteBackupService(db, settings).stage_restore, backup.file, backup_password)
        return BackupRestoreView.model_validate(result)


@router.post("/preflight", response_model=MigrationJobView, status_code=status.HTTP_201_CREATED)
def migration_preflight(
    payload: MigrationPreflightRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MigrationJobView:
    if payload.resource_kind == "database" or payload.resource_kind is None and payload.source_kind not in {"local", "local_files"}:
        if not context.principal.is_system_admin:
            raise AppError(403, "deployment_admin_required", "仅系统管理员可迁移主业务库")
        raise AppError(409, "online_database_migration_required", "请使用主业务库在线迁移入口；旧维护窗口迁移已禁用")
    return MigrationJobView.model_validate(service(db, context, settings).preflight(payload))


@router.get("/database/status")
def primary_database_status(context: SystemAdminContext, settings: AppSettings):
    return controller(settings).status()


@router.post("/database/preflight")
def primary_database_preflight(context: SystemAdminContext, settings: AppSettings):
    return controller(settings).preflight()


@router.post("/database/start", status_code=202)
def primary_database_start(payload: PrimaryDatabaseMigrationRequest, db: DB, context: SystemAdminContext, settings: AppSettings):
    _verify_backup_admin(db, context.principal, payload.current_password.get_secret_value())
    target_url = None
    if not payload.managed:
        migration_service = service(db, context, settings)
        configuration = migration_service._database_configuration("postgresql")
        if configuration is None:
            raise AppError(409, "postgresql_configuration_required", "请先配置 PostgreSQL 连接")
        target_url = migration_service._database_url(configuration).render_as_string(hide_password=False)
    # Release the request's authentication transaction before the worker drains.
    db.commit()
    return controller(settings).start(actor_id=context.principal.user_id, workspace_id=context.workspace_id,
        managed=payload.managed, target_url=target_url, auto_activate=payload.auto_activate)


@router.post("/database/activate", status_code=202)
def primary_database_activate(payload: PrimaryDatabaseActivationRequest, db: DB, context: SystemAdminContext, settings: AppSettings):
    _verify_backup_admin(db, context.principal, payload.current_password.get_secret_value())
    db.commit()
    return controller(settings).commit()


@router.post("/database/cancel")
def primary_database_cancel(payload: MigrationConfirmRequest, context: SystemAdminContext, settings: AppSettings):
    return controller(settings).cancel()


@router.post("/database/resume", status_code=202)
def primary_database_resume(payload: MigrationConfirmRequest, context: SystemAdminContext, settings: AppSettings):
    return controller(settings).resume()


def _object_job_only(migration_service: MigrationService, job_id: str, context) -> None:
    job = migration_service._job(job_id)
    if job.source_kind not in {"local", "local_files"}:
        if not context.principal.is_system_admin:
            raise AppError(403, "deployment_admin_required", "仅系统管理员可访问主业务库迁移")
        raise AppError(409, "online_database_migration_required", "旧数据库迁移入口已禁用，请使用主业务库在线迁移")


@router.get("/{job_id}", response_model=MigrationJobView)
def get_migration(
    job_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MigrationJobView:
    migration_service = service(db, context, settings)
    _object_job_only(migration_service, job_id, context)
    return MigrationJobView.model_validate(migration_service.view(migration_service._job(job_id)))


@router.post("/{job_id}/start", response_model=MigrationJobView)
def start_migration(
    job_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MigrationJobView:
    migration_service = service(db, context, settings)
    _object_job_only(migration_service, job_id, context)
    try:
        ownership = OperationLease(state_root(settings))
    except BlockingIOError as exc:
        raise AppError(409, "database_migration_busy", "请先完成或取消主业务库迁移") from exc
    try:
        return MigrationJobView.model_validate(migration_service.start(job_id))
    finally:
        ownership.close()


@router.post("/{job_id}/commit", response_model=MigrationJobView)
def commit_migration(
    job_id: str,
    payload: MigrationConfirmRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MigrationJobView:
    del payload
    migration_service = service(db, context, settings)
    _object_job_only(migration_service, job_id, context)
    return MigrationJobView.model_validate(migration_service.commit(job_id))


@router.post("/{job_id}/rollback", response_model=MigrationJobView)
def rollback_migration(
    job_id: str,
    payload: MigrationConfirmRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MigrationJobView:
    del payload
    migration_service = service(db, context, settings)
    _object_job_only(migration_service, job_id, context)
    return MigrationJobView.model_validate(migration_service.rollback(job_id))
