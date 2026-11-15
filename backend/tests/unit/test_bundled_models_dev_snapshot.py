"""Offline checks for the checked-in catalogue and its explicit update tool."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from app.providers import models_dev
from app.providers.model_options import catalog_capability_snapshot, validate_model_capability_update
from scripts import update_models_dev_snapshot as updater


def test_committed_snapshot_is_a_consistent_offline_catalogue(monkeypatch):
    snapshot = json.loads(updater.BUNDLED_PATH.read_text(encoding="utf-8"))
    meta = snapshot["_meta"]
    models = snapshot["models"]
    assert meta["source"] == models_dev.MODELS_DEV_URL
    assert meta["origin"] == "bundled"
    assert meta["provider_count"] > 0
    assert meta["model_count"] == len(models) > 0
    assert len(meta["source_sha256"]) == 64
    assert "pricing_records" not in snapshot
    assert list(models) == sorted(models)
    assert all(key == key.strip().casefold() and isinstance(value, dict) for key, value in models.items())
    monkeypatch.setattr(models_dev, "get_snapshot", lambda: snapshot)
    assert models_dev.pricing_entries() == []


def test_all_committed_models_produce_valid_capability_snapshots(monkeypatch):
    snapshot = json.loads(updater.BUNDLED_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(models_dev, "get_snapshot", lambda: snapshot)
    for model_id in snapshot["models"]:
        try:
            validate_model_capability_update(catalog_capability_snapshot(model_id))
        except ValueError as exc:
            pytest.fail(f"Invalid bundled defaults for {model_id}: {exc}")


def test_update_preserves_capabilities_and_exact_decimals_without_tariff(monkeypatch, tmp_path):
    target = tmp_path / "snapshot.json"
    target.write_text(json.dumps({"_meta": {"model_count": 1}, "models": {"old": {"id": "old"}}}), encoding="utf-8")
    payload = b'''{"openai":{"models":{"new":{"id":"new",
        "reasoning":true,"reasoning_options":[{"type":"effort","values":["none","low","high"]}],
        "modalities":{"input":["text","image"],"output":["text"]},
        "limit":{"context":100000,"output":16000},
        "cost":{"input":0.1234567890123456789,"output":2}}}}}'''
    response = httpx.Response(200, content=payload, request=httpx.Request("GET", models_dev.MODELS_DEV_URL))
    monkeypatch.setattr(updater, "BUNDLED_PATH", target)
    monkeypatch.setattr(updater.httpx, "get", lambda *args, **kwargs: response)
    summary = updater.update_bundled_snapshot()
    result = json.loads(target.read_text(encoding="utf-8"))
    assert summary["added_models"] == summary["removed_models"] == 1
    assert summary["changed_models"] == 0
    assert result["models"]["new"]["cost"]["input"] == "0.1234567890123456789"
    assert result["models"]["new"]["reasoning_options"][0]["values"][0] == "none"
    assert result["models"]["new"]["modalities"]["input"] == ["text", "image"]
    assert result["_meta"]["source_sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["_meta"]["origin"] == "bundled"
    assert "pricing_records" not in result


@pytest.mark.parametrize("payload", [b"{}", b"[]", b"null", b'{"empty":{"models":{}}}', b"not json"])
def test_invalid_response_does_not_replace_bundled_snapshot(monkeypatch, tmp_path, payload):
    target = tmp_path / "snapshot.json"
    previous = '{"models":{"keep":{"id":"keep"}}}'
    target.write_text(previous, encoding="utf-8")
    response = httpx.Response(200, content=payload, request=httpx.Request("GET", models_dev.MODELS_DEV_URL))
    monkeypatch.setattr(updater, "BUNDLED_PATH", target)
    monkeypatch.setattr(updater.httpx, "get", lambda *args, **kwargs: response)
    with pytest.raises(ValueError):
        updater.update_bundled_snapshot()
    assert target.read_text(encoding="utf-8") == previous


def test_http_error_does_not_replace_bundled_snapshot(monkeypatch, tmp_path):
    target = tmp_path / "snapshot.json"
    previous = '{"models":{"keep":{"id":"keep"}}}'
    target.write_text(previous, encoding="utf-8")
    response = httpx.Response(503, request=httpx.Request("GET", models_dev.MODELS_DEV_URL))
    monkeypatch.setattr(updater, "BUNDLED_PATH", target)
    monkeypatch.setattr(updater.httpx, "get", lambda *args, **kwargs: response)
    with pytest.raises(httpx.HTTPStatusError):
        updater.update_bundled_snapshot()
    assert target.read_text(encoding="utf-8") == previous
