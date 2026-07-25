from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.schemas.common import ActionResponse
from app.domain.schemas.sources import FetchSourceRequest, SourceRecordView
from app.domain.schemas.workflow import DeleteConfirm, DeleteImpact
from app.providers.factory import fetch_provider_for_workspace
from app.services.sources import SourceService
from app.services.authorization import AuthorizationService


router = APIRouter(prefix="/sources", tags=["search-research"])


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> SourceService:
    return SourceService(
        db,
        context.workspace_id,
        context.principal.user_id,
        fetch_provider_for_workspace(db, context.workspace_id, settings),
    )


def require_source_access(
    source_id: str,
    action: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
):
    record = service(db, context, settings).get(source_id)
    authz = AuthorizationService(db, context.principal)
    if not authz.can_access_resource(context.workspace, "source", record.id, action):
        raise AppError(404, "source_not_found", "Source was not found")
    return record


@router.get("", response_model=list[SourceRecordView])
def list_sources(db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[SourceRecordView]:
    """列出当前工作区可访问的来源记录。无请求体，输出 URL、标题、抓取状态、引用和来源元数据。"""
    authz = AuthorizationService(db, context.principal)
    return [
        SourceRecordView.model_validate(item)
        for item in service(db, context, settings).list()
        if authz.can_access_resource(context.workspace, "source", item.id, "read")
    ]


@router.get("/{source_id}", response_model=SourceRecordView)
def get_source(source_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> SourceRecordView:
    """读取单个来源。输入来源 ID，输出该来源的完整可追溯记录。"""
    return SourceRecordView.model_validate(
        require_source_access(source_id, "read", db, context, settings)
    )


@router.get("/{source_id}/delete-impact", response_model=DeleteImpact)
def source_delete_impact(
    source_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> DeleteImpact:
    require_source_access(source_id, "delete", db, context, settings)
    return service(db, context, settings).delete_impact(source_id)


@router.post("/{source_id}/delete", response_model=ActionResponse)
def delete_source(
    source_id: str,
    payload: DeleteConfirm,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ActionResponse:
    require_source_access(source_id, "delete", db, context, settings)
    service(db, context, settings).delete_confirmed(source_id, payload.confirmation_text)
    return ActionResponse(
        status="deleted",
        message="Source, citations, and source links were deleted",
        resource_id=source_id,
    )


@router.post("/fetch", response_model=SourceRecordView, status_code=status.HTTP_201_CREATED)
def fetch_source(
    payload: FetchSourceRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SourceRecordView:
    """抓取网页来源。输入 URL 及授权范围等 FetchSourceRequest 字段，输出已持久化的来源记录；抓取失败会明确报错。"""
    return SourceRecordView.model_validate(service(db, context, settings).fetch(payload))
