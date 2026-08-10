from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.research import (
    ResearchApprovalRequest,
    ResearchJobView,
    ResearchJobEventView,
    ResearchPlanView,
    ResearchRequest,
    SearchRequest,
    SearchResponse,
)
from app.providers.factory import deep_research_provider_for_workspace, search_provider_for_workspace
from app.services.research import ResearchService


router = APIRouter(tags=["search-research"])


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> ResearchService:
    return ResearchService(
        db,
        context.workspace_id,
        context.principal.user_id,
        search_provider_for_workspace(db, context.workspace_id, settings),
        deep_research_provider_for_workspace(db, context.workspace_id, settings),
        settings,
        is_manager="workspace.manage" in context.permissions,
    )


@router.post("/search", response_model=SearchResponse)
def search(payload: SearchRequest, db: DB, context: CurrentWorkspace, settings: AppSettings) -> SearchResponse:
    routed = ResearchService(
        db,
        context.workspace_id,
        context.principal.user_id,
        search_provider_for_workspace(
            db, context.workspace_id, settings, route=payload.search_route
        ),
        deep_research_provider_for_workspace(db, context.workspace_id, settings),
        settings,
    )
    return routed.search(payload)


@router.get("/research", response_model=list[ResearchJobView])
def list_research(db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[ResearchJobView]:
    return [ResearchJobView.model_validate(item) for item in service(db, context, settings).list_research()]


@router.post("/research/plan", response_model=ResearchPlanView)
def plan_research(
    payload: ResearchRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ResearchPlanView:
    return service(db, context, settings).plan_research(payload)


@router.post("/research", response_model=ResearchJobView, status_code=status.HTTP_201_CREATED)
def create_research(
    payload: ResearchRequest,
    response: Response,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ResearchJobView:
    job = service(db, context, settings).create_research(payload)
    if job.status in {"awaiting_approval", "queued", "running", "cancel_requested"}:
        response.status_code = status.HTTP_202_ACCEPTED
    return ResearchJobView.model_validate(job)


@router.get("/research/{job_id}", response_model=ResearchJobView)
def get_research(job_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> ResearchJobView:
    return ResearchJobView.model_validate(service(db, context, settings).get_research(job_id))


@router.get("/research/{job_id}/events", response_model=list[ResearchJobEventView])
def list_research_events(
    job_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[ResearchJobEventView]:
    return [
        ResearchJobEventView.model_validate(item)
        for item in service(db, context, settings).list_events(job_id)
    ]


@router.post("/research/{job_id}/approve", response_model=ResearchJobView, status_code=status.HTTP_202_ACCEPTED)
def approve_research(
    job_id: str,
    payload: ResearchApprovalRequest,
    response: Response,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ResearchJobView:
    job = service(db, context, settings).approve_research(job_id, payload)
    if job.status in {"rejected", "completed", "completed_local_demo", "completed_source_collection"}:
        response.status_code = status.HTTP_200_OK
    return ResearchJobView.model_validate(job)


@router.post("/research/{job_id}/cancel", response_model=ResearchJobView)
def cancel_research(job_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> ResearchJobView:
    return ResearchJobView.model_validate(service(db, context, settings).cancel_research(job_id))
