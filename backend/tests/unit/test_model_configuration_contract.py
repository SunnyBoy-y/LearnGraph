"""User intent, catalogue defaults, and endpoint identity have one precedence."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.providers import factory, model_options, models_dev
from app.providers.model_catalog import unified_model_defaults
from app.providers.model_options import catalog_capability_snapshot, model_capabilities_for_model, resolve_model_call_options


def test_none_effort_is_preserved_and_unknown_prefix_is_not_a_catalogue_match(monkeypatch):
    monkeypatch.setattr(models_dev, "lookup_model", lambda _: {
        "reasoning": True, "reasoning_options": [{"type": "effort", "values": ["none", "low", "high"]}],
    })
    result = models_dev.capability_overlay("gpt-5-private", {})
    assert result["thinking_required"] is False
    assert result["thinking_mapping"]["off"] == "none"
    monkeypatch.setattr(models_dev, "lookup_model", lambda _: None)
    assert models_dev.capability_overlay("gpt-5-private", {}) == {}
    assert unified_model_defaults("gpt-5-private")["models_dev_known"] is False


def test_inferred_vendor_controls_do_not_leak_to_generic_protocol_defaults(monkeypatch):
    monkeypatch.setattr(models_dev, "lookup_model", lambda _: {"reasoning": True})
    defaults = unified_model_defaults("private-audit", provider_type="openai_compatible_chat", dashscope_hosted=False)
    assert defaults["reasoning_parameter"] == "reasoning_effort"
    assert defaults["thinking_mapping"]["off"] is None


def test_official_cached_controls_refresh_without_discovery_on_background_channels(monkeypatch):
    monkeypatch.setattr(model_options, "unified_model_defaults", lambda *a, **k: {
        "models_dev_known": True, "thinking_required": False, "thinking_mapping": {"off": "none"},
        "reasoning_parameter": "reasoning_effort",
    })
    result = model_capabilities_for_model({"models": {"m": {
        "capability_source": "official_catalog", "thinking_required": True,
        "thinking_mapping": {"off": None},
    }}}, "m")
    assert result["thinking_required"] is False
    assert result["thinking_mapping"]["off"] == "none"


def test_reasoning_false_cannot_be_overruled_by_model_name(monkeypatch):
    monkeypatch.setattr(models_dev, "lookup_model", lambda _: {"reasoning": False})
    for name in ("gpt-5-private", "claude-mythos-private"):
        result = models_dev.capability_overlay(name, {})
        assert result["thinking_required"] is False
        assert result["reasoning_efforts"] == []


@pytest.mark.parametrize("source,expected", [("user_declared", 64000), ("provider_probe", 64000), ("official_catalog", 128000)])
def test_explicit_model_wins_over_group_but_group_wins_over_catalogue(monkeypatch, source, expected):
    monkeypatch.setattr(model_options, "unified_model_defaults", lambda *a, **k: {"context_window_tokens": 256000})
    caps = {"model_defaults_enabled": True, "model_defaults": {"context_window_tokens": 128000},
            "models": {"private": {"context_window_tokens": 64000, "capability_source": source}}}
    assert model_capabilities_for_model(caps, "private")["context_window_tokens"] == expected
    caps["model_defaults_enabled"] = False
    assert model_capabilities_for_model(caps, "private")["context_window_tokens"] == (256000 if source == "official_catalog" else 64000)


def test_auxiliary_fast_preference_uses_a_supported_tier_without_weakening_explicit_off():
    caps = {"thinking_required": True, "reasoning_efforts": ["medium", "high"],
            "thinking_mapping": {"medium": "medium", "high": "high"}, "reasoning_parameter": "reasoning_effort"}
    with pytest.raises(model_options.ModelCapabilityError, match="only supports thinking"):
        resolve_model_call_options(caps, "private", thinking_mode="off")
    result = resolve_model_call_options(caps, "private", thinking_mode="off", prefer_fast=True, search_route="disabled")
    assert result.thinking_mode == "medium"
    assert result.actual_reasoning_effort == "medium"
    assert result.native_web_search is False


def test_group_template_cannot_change_endpoint_protocol(monkeypatch):
    monkeypatch.setattr(model_options, "unified_model_defaults", lambda *a, **k: {})
    result = model_capabilities_for_model({"protocol_family": "openai_compatible", "provider_family": "openai_responses",
        "model_defaults_enabled": True, "model_defaults": {"protocol_family": "dashscope"}}, "private")
    assert result["protocol_family"] == "openai_compatible"


@pytest.mark.parametrize("family,expected", [("openai_compatible", {}), ("dashscope", {"enable_thinking": False})])
def test_fast_fallback_never_guesses_another_gateways_private_field(family, expected):
    caps = {"protocol_family": family, "reasoning_parameter": "reasoning_effort", "thinking_required": False,
            "reasoning_efforts": ["low"], "thinking_mapping": {"off": None, "low": "low"}}
    result = resolve_model_call_options(caps, "private", thinking_mode="off", disable_thinking_fallback=True)
    assert result.provider_options == expected


@pytest.mark.parametrize("provider_type", ["openai_compatible_chat", "qwen", "openai_responses", "anthropic_messages",
                                         "deepseek_chat", "ollama", "ollama_cloud", "github_copilot"])
def test_every_factory_adapter_keeps_user_context_cap_and_model_tool_gate(monkeypatch, provider_type):
    snapshot = {**catalog_capability_snapshot("private-audit"), "context_window_tokens": 256000,
                "context_limit_tokens": 64000, "max_output_tokens": 8000, "supports_agent_tools": False,
                "capability_source": "user_declared", "thinking_required": False,
                "default_thinking_mode": "off", "thinking_mapping": {"off": None}, "reasoning_efforts": []}
    row = NS(id="p", provider_type=provider_type, base_url="https://relay.invalid/v1", remote_capability=True,
             capabilities={"default_model": "private-audit", "models": {"private-audit": snapshot}})
    monkeypatch.setattr(factory, "cached_first_provider_row", lambda *a, **k: row)
    monkeypatch.setattr(factory, "_secret_for_provider", lambda *a, **k: "test-key-not-real")
    provider = factory.model_provider_for_workspace(None, "audit", Settings(_env_file=None), provider_id="p")
    assert provider.available
    assert provider.context_window_tokens == 64000
    assert provider.capabilities["context_window_tokens"] == 256000
    assert provider.capabilities["context_limit_tokens"] == 64000
    assert provider.max_output_tokens <= 64000
    assert provider.supports_agent_tools is False
    if provider_type == "openai_compatible_chat":
        assert type(provider).__name__ == "OpenAICompatibleChatProvider"


@pytest.mark.parametrize("provider_type", ["openai_compatible_vision", "openai_responses_vision"])
def test_dedicated_vision_channels_use_the_same_model_snapshot_and_limits(monkeypatch, provider_type):
    snapshot = {**catalog_capability_snapshot("private-vision"), "supports_image_input": True,
                "capability_source": "user_declared", "context_window_tokens": 256000,
                "context_limit_tokens": 64000, "max_output_tokens": 8192,
                "thinking_required": True, "reasoning_efforts": ["low"],
                "thinking_mapping": {"low": "low"}, "default_thinking_mode": "low",
                "reasoning_parameter": "reasoning_effort"}
    row = NS(id="vision", provider_type=provider_type, base_url="https://relay.invalid/v1", remote_capability=True,
             capabilities={"default_vision_model_id": "private-vision", "models": {"private-vision": snapshot}})
    monkeypatch.setattr(factory, "cached_first_provider_row", lambda *a, **k: row)
    monkeypatch.setattr(factory, "_secret_for_provider", lambda *a, **k: "test-key-not-real")
    p = factory.vision_provider_for_workspace(None, "audit", Settings(_env_file=None), provider_id="vision")
    assert p.available
    assert p.context_window_tokens == 64000 and p.max_output_tokens == 8192
    assert p.call_options.thinking_mode == "low"
    row.capabilities["model_states"] = {"private-vision": False}
    assert not factory.vision_provider_for_workspace(None, "audit", Settings(_env_file=None), provider_id="vision").available


def test_shared_service_forwards_explicit_search_disabled_and_auxiliary_policy(monkeypatch):
    from app.services import chat_service_factory as assembly
    from unittest.mock import Mock
    context = NS(workspace_id="audit", workspace=NS(), principal=NS(user_id="u", tenant_id="t"))
    settings = NS(memory_context_builder_v2=False, memory_read_mode="events", memory_root="unused")
    captured = {}
    def create_model(*args, **kwargs):
        captured.update(kwargs)
        return NS()
    monkeypatch.setattr(assembly, "model_provider_for_workspace", create_model)
    for name in ("AuthorizationService", "MemoryService", "search_provider_for_workspace", "memory_provider_for_workspace", "vision_provider_for_workspace", "ChatService"):
        monkeypatch.setattr(assembly, name, Mock())
    assembly.build_chat_service(None, workspace_context=context, settings=settings,
                                thinking_mode="off", search_route="disabled", agent_mode=False, prefer_fast=True)
    assert captured == {"thinking_mode": "off", "search_route": "disabled", "prefer_fast": True}


def _management_service(monkeypatch):
    from app.services import management
    row = NS(id="p", provider_type="openai_compatible_chat", base_url="https://relay.invalid/v1", capabilities={})
    service = management.ProviderService.__new__(management.ProviderService)
    service.providers = NS(require=lambda *a: row)
    service.db = Mock()
    service.audit = Mock()
    service.workspace_id = "audit"
    service.actor_id = "test-actor"
    monkeypatch.setattr(management, "invalidate_provider_plan_cache", lambda *a, **k: None)
    return service, row


def test_edit_api_pins_catalogue_initialized_values_and_discovery_preserves_them(monkeypatch):
    service, row = _management_service(monkeypatch)
    snapshot = {**catalog_capability_snapshot("private-audit"), "context_window_tokens": 128000,
                "context_limit_tokens": 64000, "capability_source": "official_catalog"}
    service.update_model_capabilities("p", "private-audit", NS(model_dump=lambda: snapshot.copy()))
    assert row.capabilities["models"]["private-audit"]["capability_source"] == "user_declared"
    service._sync_model_catalog_defaults_impl("p", ["private-audit"])
    assert row.capabilities["models"]["private-audit"]["context_limit_tokens"] == 64000


def test_apply_all_updates_snapshots_in_place_without_erasing_metadata(monkeypatch):
    service, row = _management_service(monkeypatch)
    row.capabilities = {"default_model": "a", "discovered_model_ids": ["a", "b"],
                        "models": {"a": {"custom_note": "keep"}}}
    snapshot = {**catalog_capability_snapshot("private-audit"), "apply_to_all": True}
    service.update_model_group_capabilities("p", NS(model_dump=lambda: snapshot.copy()))
    assert set(row.capabilities["models"]) == {"a", "b"}
    assert row.capabilities["models"]["a"]["custom_note"] == "keep"
    assert row.capabilities["models"]["b"]["capability_source"] == "user_declared"
