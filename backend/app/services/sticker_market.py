from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.domain.models import StickerAsset, StickerPack
from app.services.stickers import ASSET_ROOT, StickerService, digest, normalized


def catalog() -> list[dict[str, Any]]:
    return json.loads((ASSET_ROOT / "market.json").read_text(encoding="utf-8"))


def market_pack(pack_id: str) -> dict[str, Any]:
    pack = next((entry for entry in catalog() if entry["id"] == pack_id), None)
    if pack is None:
        raise AppError(404, "sticker_market_not_found", "市场表情包不存在")
    return pack


def asset_bytes(filename: str, expected_hash: str | None = None) -> bytes:
    path = (ASSET_ROOT / filename).resolve()
    if not path.is_relative_to(ASSET_ROOT.resolve()) or not path.is_file():
        raise AppError(503, "sticker_market_asset_unavailable", "表情素材暂不可用")
    content = path.read_bytes()
    if expected_hash and hashlib.sha256(content).hexdigest() != expected_hash:
        raise AppError(503, "sticker_market_integrity_failed", "表情素材校验失败")
    return content


def archive_bytes(manifest: dict[str, Any], files: list[tuple[str, bytes]], license_text: str, notice: str = "") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("LICENSE.txt", license_text)
        archive.writestr("NOTICE.txt", notice or str(manifest.get("attribution") or manifest.get("license") or "用户私有导入；未核实公开再分发许可"))
        archive.writestr("README.txt", "LearnGraph 表情包。保留 manifest.json 与许可证。私有导出不授予公开再分发许可。\n语义描述仅为辅助理解，实际含义取决于对话语境。\n")
        for filename, content in files:
            archive.writestr(filename, content)
    return output.getvalue()


def market_archive(pack_id: str) -> bytes:
    pack = market_pack(pack_id)
    files = [(entry["file"], asset_bytes(entry["file"], entry["sha256"])) for entry in pack["stickers"]]
    return archive_bytes(pack, files, asset_bytes(pack["license_file"]).decode("utf-8"), market_notice(pack))


def market_notice(pack: dict[str, Any]) -> str:
    upstream_notice = asset_bytes(pack["notice_file"]).decode("utf-8") if pack.get("notice_file") else ""
    credits = "\n".join(f"{entry['file']}: {entry.get('author', pack['author'])}; upstream: {entry['upstream']}; SHA-256: {entry['sha256']}" for entry in pack["stickers"])
    return f"{pack.get('attribution', pack['author'])}\nSource: {pack['source']}\nLicense: {pack.get('license_url', pack['license_id'])}\nOriginal PNG images unchanged. Resized posters may be used for display. Chinese semantic annotations: LearnGraph.\n\n{upstream_notice}\n{credits}\n"


