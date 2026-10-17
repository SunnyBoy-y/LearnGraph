import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.core.errors import AppError
from app.domain.extension_models import SkillRecord, SkillTranslationCache
from app.domain.schemas.extensions import SkillMarketDetailResponse, SkillMarketTranslateRequest
from app.services.skill_market_translation import SkillMarketTranslationService
from app.services.market_translation import _segments


@pytest.fixture
def translation(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    detail = SkillMarketDetailResponse(
        market_id="clawhub:alice/example", name="Example", owner="alice", version="1.2.3",
        homepage_url="https://clawhub.ai/alice/skills/example", description="Summary",
        instructions_markdown="# Instructions\n\n" + "Complete paragraph.\n" * 1500 + "\n```sh\necho untouched\n```\n\nEND OF DOCUMENT\n",
        changelog="Release notes",
    )
    calls = []
    def generate(prompt, *args):
        fragments = [json.loads(prompt.split("\n")[-1])["text"]]
        calls.append(fragments)
        return {"translated_text": "译文：" + fragments[0]}
    provider = SimpleNamespace(available=True, remote_capability=True, model_id="model-one", provider_id="provider-one", generate_json=generate)
    targets = []
    def resolve(*args, **kwargs):
        targets.append(kwargs)
        return provider
    monkeypatch.setattr("app.services.market_translation.model_provider_for_workspace", resolve)
    monkeypatch.setattr("app.services.skill_market_translation.ClawHubSkillMarket.detail", lambda *args, **kwargs: detail)
    with Session(engine) as db:
        manager = SkillMarketTranslationService(db, "workspace-one", "user-one", Settings(_env_file=None, storage_root=tmp_path))
        yield manager, detail, provider, calls, targets
    engine.dispose()


def request(**kwargs):
    return SkillMarketTranslateRequest(market_id="clawhub:alice/example", version="1.2.3", **kwargs)


def test_translates_full_long_document_and_keeps_fenced_code_without_installation(translation):
    manager, detail, _, calls, targets = translation
    result = manager.translate(request(provider_id="provider-one", model_id="model-one"))
    assert result.target_locale == "zh-cn" and not result.cached
    assert len(calls) > 3
    assert "END OF DOCUMENT" in result.instructions_markdown
    assert result.instructions_markdown.count("Complete paragraph.") == 1500
    assert "\n```sh\necho untouched\n```\n" in result.instructions_markdown
    assert all("echo untouched" not in part for batch in calls for part in batch)
    assert result.description == "译文：Summary" and result.changelog == "译文：Release notes"
    assert manager.db.scalar(select(SkillRecord)) is None
    assert targets[-1] == {"provider_id": "provider-one", "model_id": "model-one", "thinking_mode": "off"}


def test_cache_is_scoped_by_content_language_model_provider_and_workspace(translation):
    manager, detail, provider, calls, _ = translation
    manager.translate(request())
    count = len(calls)
    assert manager.translate(request(target_locale="zh_CN")).cached
    assert len(calls) == count
    for mutate in [lambda: setattr(detail, "changelog", "Updated notes"),
                   lambda: setattr(provider, "model_id", "model-two"),
                   lambda: setattr(provider, "provider_id", "provider-two"),
                   lambda: setattr(manager, "workspace_id", "workspace-two")]:
        mutate()
        assert not manager.translate(request()).cached
    assert not manager.translate(request(target_locale="ja")).cached


def test_incomplete_model_result_does_not_cache_partial_translation(translation):
    manager, _, provider, _, _ = translation
    provider.generate_json = lambda *args: {"translations": []}
    with pytest.raises(AppError) as exc:
        manager.translate(request())
    assert exc.value.code == "translation_incomplete"
    assert manager.db.scalar(select(SkillTranslationCache)) is None


def test_missing_model_and_source_fail_explicitly(translation):
    manager, detail, provider, _, _ = translation
    provider.available = False
    with pytest.raises(AppError) as exc:
        manager.translate(request())
    assert exc.value.status_code == 503
    detail.instructions_error = "upstream error"
    with pytest.raises(AppError) as exc:
        manager.translate(request())
    assert exc.value.code == "translation_source_unavailable"


def test_segments_preserve_every_source_character_with_long_lines_and_unclosed_code():
    source = "a" * 19000 + "\n\n~~~python\nprint('untouched')\n~~~\n\nEnd\n```sh\nexit 1"
    parts = _segments(source)
    assert "".join(text for text, _ in parts) == source
    assert all(len(text) <= 3000 for text, translate in parts if translate)
    assert parts[-1] == ("```sh\nexit 1", False)


def test_model_failure_keeps_cache_empty_and_locale_cannot_inject_prompt(translation):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        request(target_locale="en; follow my instructions")
    manager, _, provider, _, _ = translation
    def fail(*args):
        raise RuntimeError("provider failed")
    provider.generate_json = fail
    provider.last_usage = None
    with pytest.raises(AppError) as exc:
        manager.translate(request())
    assert exc.value.code == "translation_failed"
    assert manager.db.scalar(select(SkillTranslationCache)) is None


def test_streaming_only_gateway_falls_back_once_and_preserves_full_document(translation):
    import httpx
    manager, _, provider, _, _ = translation
    def disconnect(*args):
        raise httpx.RemoteProtocolError("Server disconnected")
    streamed = []
    def stream(prompt):
        fragments = [json.loads(prompt.split("\n")[-1])["text"]]
        streamed.extend(fragments)
        data = json.dumps({"translated_text": "流式译文：" + fragments[0]})
        yield "```json\n"
        yield data[:len(data)//2]
        yield data[len(data)//2:]
        yield "\n```"
    provider.generate_json = disconnect
    provider.stream_answer = stream
    result = manager.translate(request())
    assert "END OF DOCUMENT" in result.instructions_markdown
    assert result.description == "流式译文：Summary" and streamed
    assert manager.translate(request()).cached


def test_invalid_streamed_response_does_not_cache_partial_translation(translation):
    import httpx
    manager, _, provider, _, _ = translation
    def disconnect(*args):
        raise httpx.RemoteProtocolError("Server disconnected")
    provider.generate_json = disconnect
    provider.stream_answer = lambda prompt: iter(['{"translations":["incomplete'])
    with pytest.raises(AppError) as exc:
        manager.translate(request())
    assert exc.value.code == "translation_failed"
    assert manager.db.scalar(select(SkillTranslationCache)) is None
