from app.providers import models_dev
from datetime import datetime, timedelta, timezone

import httpx
import pytest


def test_models_dev_keeps_native_vendor_currency_and_exact_cache_zero(monkeypatch):
    raw = {
        "openai": {"models": {"shared": {"id": "shared", "cost": {"input": "0.1234567890123456789", "output": 2, "cache_read": 0}, "limit": {"context": 20000}}}},
        "regional": {"currency": "CNY", "models": {"shared": {"id": "shared", "cost": {"input": 7, "output": 9, "cache_read": 0,
            "tiers": [{"tier": {"type": "context", "size": 10000}, "input": 11, "output": 13}]}}}},
        "relay": {"models": {"shared": {"id": "shared", "cost": {"currency": "USD", "input": 3, "output": 4}}}},
    }
    snapshot = models_dev.build_snapshot(raw, origin="test", fetched_at="2026-10-11")
    monkeypatch.setattr(models_dev, "get_snapshot", lambda: snapshot)
    entries = models_dev.pricing_entries()
    assert len(entries) == 4
    assert len({entry["catalog_id"] for entry in entries}) == 4
    usd = models_dev.price_entry_for_model("shared", 100, provider_key="openai")
    cny = models_dev.price_entry_for_model("shared", 100, provider_key="regional")
    assert usd["currency"] == "USD" and usd["native_input_per_million"] == "0.1234567890123456789"
    assert cny["currency"] == "CNY" and cny["native_input_per_million"] == "7" and cny["native_cached_input_per_million"] == "0"
    assert models_dev.price_entry_for_model("shared", 10001, provider_key="regional")["native_input_per_million"] == "11"
    assert models_dev.price_entry_for_model("shared", 100, provider_key="regional", currency="USD") is None
    assert models_dev.price_entry_for_model("shared", 100, provider_key="unknown") is None


def test_old_offline_snapshot_is_bound_to_its_actual_provider(monkeypatch):
    monkeypatch.setattr(models_dev, "get_snapshot", lambda: {"models": {"shared": {"id": "shared", "provider": "openai", "cost": {"input": 1, "output": 2}}}})
    assert models_dev.price_entry_for_model("shared", 1, provider_key="openai")["currency"] == "USD"
    assert models_dev.price_entry_for_model("shared", 1, provider_key="relay") is None


def test_network_catalog_keeps_exact_prices_after_cache_reload(monkeypatch, tmp_path):
    import httpx

    payload = b'''{"openai":{"models":{
      "precise":{"id":"precise","cost":{"input":0.1234567890123456789,"output":2,"cache_read":0}},
      "free":{"id":"free","cost":{"input":0,"output":0}},
      "incomplete":{"id":"incomplete","cost":{"input":1}}
    }}}'''
    response = httpx.Response(200, content=payload, request=httpx.Request("GET", models_dev.MODELS_DEV_URL))
    monkeypatch.setattr(models_dev.httpx, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(models_dev, "_cache_path", lambda: tmp_path / "catalog.json")
    monkeypatch.setattr(models_dev, "_snapshot", None)

    models_dev.refresh_snapshot()
    # Reload the saved JSON, exercising the normal startup cache path too.
    models_dev._snapshot = None
    precise = models_dev.price_entry_for_model("precise", 1, provider_key="openai")
    assert precise["native_input_per_million"] == "0.1234567890123456789"
    assert precise["native_cached_input_per_million"] == "0"
    assert models_dev.price_entry_for_model("free", 1, provider_key="openai")["native_input_per_million"] == "0"
    assert models_dev.price_entry_for_model("incomplete", 1, provider_key="openai") is None


def test_bundled_prices_are_not_used_for_billing(monkeypatch):
    snapshot = models_dev.build_snapshot({"openai": {"models": {
        "bundled": {"id": "bundled", "cost": {"input": 99, "output": 99}}
    }}}, origin="bundled", fetched_at="2026-01-01")
    monkeypatch.setattr(models_dev, "get_snapshot", lambda: snapshot)
    assert models_dev.lookup_model("bundled") is not None  # Offline capabilities remain available.
    assert models_dev.pricing_entries() == []
    assert models_dev.price_entry_for_model("bundled", 1, provider_key="openai") is None
    assert models_dev.snapshot_status()["priced_model_count"] == 0
    assert models_dev.snapshot_status()["stale"] is True


@pytest.mark.parametrize("age_hours, stale", [(23, False), (25, True)])
def test_refresh_is_due_after_one_day(monkeypatch, age_hours, stale):
    fetched = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    monkeypatch.setattr(models_dev, "get_snapshot", lambda: {
        "_meta": {"origin": "network_cache", "fetched_at": fetched.isoformat()}, "models": {},
    })
    status = models_dev.snapshot_status()
    assert status["stale"] is stale
    assert status["next_refresh_at"] == (fetched + timedelta(days=1)).isoformat()


@pytest.mark.parametrize("models", [{}, {"bad": {"id": "bad", "cost": {"input": 1}}},
    {"bad": {"id": "bad", "cost": {"input": -1, "output": 2}}}])
def test_invalid_network_response_preserves_last_good_prices(monkeypatch, tmp_path, models):
    previous = {"models": {"known": {"id": "known"}}, "_meta": {"origin": "network"}}
    cache = tmp_path / "catalog.json"
    cache.write_text('{"last":"good"}', encoding="utf-8")
    monkeypatch.setattr(models_dev, "_cache_path", lambda: cache)
    monkeypatch.setattr(models_dev, "_snapshot", previous)
    response = httpx.Response(200, json={"openai": {"models": models}}, request=httpx.Request("GET", models_dev.MODELS_DEV_URL))
    monkeypatch.setattr(models_dev.httpx, "get", lambda *args, **kwargs: response)
    with pytest.raises(ValueError, match="empty"):
        models_dev.refresh_snapshot()
    assert models_dev._snapshot is previous
    assert cache.read_text(encoding="utf-8") == '{"last":"good"}'


def test_another_workers_cache_update_is_loaded(monkeypatch, tmp_path):
    import json

    cache = tmp_path / "catalog.json"
    monkeypatch.setattr(models_dev, "_cache_path", lambda: cache)
    monkeypatch.setattr(models_dev, "_snapshot", {"models": {"old": {"id": "old"}}})
    monkeypatch.setattr(models_dev, "_snapshot_cache_mtime", None)
    cache.write_text(json.dumps({"models": {"new": {"id": "new"}}, "_meta": {"origin": "network"}}), encoding="utf-8")
    assert models_dev.lookup_model("new")["id"] == "new"
    assert models_dev.lookup_model("old") is None
    assert models_dev.get_snapshot()["_meta"]["origin"] == "network_cache"
