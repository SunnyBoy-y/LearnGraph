from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.files import (
    DocumentJobCreate,
    DocumentJobEventView,
    DocumentJobView,
    DocumentQueryPreviewRequest,
    DocumentQueryPreviewView,
    DocumentRevisionView,
)
from app.services.authorization import AuthorizationService
from app.services.document_learning import DocumentLearningService, run_document_job


router = APIRouter(tags=["document-learning"])


def service(
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> DocumentLearningService:
    return DocumentLearningService(
        db,
        context.workspace_id,
        context.principal.user_id,
        settings,
    )


def _require_file_access(db: DB, context: CurrentWorkspace, file_id: str, permission: str) -> None:
    if not AuthorizationService(db, context.principal).can_access_resource(
        context.workspace, "file", file_id, permission
    ):
        from app.core.errors import AppError

        raise AppError(404, "not_found", "File not found in this workspace")


@router.post(
    "/files/{file_id}/document-jobs",
    response_model=DocumentJobView,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_document_job(
    file_id: str,
    payload: DocumentJobCreate,
    background_tasks: BackgroundTasks,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=128)
    ] = None,
) -> DocumentJobView:
    _require_file_access(db, context, file_id, "write")
    job, created = service(db, context, settings).create_job(
        file_id, payload, idempotency_key
    )
    if created or job.status == "queued":
        background_tasks.add_task(
            run_document_job,
            job.id,
            DocumentLearningService.execution_token(job),
        )
    return DocumentJobView.model_validate(job)


@router.get(
    "/files/{file_id}/document-revisions",
    response_model=list[DocumentRevisionView],
)
def list_document_revisions(
    file_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[DocumentRevisionView]:
    _require_file_access(db, context, file_id, "read")
    return [
        DocumentRevisionView.model_validate(item)
        for item in service(db, context, settings).revisions(file_id)
    ]


@router.get("/document-jobs/{job_id}", response_model=DocumentJobView)
def get_document_job(
    job_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> DocumentJobView:
    job = service(db, context, settings).get_job(job_id)
    _require_file_access(db, context, job.file_id, "read")
    return DocumentJobView.model_validate(job)


@router.get(
    "/document-jobs/{job_id}/events",
    response_model=list[DocumentJobEventView],
)
def list_document_job_events(
    job_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[DocumentJobEventView]:
    document_service = service(db, context, settings)
    job = document_service.get_job(job_id)
    _require_file_access(db, context, job.file_id, "read")
    return [
        DocumentJobEventView.model_validate(item)
        for item in document_service.job_events(job_id)
    ]


@router.post("/document-jobs/{job_id}/retry", response_model=DocumentJobView)
def retry_document_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> DocumentJobView:
    document_service = service(db, context, settings)
    job = document_service.get_job(job_id)
    _require_file_access(db, context, job.file_id, "write")
    job = document_service.retry(job_id)
    background_tasks.add_task(
        run_document_job,
        job.id,
        DocumentLearningService.execution_token(job),
    )
    return DocumentJobView.model_validate(job)


@router.post("/document-jobs/{job_id}/cancel", response_model=DocumentJobView)
def cancel_document_job(
    job_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> DocumentJobView:
    document_service = service(db, context, settings)
    job = document_service.get_job(job_id)
    _require_file_access(db, context, job.file_id, "write")
    return DocumentJobView.model_validate(document_service.cancel(job_id))


@router.post("/document-query/preview", response_model=DocumentQueryPreviewView)
def preview_document_query(
    payload: DocumentQueryPreviewRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> DocumentQueryPreviewView:
    for file_id in set(payload.file_ids):
        _require_file_access(db, context, file_id, "read")
    return service(db, context, settings).preview(payload)
