import copy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.core.errors import AppError
from app.domain.extension_models import MCPServer, SkillTranslationCache
from app.domain.schemas.extensions import McpMarketTranslateRequest, McpRegistryDetailResponse, McpRegistrySearchItem
from app.services.mcp_market import translation_source_hash
from app.services.mcp_market_translation import McpMarketTranslationService


@pytest.fixture
def translation(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    detail = McpRegistryDetailResponse(
        item=McpRegistrySearchItem(name="io.example/search", version="1.2.3", description="Search public pages", endpoint_url="https://example.org/mcp"),
        registry_url="https://registry.modelcontextprotocol.io",
        configuration=[{"name": "API_KEY", "description": "Service API key", "isRequired": True, "isSecret": True}],
        tools=[{"name": "search_web", "description": "Search a website", "inputSchema": {"type": "object", "description": "ORIGINAL SCHEMA"}}, {"name": "ping"}],
        raw={"description": "ORIGINAL JSON"},
    )
    calls, targets = [], []
    def generate(prompt, *args):
        fragments = [json.loads(prompt.split("\n")[-1])["text"]]
        calls.extend(fragments)
        return {"translated_text": "译文：" + fragments[0]}
    provider = SimpleNamespace(available=True, remote_capability=True, model_id="model-one", provider_id="provider-one", generate_json=generate)
    def resolve(*args, **kwargs):
        targets.append(kwargs)
        return provider
    monkeypatch.setattr("app.services.market_translation.model_provider_for_workspace", resolve)
    monkeypatch.setattr("app.services.mcp_market_translation.McpMarketService.detail", lambda *args, **kwargs: detail)
    with Session(engine) as db:
        yield McpMarketTranslationService(db, "test-workspace", "test-user", Settings(_env_file=None, storage_root=tmp_path)), detail, calls, targets
    engine.dispose()


def request(**kwargs):
    return McpMarketTranslateRequest(name="io.example/search", **kwargs)


def test_translates_descriptions_without_changing_registration_or_schema(translation):
    manager, detail, calls, targets = translation
    original = copy.deepcopy(detail)
    result = manager.translate(request(version="1.2.3", source_hash=translation_source_hash(detail), provider_id="selected-provider", model_id="selected-model"))
    assert result.target_locale == "zh-cn"
    assert result.description == "译文：Search public pages"
    assert result.configuration_descriptions == ["译文：Service API key"]
    assert result.tool_descriptions == ["译文：Search a website", ""]
    assert all("API_KEY" not in text and "example.org" not in text and "ORIGINAL" not in text for text in calls)
    assert detail == original
    assert manager.db.scalar(select(MCPServer)) is None
    assert targets[-1] == {"provider_id": "selected-provider", "model_id": "selected-model", "thinking_mode": "off"}


def test_cache_separates_catalog_and_invalidates_changed_text(translation):
    manager, detail, calls, _ = translation
    manager.translate(request())
    count = len(calls)
    assert manager.translate(request()).cached and len(calls) == count
    assert not manager.translate(request(catalog="smithery")).cached
    detail.tools[0]["description"] = "Updated purpose"
    assert not manager.translate(request()).cached
    assert manager.translate(request()).tool_descriptions[0] == "译文：Updated purpose"


def test_stale_or_reordered_source_is_rejected_before_model_call(translation):
    manager, detail, calls, targets = translation
    old_hash = translation_source_hash(detail)
    detail.tools.reverse()
    with pytest.raises(AppError) as exc:
        manager.translate(request(source_hash=old_hash))
    assert exc.value.code == "translation_source_changed"
    assert not calls and not targets
    assert manager.db.scalar(select(SkillTranslationCache)) is None


def test_resolved_version_must_match_the_version_on_screen(translation):
    manager, _, _, targets = translation
    with pytest.raises(AppError) as exc:
        manager.translate(request(version="9.0"))
    assert exc.value.status_code == 409 and not targets


def test_smithery_without_versions_supports_other_target_languages(translation):
    manager, detail, _, _ = translation
    detail.item.version = ""
    result = manager.translate(request(catalog="smithery", target_locale="ja"))
    assert result.version == "" and result.catalog == "smithery" and result.target_locale == "ja"
