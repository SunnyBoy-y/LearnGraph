from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.schemas.images import ImageGenerationTaskView
from app.services.authorization import AuthorizationService
from app.services.image_generations import ImageGenerationService


router = APIRouter(prefix="/image-generations", tags=["image-generations"])


def service(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ImageGenerationService:
    return ImageGenerationService(
        db,
        context.workspace_id,
        context.principal.user_id,
        settings,
    )


def require_task_session_access(
    task_session_id: str,
    permission: str,
    db: DB,
    context: CurrentWorkspace,
) -> None:
    if not AuthorizationService(db, context.principal).can_access_resource(
        context.workspace,
        "session",
        task_session_id,
        permission,
    ):
        raise AppError(404, "not_found", "Resource not found in this workspace")


@router.get("/{task_id}", response_model=ImageGenerationTaskView)
def get_image_generation(
    task_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ImageGenerationTaskView:
    task = service(db, context, settings).get(task_id)
    require_task_session_access(task.session_id, "read", db, context)
    return ImageGenerationTaskView.model_validate(task)


@router.post("/{task_id}/cancel", response_model=ImageGenerationTaskView)
def cancel_image_generation(
    task_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ImageGenerationTaskView:
    image_service = service(db, context, settings)
    task = image_service.get(task_id)
    require_task_session_access(task.session_id, "write", db, context)
    return ImageGenerationTaskView.model_validate(image_service.cancel(task_id))
