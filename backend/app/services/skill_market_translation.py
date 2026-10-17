"""Translate public skill-market documentation without installing the skill."""
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.schemas.extensions import SkillMarketTranslateRequest, SkillMarketTranslateResponse
from app.services.market_translation import MarketTranslationService
from app.services.skill_clawhub import ClawHubSkillMarket


class SkillMarketTranslationService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str, settings: Settings) -> None:
        self.db, self.workspace_id, self.actor_id, self.settings = db, workspace_id, actor_id, settings

    def translate(self, payload: SkillMarketTranslateRequest) -> SkillMarketTranslateResponse:
        detail = ClawHubSkillMarket(self.db, self.workspace_id, self.actor_id, self.settings).detail(
            payload.market_id, version=payload.version,
        )
        if detail.instructions_error:
            raise AppError(502, "translation_source_unavailable", "完整说明尚未读取成功，请先重试说明。")
        result = MarketTranslationService(self.db, self.workspace_id, self.actor_id, self.settings).translate_fields(
            {field: getattr(detail, field) for field in ("description", "instructions_markdown", "changelog")},
            cache_namespace=f"market/{detail.market_id}@{detail.version}", target_locale=payload.target_locale,
            provider_id=payload.provider_id, model_id=payload.model_id,
            action="skill.market.translate", resource_type="skill_market", resource_id=detail.market_id,
        )
        return SkillMarketTranslateResponse(
            market_id=detail.market_id, version=detail.version, target_locale=result.target_locale,
            translator_model_id=result.translator_model_id, cached=result.cached, **result.texts,
        )
