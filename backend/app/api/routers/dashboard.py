from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentWorkspace, DB
from app.domain.schemas.auth import DashboardResponse
from app.services.dashboard import DashboardService


router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(context: CurrentWorkspace, db: DB) -> DashboardResponse:
    return DashboardService(db, context.workspace, context.principal).get()
