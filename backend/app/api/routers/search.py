from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.search import SearchRequest, SearchResponse
from app.providers.factory import search_provider_for_workspace
from app.services.search import SearchService

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
def search(payload: SearchRequest, db: DB, context: CurrentWorkspace, settings: AppSettings) -> SearchResponse:
    service = SearchService(
        db, context.workspace_id, context.principal.user_id,
        search_provider_for_workspace(db, context.workspace_id, settings, route=payload.search_route),
    )
    return service.search(payload)
