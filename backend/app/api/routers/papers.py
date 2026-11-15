"""Explicit scholarly endpoints; do not mutate the chat search-route setting."""
from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.models import SourceCitation, SourceRecord
from app.domain.schemas.papers import PaperExcerpt, PaperReadRequest, PaperReadResponse, PaperSaveRequest, PaperSearchRequest, PaperSearchResponse
from app.domain.schemas.sources import SourceRecordView
from app.services.authorization import AuthorizationService
from app.services.papers import PaperService

router = APIRouter(prefix="/papers", tags=["papers"])


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> PaperService:
    authorization = AuthorizationService(db, context.principal)
    return PaperService(db, context.workspace_id, context.principal.user_id, settings,
        source_access=lambda source_id, action: authorization.can_access_resource(context.workspace, "source", source_id, action))


@router.post("/search", response_model=PaperSearchResponse)
def search_papers(payload: PaperSearchRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return service(db, context, settings).search(payload)


@router.post("/read", response_model=PaperReadResponse)
def read_paper(payload: PaperReadRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    """Read publicly accessible PDF text; no automatic collection or saving."""
    return service(db, context, settings).read(payload)


@router.post("/save", response_model=SourceRecordView)
def save_paper(payload: PaperSaveRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    """Explicit save to existing sources; graph association stays a separate action."""
    return service(db, context, settings).save(payload)


@router.get("/library", response_model=list[SourceRecordView])
def paper_library(db: DB, context: CurrentWorkspace, settings: AppSettings):
    return service(db, context, settings).library()


def require_paper_source(source_id: str, db: DB, context: CurrentWorkspace):
    source = db.scalar(select(SourceRecord).where(SourceRecord.id == source_id, SourceRecord.workspace_id == context.workspace_id))
    if source is None or source.metadata_json.get("kind") != "paper" or not AuthorizationService(db, context.principal).can_access_resource(context.workspace, "source", source_id, "read"):
        raise AppError(404, "source_not_found", "Source was not found")
    return source


@router.get("/library/{source_id}/bibtex")
def export_bibtex(source_id: str, db: DB, context: CurrentWorkspace):
    record = require_paper_source(source_id, db, context)
    return Response(str(record.metadata_json.get("bibtex") or ""), media_type="application/x-bibtex",
                    headers={"Content-Disposition": f'attachment; filename="paper-{record.id}.bib"'})


@router.get("/library/{source_id}/citations", response_model=list[PaperExcerpt])
def paper_citations(source_id: str, db: DB, context: CurrentWorkspace):
    require_paper_source(source_id, db, context)
    rows = db.scalars(select(SourceCitation).where(SourceCitation.workspace_id == context.workspace_id,
        SourceCitation.source_id == source_id).order_by(SourceCitation.created_at, SourceCitation.id)).all()
    return sorted([PaperExcerpt(locator=r.locator, content=r.quote) for r in rows],
                  key=lambda r: int(r.locator.split(":", 1)[1]) if r.locator.startswith("page:") and r.locator.split(":", 1)[1].isdigit() else 0)
