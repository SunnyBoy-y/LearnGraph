from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentWorkspace, DB
from app.domain.schemas.workspace_search import SearchKind, WorkspaceSearchPage
from app.services.workspace_search import search_workspace

router = APIRouter(tags=["workspace-search"])


@router.get("/workspace/search", response_model=WorkspaceSearchPage)
def workspace_search(
    db: DB, context: CurrentWorkspace,
    q: Annotated[str, Query(max_length=240)] = "",
    kind: SearchKind = "all",
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> WorkspaceSearchPage:
    return search_workspace(db, context, q, kind, offset=offset, limit=limit)
