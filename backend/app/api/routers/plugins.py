from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentWorkspace, DB
from app.domain.schemas.management import PluginToggleRequest, PluginView
from app.services.management import PluginService


router = APIRouter(prefix="/plugins", tags=["plugins"])


def service(db: DB, context: CurrentWorkspace) -> PluginService:
    return PluginService(db, context.workspace_id, context.principal.user_id)


@router.get("", response_model=list[PluginView])
def list_plugins(db: DB, context: CurrentWorkspace) -> list[PluginView]:
    return [PluginView.model_validate(item) for item in service(db, context).list()]


@router.post("/{plugin_id}/toggle", response_model=PluginView)
def toggle_plugin(
    plugin_id: str,
    payload: PluginToggleRequest,
    db: DB,
    context: CurrentWorkspace,
) -> PluginView:
    return PluginView.model_validate(service(db, context).toggle(plugin_id, payload))

