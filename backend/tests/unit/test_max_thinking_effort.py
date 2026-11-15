"""Maximum tier contracts: no network, credentials, generation or database writes."""
import pytest
from app.providers import models_dev
from app.providers.model_options import ModelCallOptions, catalog_capability_snapshot, model_capabilities_for_model, resolve_model_call_options, validate_model_capability_update
from app.providers.qwen_catalog import proportional_effort_mapping, qwen_model_defaults
from app.providers.remote.anthropic import AnthropicMessagesProvider
from app.providers.remote.ollama import ollama_think_value
from app.providers.thinking_metadata import automatic_thinking_metadata
from app.services.agent_execution_profiles import normalize_thinking_mode
from app.voice.policy import clip_thinking_mode


def record(values, toggle=False):
    return {"reasoning": True, "reasoning_options": ([{"type": "toggle"}] if toggle else []) + [{"type": "effort", "values": values}]}


def test_same_name_mapping_is_sparse_order_independent_and_never_compresses_high():
    assert proportional_effort_mapping(["max", "xhigh", "high", "medium", "low", "high"]) == {k:k for k in ("low", "medium", "high", "xhigh", "max")}
    assert proportional_effort_mapping(["max", "high"]) == {"high":"high", "max":"max"}
    assert proportional_effort_mapping(["unknown"]) == {}


@pytest.mark.parametrize("values", [["none", "low", "high", "max"], ["low", "medium", "high", "xhigh", "max"]])
def test_combined_toggle_and_effort_uses_all_controls(values):
    result = models_dev._thinking_overlay(record(values, True), {})
    assert result["thinking_mapping"]["max"] == "max"
    assert result["thinking_mapping"]["high"] == "high"
    assert result["thinking_required"] is False
    assert len(result["reasoning_controls"]) == 2


def test_hosted_reviewed_mapping_is_not_replaced_by_canonical_vendor_dialect():
    base = qwen_model_defaults("deepseek-v4-pro")
    assert base["reasoning_efforts"] == ["high", "max"]
    assert models_dev._thinking_overlay(record(["low", "high", "max"]), base).get("thinking_mapping") is None


def test_native_and_hosted_deepseek_remain_distinct(monkeypatch):
    monkeypatch.setattr(models_dev, "lookup_model", lambda _: record(["low", "high", "max"], True))
    native = catalog_capability_snapshot("deepseek-v4-pro", provider_type="deepseek_chat", dashscope_hosted=False)
    hosted = catalog_capability_snapshot("deepseek-v4-pro", provider_type="qwen", dashscope_hosted=True)
    assert native["reasoning_efforts"] == ["low", "high", "max"]
    assert hosted["reasoning_efforts"] == ["high", "max"]
    for caps in (native, hosted):
        validate_model_capability_update(caps)
        result = resolve_model_call_options({"models": {"deepseek-v4-pro": {**caps, "capability_source":"user_declared"}}}, "deepseek-v4-pro", thinking_mode="max")
        assert result.actual_reasoning_effort == "max"


def test_old_automatic_metadata_requires_persisted_evidence_and_preserves_user_mapping():
    current = {"reasoning_parameter":"reasoning_effort"}
    old = {"capability_source":"official_catalog", "reasoning_parameter":"reasoning_effort", "thinking_mapping":{"xhigh":"max"}}
    meta = automatic_thinking_metadata(current, old)
    assert meta["legacy_thinking_mapping"] == {"xhigh":"max"}
    assert automatic_thinking_metadata(current, {**old,"capability_source":"user_declared"}) == {"thinking_mapping_version":2}
    assert automatic_thinking_metadata({"reasoning_parameter":"thinking_budget"}, old) == {"thinking_mapping_version":2}
    assert automatic_thinking_metadata(current, {**old, **meta}) == meta


def test_explicit_xhigh_to_max_mapping_is_retained(monkeypatch):
    monkeypatch.setattr(models_dev, "lookup_model", lambda _: record(["low", "medium", "high", "xhigh", "max"]))
    explicit = {"capability_source":"user_declared", "reasoning_efforts":["xhigh"], "thinking_mapping":{"xhigh":"max"}, "reasoning_parameter":"reasoning_effort"}
    caps = model_capabilities_for_model({"models":{"m":explicit}}, "m")
    assert caps["thinking_mapping"] == {"xhigh":"max"}
    assert "legacy_thinking_mapping" not in caps


def test_max_is_not_an_agent_alias_and_voice_ceiling_is_independent():
    assert normalize_thinking_mode("max") == "max"
    assert clip_thinking_mode("max", "max") == "max"
    assert clip_thinking_mode("max", "xhigh") == "xhigh"
    assert ollama_think_value("max", "max") == "max"


def test_anthropic_max_uses_native_output_config():
    options = ModelCallOptions("max", "max", "reasoning_effort", "disabled", False, {})
    provider = AnthropicMessagesProvider(provider_id="test", model_id="claude-opus-4-7", base_url="https://relay.invalid", api_key="not-real", call_options=options)
    body = provider._apply_call_options({}, responses=False)
    assert body["thinking"] == {"type":"adaptive"}
    assert body["output_config"]["effort"] == "max"


def test_unknown_models_do_not_advertise_unconfirmed_max(monkeypatch):
    monkeypatch.setattr(models_dev, "lookup_model", lambda _: None)
    assert catalog_capability_snapshot("private-alias")["reasoning_efforts"] == []
