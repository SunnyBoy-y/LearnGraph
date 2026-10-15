from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.services.management import ProviderService
from app.services.provider_config_import import commit_preview, list_sources, preview_sources

router = APIRouter(prefix='/providers/config-import', tags=['providers'])


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_ids: list[str] | None = Field(default=None, max_length=16)


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_id: str = Field(min_length=36, max_length=36)
    candidate_ids: list[str] = Field(min_length=1, max_length=200)


def service(db, context, settings):
    context.require_permission('workspace.manage')
    return ProviderService(db, context.workspace_id, context.principal.user_id, settings)


@router.get('/sources')
def sources(db: DB, context: CurrentWorkspace, settings: AppSettings):
    return list_sources(service(db, context, settings))


@router.post('/preview')
def preview(payload: PreviewRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return preview_sources(service(db, context, settings), payload.source_ids)


@router.post('/commit')
def commit(payload: CommitRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return commit_preview(service(db, context, settings), payload.preview_id, payload.candidate_ids)
