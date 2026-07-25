from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from typing import Annotated
from fastapi import APIRouter, File, Header, Query, Response, UploadFile, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.schemas.common import ActionResponse
from app.domain.schemas.files import (
    FileParserCapabilityView,
    AudioTranscriptionCreate,
    AudioTranscriptionView,
    FileBatchDeleteConfirm,
    FileBatchDeleteImpact,
    FileBatchDeleteResponse,
    FileBatchSelection,
    FileReferenceCreate,
    FileReferenceView,
    FileStorageSummary,
    FileTextChunkView,
    FileView,
)
from app.domain.schemas.workflow import DeleteConfirm, DeleteImpact
from app.services.files import FileService
from app.services.authorization import AuthorizationService


router = APIRouter(prefix="/files", tags=["files"])


def _content_disposition(original_name: str) -> str:
    """Build an HTTP-safe attachment name without losing the Unicode filename."""
    filename = Path(original_name).name or "download"
    ascii_stem = "".join(
        character
        if character.isascii()
        and 0x20 <= ord(character) < 0x7F
        and character not in {'"', "\\", ";"}
        else "_"
        for character in Path(filename).stem
    ).strip(" ._")
    ascii_suffix = "".join(
        character
        for character in Path(filename).suffix
        if character.isascii() and (character.isalnum() or character in {".", "_", "-"})
    )
    ascii_fallback = f"{ascii_stem or 'download'}{ascii_suffix}"
    encoded_filename = quote(filename, safe="")
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{encoded_filename}"
    )


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> FileService:
    return FileService(db, context.workspace_id, context.principal.user_id, settings)


def _require_file_access(
    db: DB,
    context: CurrentWorkspace,
    file_id: str,
    permission: str,
) -> None:
    if not AuthorizationService(db, context.principal).can_access_resource(
        context.workspace,
        "file",
        file_id,
        permission,
    ):
        raise AppError(404, "not_found", "File not found in this workspace")