class StickerMarketService:
    def __init__(self, stickers: StickerService):
        self.stickers = stickers
        self.db = stickers.db

    def listing(self, query: str = "") -> dict[str, Any]:
        self.stickers.ensure_builtin()
        installed = list(self.db.scalars(select(StickerPack).where(
            StickerPack.workspace_id == self.stickers.workspace_id,
            or_(StickerPack.builtin.is_(True), StickerPack.owner_id == self.stickers.actor_id),
        )).all())
        result = []
        for pack in catalog():
            if normalized(query) not in normalized(" ".join([pack["name"], pack["description"], pack["category"], *[entry["name"] + " " + " ".join(entry.get("tags", [])) for entry in pack["stickers"]]])):
                continue
            source_key = digest(sorted(entry["sha256"] for entry in pack["stickers"]))
            current = next((row for row in installed if row.source_key == source_key or (pack.get("builtin") and row.builtin and row.source_key == "fluent-v1")), None)
            result.append({
                **{key: pack[key] for key in ("id", "name", "description", "category", "author", "version", "source", "license", "license_id", "style", "source_id", "license_url", "attribution", "approval_required")},
                "count": len(pack["stickers"]), "bytes": sum((ASSET_ROOT / entry["file"]).stat().st_size for entry in pack["stickers"]),
                "installed_pack_id": current.id if current else None, "enabled": current.enabled if current else False,
                "previews": [{"name": entry["name"], "index": index, "description": entry["description"]} for index, entry in enumerate(pack["stickers"])],
            })
        return {"packs": result, "total_packs": len(catalog()), "total_assets": sum(len(pack["stickers"]) for pack in catalog()), "sources": sorted({pack["source_id"] for pack in catalog()}), "policy": "无需申请 API Key 或厂商接入审批；仍须保留许可、署名并遵守各素材条款。用户导入不会发布到市场，不抓取未授权商店。"}

    def install(self, pack_id: str) -> dict[str, Any]:
        pack = market_pack(pack_id)
        if pack.get("builtin"):
            self.stickers.ensure_builtin()
            current = next(row for row in self.listing()["packs"] if row["id"] == pack_id)
            return {"pack_id": current["installed_pack_id"], "imported": 0, "duplicates": len(pack["stickers"]), "private": False}
        content = market_archive(pack_id)
        try:
            result = self.stickers.import_pack([(f"{pack_id}.zip", content)], pack["name"])
        except IntegrityError:
            self.db.rollback()
            result = self.stickers.import_pack([(f"{pack_id}.zip", content)], pack["name"])
        if result["imported"]:
            installed = self.db.get(StickerPack, result["pack_id"])
            installed.source = pack["source"]
            installed.license = pack["license"]
            self.db.commit()
        return result

    def export(self, pack_id: str) -> bytes:
        pack = self.db.scalar(select(StickerPack).where(
            StickerPack.id == pack_id, StickerPack.workspace_id == self.stickers.workspace_id,
            or_(StickerPack.builtin.is_(True), StickerPack.owner_id == self.stickers.actor_id),
        ))
        if pack is None:
            raise AppError(404, "sticker_pack_not_found", "表情包不存在")
        assets = list(self.db.scalars(select(StickerAsset).where(StickerAsset.pack_id == pack.id, StickerAsset.workspace_id == self.stickers.workspace_id).order_by(StickerAsset.id)).all())
        from app.domain.models import WorkspaceSetting
        record = self.db.scalar(select(WorkspaceSetting).where(WorkspaceSetting.workspace_id == self.stickers.workspace_id, WorkspaceSetting.key == f"stickers.pack_metadata:{pack.id}"))
        provenance = record.value if record else {}
        manifest = {**{key: provenance[key] for key in ("attribution", "license_id", "license_url", "original_source") if key in provenance}, "name": pack.name, "source": pack.source, "license": pack.license, "private": not pack.builtin, "stickers": []}
        files = []
        for index, asset in enumerate(assets):
            suffix = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}[asset.mime_type]
            filename = f"sticker-{index + 1}.{suffix}"
            content = base64.b64decode(asset.asset_base64) if asset.asset_base64 else asset_bytes(asset.asset_path, asset.content_hash)
            files.append((filename, content))
            original = next((entry for source in catalog() for entry in source["stickers"] if entry["sha256"] == asset.content_hash), {})
            manifest["stickers"].append({**asset.semantics, "name": asset.name, "file": filename, "sha256": asset.content_hash, "upstream": original.get("upstream"), "author": original.get("author")})
        license_text = pack.license
        known = next((entry for entry in catalog() if pack.source == entry["source"] and {asset.content_hash for asset in assets} == {item["sha256"] for item in entry["stickers"]}), None)
        notice = str(provenance.get("notice") or "")
        if known:
            license_text = asset_bytes(known["license_file"]).decode("utf-8")
            notice = market_notice(known)
            manifest.update({key: known[key] for key in ("attribution", "license_id", "license_url")})
        return archive_bytes(manifest, files, license_text, notice)
