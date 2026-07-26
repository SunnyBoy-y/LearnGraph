from __future__ import annotations

"""models.dev unified model snapshot.

The project treats https://models.dev/api.json as the shared, vendor-neutral
source for model defaults: context window, output limit, multimodal input
support, reasoning availability, and USD list prices.  A pruned snapshot ships
with the repository so the catalogue works offline; an explicit network
refresh re-downloads the live api.json, rebuilds the same index, and persists
it under ``backend/data`` for later starts.

Matching is intentionally by model name only ("只要模型名字对上号"): the same
model id served through any Provider instance resolves to the same models.dev
record.  Workspace-level configuration saved for a specific Provider + model
always wins over this snapshot (merge happens in ``model_options``).
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.providers.qwen_catalog import (
    LEARNGRAPH_EFFORTS,
    proportional_budget_mapping,
    proportional_effort_mapping,
)


MODELS_DEV_URL = "https://models.dev/api.json"

# Aggregators mirror vendor models with sometimes stale metadata; prefer the
# canonical vendor record when the same model id appears multiple times.
_PREFERRED_PROVIDERS: dict[str, int] = {
    "openai": 100,
    "anthropic": 100,
    "google": 100,
    "deepseek": 100,
    "xai": 100,
    "mistral": 100,
    "alibaba-cn": 95,
    "alibaba": 90,
    "moonshotai": 90,
    "zhipuai": 90,
    "minimax": 90,
    "meta": 90,
    "xiaomi": 90,
    "stepfun": 90,
    "amazon-bedrock": 40,
    "azure": 40,
}

_KEEP_FIELDS = (
    "id",
    "name",
    "family",
    "reasoning",
    "reasoning_options",
    "tool_call",
    "structured_output",
    "attachment",
    "temperature",
    "modalities",
    "limit",
    "cost",
    "release_date",
    "last_updated",
    "knowledge",
    "open_weights",
)

_lock = threading.Lock()
_snapshot: dict[str, Any] | None = None


def _bundled_path() -> Path:
    # Lives beside the module (not in a data/ subdirectory) because the
    # backend .gitignore excludes every data/ directory.
    return Path(__file__).resolve().parent / "models_dev_snapshot.json"


def _cache_path() -> Path:
    # backend/app/providers/models_dev.py -> backend/data, matching the
    # existing on-disk data directory used by the sqlite database.
    return Path(__file__).resolve().parents[2] / "data" / "models_dev_snapshot.json"


def _normalize(model_id: str) -> str:
    return model_id.strip().casefold()


def build_snapshot(raw: dict[str, Any], *, origin: str, fetched_at: str) -> dict[str, Any]:
    """Collapse the per-provider api.json tree into one index keyed by model id."""

    index: dict[str, tuple[int, str, dict[str, Any]]] = {}
    provider_count = 0
    for provider_id, provider in raw.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models")
        if not isinstance(models, dict):
            continue
        provider_count += 1
        for model_id, model in models.items():
            if not isinstance(model, dict):
                continue
            key = _normalize(str(model_id))
            if not key:
                continue
            cost = model.get("cost") or {}
            limit = model.get("limit") or {}
            score = _PREFERRED_PROVIDERS.get(provider_id, 10)
            score += 4 * bool(cost) + 2 * bool(limit.get("context"))
            score += bool(model.get("modalities"))
            current = index.get(key)
            if current is None or score > current[0] or (
                score == current[0] and provider_id < current[1]
            ):
                record = {field: model[field] for field in _KEEP_FIELDS if field in model}
                record["provider"] = provider_id
                record["provider_name"] = provider.get("name", provider_id)
                record["doc"] = provider.get("doc")
                record["providers"] = current[2]["providers"] if current else []
                index[key] = (score, provider_id, record)
            if provider_id not in index[key][2]["providers"]:
                index[key][2]["providers"].append(provider_id)
    return {
        "_meta": {
            "source": MODELS_DEV_URL,
            "origin": origin,
            "fetched_at": fetched_at,
            "provider_count": provider_count,
            "model_count": len(index),
        },
        "models": {key: value[2] for key, value in sorted(index.items())},
    }


def _read_snapshot_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        return None
    return data


def _load_locked() -> dict[str, Any]:
    global _snapshot
    if _snapshot is not None:
        return _snapshot
    for path, origin in ((_cache_path(), "network_cache"), (_bundled_path(), "bundled")):
        data = _read_snapshot_file(path)
        if data is not None:
            data.setdefault("_meta", {})
            data["_meta"].setdefault("origin", origin)
            _snapshot = data
            return data
    _snapshot = {
        "_meta": {
            "source": MODELS_DEV_URL,
            "origin": "missing",
            "fetched_at": None,
            "provider_count": 0,
            "model_count": 0,
        },
        "models": {},
    }
    return _snapshot


def get_snapshot() -> dict[str, Any]:
    with _lock:
        return _load_locked()


def refresh_snapshot(*, timeout_seconds: float = 45.0) -> dict[str, Any]:
    """Re-download api.json, rebuild the index, persist it, and swap it in."""

    response = httpx.get(
        MODELS_DEV_URL,
        timeout=timeout_seconds,
        follow_redirects=True,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    raw = response.json()
    if not isinstance(raw, dict) or not raw:
        raise ValueError("models.dev returned an unexpected payload shape")
    snapshot = build_snapshot(
        raw,
        origin="network",
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)
    global _snapshot
    with _lock:
        _snapshot = snapshot
    return snapshot_status()


def snapshot_status() -> dict[str, Any]:
    snapshot = get_snapshot()
    meta = dict(snapshot.get("_meta") or {})
    models = snapshot.get("models") or {}
    priced = sum(
        1
        for record in models.values()
        if isinstance(record.get("cost"), dict)
        and record["cost"].get("input") is not None
        and record["cost"].get("output") is not None
    )
    return {
        "source": str(meta.get("source") or MODELS_DEV_URL),
        "origin": str(meta.get("origin") or "missing"),
        "fetched_at": meta.get("fetched_at"),
        "provider_count": int(meta.get("provider_count") or 0),
        "model_count": len(models),
        "priced_model_count": priced,
    }


def lookup_model(model_id: str) -> dict[str, Any] | None:
    """Match by model name across every provider models.dev knows about."""

    models = get_snapshot().get("models") or {}
    key = _normalize(model_id)
    if not key:
        return None
    record = models.get(key)
    if record is not None:
        return record
    # Workspace model ids are often namespaced ("kimi/kimi-k3"); models.dev
    # aggregator ids are too ("moonshotai/kimi-k2.6"). Fall back to the bare
    # model name after the last slash on both sides.
    if "/" in key:
        bare = key.rsplit("/", 1)[-1]
        record = models.get(bare)
        if record is not None:
            return record
    else:
        bare = key
    for candidate_key, candidate in models.items():
        if "/" in candidate_key and candidate_key.rsplit("/", 1)[-1] == bare:
            return candidate
    return None


def _thinking_overlay(record: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    reasoning = record.get("reasoning")
    if reasoning is False:
        # models.dev is authoritative: the model has no thinking mode.
        return {
            "reasoning_efforts": [],
            "thinking_mapping": {"off": None},
            "default_thinking_mode": "off",
            "thinking_required": False,
        }
    if reasoning is not True:
        return {}
    base_efforts = base.get("reasoning_efforts")
    if isinstance(base_efforts, list) and base_efforts:
        # The reviewed catalogue already carries a provider-specific wire
        # mapping; models.dev only confirms that thinking exists.
        return {}
    options = record.get("reasoning_options")
    option = next(
        (item for item in options if isinstance(item, dict)),
        None,
    ) if isinstance(options, list) else None
    overlay: dict[str, Any] = {
        "reasoning_efforts": list(LEARNGRAPH_EFFORTS),
        "default_thinking_mode": "off",
        "thinking_required": False,
    }
    option_type = str(option.get("type") or "") if option else ""
    if option_type == "effort":
        values = [
            str(value)
            for value in option.get("values") or []
            if str(value).strip() and str(value).casefold() != "none"
        ]
        if values:
            overlay["reasoning_parameter"] = "reasoning_effort"
            overlay["thinking_mapping"] = {
                "off": None,
                **proportional_effort_mapping(values),
            }
            return overlay
    if option_type == "budget_tokens":
        try:
            maximum = int(option.get("max") or 0)
            minimum = int(option.get("min") or 1)
        except (TypeError, ValueError):
            maximum, minimum = 0, 1
        if maximum >= 1:
            overlay["reasoning_parameter"] = "thinking_budget"
            overlay["thinking_mapping"] = {
                "off": None,
                **proportional_budget_mapping(maximum, minimum=minimum),
            }
            return overlay
    # Toggle-shaped (or undeclared) reasoning: any non-off level simply turns
    # provider thinking on.
    overlay["reasoning_parameter"] = "enable_thinking"
    overlay["thinking_mapping"] = {
        "off": False,
        **{effort: True for effort in LEARNGRAPH_EFFORTS},
    }
    return overlay


def capability_overlay(model_id: str, base: dict[str, Any]) -> dict[str, Any]:
    """Project-shape defaults derived from the models.dev record, if any."""

    record = lookup_model(model_id)
    if record is None:
        return {}
    overlay: dict[str, Any] = {}
    limit = record.get("limit") or {}
    context = limit.get("context")
    if isinstance(context, (int, float)) and context > 0:
        context_tokens = int(context)
        overlay["context_window_tokens"] = context_tokens
        input_limit = limit.get("input")
        overlay["context_limit_tokens"] = (
            min(int(input_limit), context_tokens)
            if isinstance(input_limit, (int, float)) and input_limit > 0
            else context_tokens
        )
    output = limit.get("output")
    if isinstance(output, (int, float)) and output > 0:
        ceiling = overlay.get("context_limit_tokens") or base.get("context_limit_tokens")
        overlay["max_output_tokens"] = (
            min(int(output), int(ceiling) - 1)
            if isinstance(ceiling, int) and int(ceiling) > 1
            else int(output)
        )
    modalities = record.get("modalities") or {}
    inputs = {
        str(value).casefold()
        for value in (modalities.get("input") or [])
        if str(value).strip()
    }
    if inputs:
        supports_image = "image" in inputs
        overlay["supports_image_input"] = supports_image
        overlay["supports_video_input"] = "video" in inputs
        overlay["supports_audio_input"] = "audio" in inputs
        overlay["supports_pdf_input"] = "pdf" in inputs or record.get("attachment") is True
        overlay["image_input_mode"] = "native" if supports_image else "auto"
    if isinstance(record.get("tool_call"), bool):
        overlay["supports_agent_tools"] = record["tool_call"]
    if isinstance(record.get("structured_output"), bool):
        overlay["supports_structured_output"] = record["structured_output"]
    overlay.update(_thinking_overlay(record, base))
    overlay["capability_source"] = "models_dev"
    overlay["models_dev"] = {
        "provider": record.get("provider"),
        "providers": list(record.get("providers") or []),
        "name": record.get("name"),
        "release_date": record.get("release_date"),
        "last_updated": record.get("last_updated"),
        "knowledge": record.get("knowledge"),
        "doc": record.get("doc"),
    }
    return overlay


def _price_entry(
    record: dict[str, Any],
    key: str,
    *,
    suffix: str,
    values: dict[str, Any],
    conditions: dict[str, Any],
    as_of: str,
) -> dict[str, Any] | None:
    def number(name: str) -> float | None:
        value = values.get(name)
        if isinstance(value, (int, float)):
            return float(value)
        return None

    input_price = number("input")
    output_price = number("output")
    if input_price is None or output_price is None:
        return None
    cached = number("cache_read")
    cache_write = number("cache_write")
    limit = record.get("limit") or {}
    merged_conditions = dict(conditions)
    if isinstance(limit.get("context"), (int, float)):
        merged_conditions.setdefault("context_window_tokens", int(limit["context"]))
    if isinstance(limit.get("output"), (int, float)):
        merged_conditions.setdefault("max_output_tokens", int(limit["output"]))
    return {
        "catalog_id": f"mdev:{key}{suffix}",
        "provider_key": str(record.get("provider") or "models.dev"),
        "model_id": str(record.get("id") or key),
        "currency": "USD",
        "native_input_per_million": input_price,
        "native_cached_input_per_million": cached,
        "native_cache_write_per_million": cache_write,
        "native_output_per_million": output_price,
        "input_usd_per_million": input_price,
        "cached_input_usd_per_million": cached,
        "cache_write_usd_per_million": cache_write,
        "output_usd_per_million": output_price,
        "conditions": merged_conditions,
        "source_url": str(record.get("doc") or MODELS_DEV_URL),
        "as_of": as_of,
        "source": "models_dev",
    }


def _entries_for_record(record: dict[str, Any], key: str, as_of: str) -> list[dict[str, Any]]:
    cost = record.get("cost")
    if not isinstance(cost, dict):
        return []
    tiers = [
        tier
        for tier in (cost.get("tiers") or [])
        if isinstance(tier, dict)
        and str((tier.get("tier") or {}).get("type") or "") == "context"
        and isinstance((tier.get("tier") or {}).get("size"), (int, float))
    ]
    tiers.sort(key=lambda tier: float(tier["tier"]["size"]))
    entries: list[dict[str, Any]] = []
    base_conditions: dict[str, Any] = {}
    if tiers:
        base_conditions = {
            "context_tier": "short",
            "max_input_tokens": int(tiers[0]["tier"]["size"]),
        }
    base = _price_entry(
        record,
        key,
        suffix="",
        values=cost,
        conditions=base_conditions,
        as_of=as_of,
    )
    if base is not None:
        entries.append(base)
    for position, tier in enumerate(tiers):
        boundary = int(tier["tier"]["size"])
        conditions: dict[str, Any] = {
            "context_tier": "long",
            "min_input_tokens": boundary + 1,
        }
        if position + 1 < len(tiers):
            conditions["max_input_tokens"] = int(tiers[position + 1]["tier"]["size"])
        entry = _price_entry(
            record,
            key,
            suffix=f"#t{position + 1}",
            values=tier,
            conditions=conditions,
            as_of=as_of,
        )
        if entry is not None:
            entries.append(entry)
    return entries


def _snapshot_as_of() -> str:
    fetched = str((get_snapshot().get("_meta") or {}).get("fetched_at") or "")
    return fetched[:10] if fetched else "unknown"


def pricing_entries() -> list[dict[str, Any]]:
    """Every models.dev USD tariff in the local pricing-catalog item shape."""

    snapshot = get_snapshot()
    as_of = _snapshot_as_of()
    entries: list[dict[str, Any]] = []
    for key, record in (snapshot.get("models") or {}).items():
        entries.extend(_entries_for_record(record, key, as_of))
    return entries


def price_entry_for_model(model_id: str, input_tokens: int) -> dict[str, Any] | None:
    """Tier-aware tariff for one model, matched by name across providers."""

    record = lookup_model(model_id)
    if record is None:
        return None
    key = _normalize(str(record.get("id") or model_id))
    matches = []
    for entry in _entries_for_record(record, key, _snapshot_as_of()):
        conditions = entry.get("conditions") or {}
        minimum = conditions.get("min_input_tokens")
        maximum = conditions.get("max_input_tokens")
        if minimum is not None and input_tokens < int(minimum):
            continue
        if maximum is not None and input_tokens > int(maximum):
            continue
        matches.append(entry)
    return matches[-1] if matches else None
