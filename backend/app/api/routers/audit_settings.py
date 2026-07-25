from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.common import ActionResponse, AuditView
from app.domain.schemas.management import SettingUpdateRequest, SettingView
from app.services.management import AuditService, SettingsService
from app.services.workspace_export import WorkspaceExportService


router = APIRouter(tags=["audit-settings"])


class AuditDeleteRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


def _safe_export_name(value: str) -> str:
    safe = "".join(
        character
        if character.isascii() and (character.isalnum() or character in "-_")
        else "_"
        for character in value
    )[:64]
    return safe or "workspace"


def _audit_service(db: DB, context: CurrentWorkspace) -> AuditService:
    return AuditService(db, context.workspace_id, context.principal.user_id)


@router.get("/workspace/export")
def export_workspace(
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
            "Content-Disposition": (
                "attachment; filename=\"learngraph-workspace-"
                f"{_safe_export_name(context.workspace_id)}.zip\""
            ),
            "Cache-Control": "no-store",
        },
    )


@router.get("/audit", response_model=list[AuditView])
def audit_log(
    db: DB,
    context: CurrentWorkspace,
    action: str | None = Query(default=None, max_length=120),
) -> list[AuditView]:
    return [AuditView.model_validate(item) for item in _audit_service(db, context).list(action)]


@router.delete("/audit/{event_id}", response_model=ActionResponse)
def delete_audit_event(
    event_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> ActionResponse:
    context.require_permission("workspace.manage")
    snapshot = _audit_service(db, context).delete(event_id)
    return ActionResponse(
        status="deleted",
        message="Audit event deleted",
        resource_id=str(snapshot["id"]),
        details={
            "action": snapshot["action"],
            "resource_type": snapshot["resource_type"],
            "resource_id": snapshot["resource_id"],
            "outcome": snapshot["outcome"],
        },
    )


@router.post("/audit/delete", response_model=ActionResponse)
def delete_audit_events(
    payload: AuditDeleteRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ActionResponse:
    context.require_permission("workspace.manage")
    result: dict[str, Any] = _audit_service(db, context).delete_many(payload.ids)
    return ActionResponse(
        status="deleted",
        message=f"Deleted {result['deleted']} audit events",
        resource_id=context.workspace_id,
        details=result,
    )


@router.get("/settings", response_model=list[SettingView])
def list_settings(db: DB, context: CurrentWorkspace) -> list[SettingView]:
    return [SettingView.model_validate(item) for item in SettingsService(db, context.workspace_id, context.principal.user_id).list()]


@router.put("/settings/{key}", response_model=SettingView)
def update_setting(
    key: str,
    payload: SettingUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
) -> SettingView:
    return SettingView.model_validate(
        SettingsService(db, context.workspace_id, context.principal.user_id).update(key, payload)
    )
