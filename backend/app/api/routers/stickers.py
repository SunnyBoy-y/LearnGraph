from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.services.stickers import MAX_IMPORT_BYTES, StickerService
from app.services.sticker_market import StickerMarketService, asset_bytes, market_archive, market_pack
from app.services.sticker_pipeline import StickerPipeline


router = APIRouter(prefix="/stickers", tags=["stickers"])


class StickerSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["follow", "independent", "keyword"] | None = None
    provider_id: str | None = Field(default=None, max_length=36)
    model_id: str | None = Field(default=None, max_length=160)
    ai_enabled: bool | None = None


class StickerUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    semantics: dict[str, Any]


class PackUpdate(BaseModel):
    enabled: bool


class PipelineUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_analyze: bool


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: Literal[True]


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> StickerService:
    return StickerService(db, context.workspace_id, context.principal.user_id, settings)


@router.get("")
def status(db: DB, context: CurrentWorkspace, settings: AppSettings):
    return service(db, context, settings).status()


@router.get("/market")
def market(db: DB, context: CurrentWorkspace, settings: AppSettings,
           q: Annotated[str, Query(max_length=200)] = ""):
    return StickerMarketService(service(db, context, settings)).listing(q)


def zip_response(content: bytes, filename: str) -> Response:
    return Response(content=content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}.zip"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/market/{pack_id}/download")
def download_market(pack_id: str, context: CurrentWorkspace):
    return zip_response(market_archive(pack_id), market_pack(pack_id)["id"])


@router.get("/market/{pack_id}/preview/{index}")
def preview_market(pack_id: str, index: int, context: CurrentWorkspace):
    entries = market_pack(pack_id)["stickers"]
    if index < 0 or index >= len(entries):
        raise AppError(404, "sticker_market_not_found", "表情不存在")
    entry = entries[index]
    return Response(content=asset_bytes(entry["file"], entry["sha256"]), media_type="image/png", headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.post("/market/{pack_id}/install", status_code=201)
def install_market(pack_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return StickerMarketService(service(db, context, settings)).install(pack_id)


@router.get("/packs/{pack_id}/download")
def download_pack(pack_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return zip_response(StickerMarketService(service(db, context, settings)).export(pack_id), "learngraph-stickers")


@router.get("/pipeline")
def pipeline(db: DB, context: CurrentWorkspace, settings: AppSettings):
    return StickerPipeline(service(db, context, settings)).status()


@router.put("/pipeline")
def configure_pipeline(payload: PipelineUpdate, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return StickerPipeline(service(db, context, settings)).configure(payload.auto_analyze)


@router.post("/packs/{pack_id}/analyze", status_code=202)
def analyze_pack(pack_id: str, payload: AnalysisRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return StickerPipeline(service(db, context, settings)).enqueue(pack_id)


@router.put("/settings")
def configure(payload: StickerSettingsUpdate, db: DB, context: CurrentWorkspace, settings: AppSettings):
    context.require_permission("workspace.manage")
    return service(db, context, settings).configure(payload.model_dump(exclude_none=True))


@router.get("/search")
def search(db: DB, context: CurrentWorkspace, settings: AppSettings,
           q: Annotated[str, Query(max_length=600)] = "",
           limit: Annotated[int, Query(ge=1, le=24)] = 24,
           pack_id: Annotated[str | None, Query(max_length=36)] = None,
           offset: Annotated[int, Query(ge=0, le=100000)] = 0,
           semantic: bool = False):
    return service(db, context, settings).search(q, limit, semantic=semantic, pack_id=pack_id, offset=offset)


@router.post("/rebuild", status_code=202)
def rebuild(db: DB, context: CurrentWorkspace, settings: AppSettings):
    context.require_permission("workspace.manage")
    current = service(db, context, settings)
    current.reconcile(force=True)
    return current.status()


@router.post("/import", status_code=201)
async def import_pack(db: DB, context: CurrentWorkspace, settings: AppSettings,
                      files: Annotated[list[UploadFile], File()],
                      name: Annotated[str, Form(max_length=120)] = ""):
    if len(files) > 100:
        raise AppError(422, "sticker_import_invalid", "每次最多导入 100 个文件")
    contents = []
    total = 0
    for upload in files:
        content = await upload.read(MAX_IMPORT_BYTES - total + 1)
        total += len(content)
        if total > MAX_IMPORT_BYTES:
            raise AppError(413, "sticker_import_too_large", "导入总大小不能超过 24 MiB")
        contents.append((upload.filename or "image", content))
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(service(db, context, settings).import_pack, contents, name)


@router.put("/packs/{pack_id}")
def update_pack(pack_id: str, payload: PackUpdate, db: DB, context: CurrentWorkspace, settings: AppSettings):
    current = service(db, context, settings)
    from app.domain.models import StickerPack
    pack = db.get(StickerPack, pack_id)
    if pack and pack.builtin:
        context.require_permission("workspace.manage")
    current.set_pack_enabled(pack_id, payload.enabled)
    return current.status()


@router.put("/{sticker_id}")
def update_asset(sticker_id: str, payload: StickerUpdate, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return service(db, context, settings).update_asset(sticker_id, payload.model_dump())


@router.get("/{sticker_id}")
def get_asset(sticker_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    current = service(db, context, settings)
    return current.view(current.require_asset(sticker_id))


@router.post("/{sticker_id}/analyze", status_code=202)
def analyze_asset(sticker_id: str, payload: AnalysisRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return StickerPipeline(service(db, context, settings)).enqueue(sticker_id=sticker_id)


@router.get("/{sticker_id}/content")
def content(sticker_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings,
            poster: bool = False):
    data, mime_type = service(db, context, settings).content(sticker_id, poster)
    return Response(content=data, media_type=mime_type, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
