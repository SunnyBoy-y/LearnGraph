"""View-only translation of public MCP catalog descriptions."""
import hashlib

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.schemas.extensions import McpMarketTranslateRequest, McpMarketTranslateResponse
from app.services.market_translation import MarketTranslationService
from app.services.mcp_market import McpMarketService, translation_source, translation_source_hash


class McpMarketTranslationService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str, settings: Settings) -> None:
        self.db, self.workspace_id, self.actor_id, self.settings = db, workspace_id, actor_id, settings

    def translate(self, payload: McpMarketTranslateRequest) -> McpMarketTranslateResponse:
        detail = McpMarketService(self.db, self.workspace_id, self.actor_id, self.settings).detail(
            payload.name, catalog=payload.catalog, version=payload.version,
        )
        source_hash = translation_source_hash(detail)
        if (payload.source_hash and source_hash != payload.source_hash) or (
            payload.version != "latest" and payload.version != detail.item.version
        ):
            raise AppError(409, "translation_source_changed", "目录说明或版本已更新，请重新打开详情后翻译。")
        identity = hashlib.sha256(f"{payload.name}@{detail.item.version}".encode()).hexdigest()
        result = MarketTranslationService(self.db, self.workspace_id, self.actor_id, self.settings).translate_fields(
            translation_source(detail), cache_namespace=f"mcp-market/{payload.catalog}/{identity}",
            target_locale=payload.target_locale, provider_id=payload.provider_id, model_id=payload.model_id,
            action="mcp.market.translate", resource_type="mcp_market", resource_id=payload.name,
        )
        return McpMarketTranslateResponse(
            name=detail.item.name, catalog=payload.catalog, version=detail.item.version, source_hash=source_hash,
            target_locale=result.target_locale, translator_model_id=result.translator_model_id, cached=result.cached,
            description=result.texts["description"],
            configuration_descriptions=[result.texts[f"configuration:{i}:{row.get('name') or ''}"] for i, row in enumerate(detail.configuration)],
            tool_descriptions=[result.texts[f"tools:{i}:{row.get('name') or ''}"] for i, row in enumerate(detail.tools)],
        )