@router.get("", response_model=list[FileView])
def list_files(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> list[FileView]:
    authz = AuthorizationService(db, context.principal)
    # When the client asks for a name filter (chat @ mention), use the bounded
    # search path. Unfiltered list remains full-workspace for the materials UI.
    if q is not None and q.strip():
        records = service(db, context, settings).search(q=q, limit=limit or 20)
    elif limit is not None:
        records = service(db, context, settings).search(q=None, limit=limit)
    else:
        records = service(db, context, settings).list()
    return [
        FileView.model_validate(item)
        for item in records
        if authz.can_access_resource(context.workspace, "file", item.id, "read")
    ]


@router.get("/lookup", response_model=FileView)
def lookup_file(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    name: Annotated[str, Query(min_length=1, max_length=255)],
    sha256: Annotated[str, Query(min_length=64, max_length=64)],
) -> FileView:
    """Return an existing FileRecord when name + content hash already match.

    Used by the chat composer to reuse a materials-library file without
    re-uploading bytes. Absence is a typed 404 so the client can fall back to
    multipart upload (which still dedups server-side as a second line of defense).
    """

    authz = AuthorizationService(db, context.principal)
    record = service(db, context, settings).lookup_by_name_and_hash(
        original_name=name,
        sha256=sha256,
    )
    if record is None or not authz.can_access_resource(
        context.workspace, "file", record.id, "read"
    ):
        raise AppError(404, "file_not_found", "No matching file in this workspace")
    return FileView.model_validate(record)


@router.get("/storage-summary", response_model=FileStorageSummary)
def file_storage_summary(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> FileStorageSummary:
    authz = AuthorizationService(db, context.principal)
    visible = [
        item
        for item in service(db, context, settings).list()
        if authz.can_access_resource(context.workspace, "file", item.id, "read")
    ]
    return FileStorageSummary(
        file_count=len(visible),
        total_bytes=int(sum(int(item.size_bytes or 0) for item in visible)),
    )


@router.get("/parser-capabilities", response_model=list[FileParserCapabilityView])
def parser_capabilities(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[FileParserCapabilityView]:
    return [
        FileParserCapabilityView.model_validate(item.__dict__)
        for item in service(db, context, settings).capabilities()
    ]


@router.post("", response_model=FileView, status_code=status.HTTP_201_CREATED)
async def upload_file(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    file: UploadFile = File(...),
) -> FileView:
    return FileView.model_validate(await service(db, context, settings).upload(file))


@router.post("/{file_id}/parse", response_model=FileView)
def parse_file(file_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> FileView:
    _require_file_access(db, context, file_id, "write")
    return FileView.model_validate(service(db, context, settings).parse(file_id))


@router.get("/{file_id}/content")
def download_file(file_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> Response:
    _require_file_access(db, context, file_id, "read")
    record, payload = service(db, context, settings).content(file_id)
    return Response(
        content=payload,
        media_type=record.mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": _content_disposition(record.original_name),
        },
    )


@router.get("/{file_id}/chunks", response_model=list[FileTextChunkView])
def list_file_chunks(file_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[FileTextChunkView]:
    _require_file_access(db, context, file_id, "read")
    return [
        FileTextChunkView.model_validate(item)
        for item in service(db, context, settings).list_chunks(file_id)
    ]


@router.get("/{file_id}/transcriptions", response_model=list[AudioTranscriptionView])
def list_file_transcriptions(
    file_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings
) -> list[AudioTranscriptionView]:
    _require_file_access(db, context, file_id, "read")
    return [
        AudioTranscriptionView.model_validate(item)
        for item in service(db, context, settings).list_transcriptions(file_id)
    ]


@router.post(
    "/{file_id}/transcriptions",
    response_model=AudioTranscriptionView,
    status_code=status.HTTP_201_CREATED,
)
def transcribe_file(
    file_id: str,
    payload: AudioTranscriptionCreate,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> AudioTranscriptionView:
    _require_file_access(db, context, file_id, "read")
    return AudioTranscriptionView.model_validate(
        service(db, context, settings).transcribe(file_id, payload, idempotency_key)
    )


@router.get("/{file_id}/references", response_model=list[FileReferenceView])
def list_file_references(
    file_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[FileReferenceView]:
    _require_file_access(db, context, file_id, "read")
    return [
        FileReferenceView.model_validate(item)
        for item in service(db, context, settings).list_references(file_id)
    ]


@router.post(
    "/{file_id}/references",
    response_model=FileReferenceView,
    status_code=status.HTTP_201_CREATED,
)
def create_file_reference(
    file_id: str,
    payload: FileReferenceCreate,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> FileReferenceView:
    _require_file_access(db, context, file_id, "write")
    return FileReferenceView.model_validate(
        service(db, context, settings).add_reference(file_id, payload)
    )


@router.get("/{file_id}/delete-impact", response_model=DeleteImpact)
def file_delete_impact(
    file_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> DeleteImpact:
    _require_file_access(db, context, file_id, "delete")
    return service(db, context, settings).delete_impact(file_id)


@router.post("/batch-delete-impact", response_model=FileBatchDeleteImpact)
def file_batch_delete_impact(
    payload: FileBatchSelection,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> FileBatchDeleteImpact:
    for file_id in payload.file_ids:
        _require_file_access(db, context, file_id, "delete")
    return service(db, context, settings).batch_delete_impact(payload.file_ids)


@router.post("/batch-delete", response_model=FileBatchDeleteResponse)
def delete_files_batch(
    payload: FileBatchDeleteConfirm,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> FileBatchDeleteResponse:
    for file_id in payload.file_ids:
        _require_file_access(db, context, file_id, "delete")
    return service(db, context, settings).delete_batch(
        payload.file_ids, payload.confirmation_text
    )


@router.post("/{file_id}/delete", response_model=ActionResponse)
def delete_file_confirmed(
    file_id: str,
    payload: DeleteConfirm,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ActionResponse:
    _require_file_access(db, context, file_id, "delete")
    impact = service(db, context, settings).delete_confirmed(
        file_id, payload.confirmation_text
    )
    return ActionResponse(
        status="deleted",
        message="File, chunks, and reviewed reference links were deleted",
        resource_id=file_id,
        details={"impacts": [item.model_dump() for item in impact.impacts]},
    )


@router.delete("/{file_id}", response_model=ActionResponse)
def delete_file(file_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> ActionResponse:
    _require_file_access(db, context, file_id, "delete")
    service(db, context, settings).delete(file_id)
    return ActionResponse(status="deleted", message="File metadata and local object were deleted", resource_id=file_id)
