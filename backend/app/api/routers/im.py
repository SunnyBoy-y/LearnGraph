from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import delete, select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.im_models import IMBinding, IMBindingCode
from app.services.im_channels import feishu_connection, issue_code

router = APIRouter(prefix="/im/feishu", tags=["IM"])


@router.get("")
def status(db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    connection = feishu_connection() if settings.durable_queue_enabled else None
    binding = db.scalar(select(IMBinding).where(
        IMBinding.connection == connection, IMBinding.workspace_id == context.workspace_id,
        IMBinding.user_id == context.principal.user_id,
    )) if connection else None
    private = context.workspace.workspace_kind == "personal" and context.workspace.owner_user_id == context.principal.user_id
    return {"configured": bool(connection), "personal_workspace": private, "bound": binding is not None,
            "session_id": binding.session_id if binding else None}


@router.post("/binding-code", status_code=201)
def create_code(db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    if context.workspace.workspace_kind != "personal" or context.workspace.owner_user_id != context.principal.user_id:
        raise AppError(403, "im_personal_workspace_required", "IM bindings require your personal workspace")
    connection = feishu_connection() if settings.durable_queue_enabled else None
    if connection is None:
        raise AppError(503, "feishu_not_configured", "Feishu bot is not configured")
    return {"code": issue_code(db, context, connection), "expires_in": 600}


@router.delete("/binding")
def unbind(db: DB, context: CurrentWorkspace) -> dict:
    for model in (IMBinding, IMBindingCode):
        db.execute(delete(model).where(model.connection.like("feishu:%"),
            model.workspace_id == context.workspace_id, model.user_id == context.principal.user_id))
    db.commit()
    return {"bound": False}
