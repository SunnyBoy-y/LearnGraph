from __future__ import annotations

"""models.dev unified model snapshot.

The project treats https://models.dev/api.json as the shared, vendor-neutral
source for model defaults: context window, output limit, multimodal input
support, reasoning availability, and native list prices. A bundled snapshot
provides offline capabilities only. Prices come from the daily network refresh
or its last successful persisted cache, never the bundled prices.

Matching is intentionally by model name only ("只要模型名字对上号"): the same
model id served through any Provider instance resolves to the same models.dev
capability record. Prices retain their provider and currency binding. Workspace-level configuration saved for a specific Provider + model
always wins over this snapshot (merge happens in ``model_options``).
"""

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.providers.qwen_catalog import (
    LEARNGRAPH_EFFORTS,
    proportional_budget_mapping,
    proportional_effort_mapping,
)


MODELS_DEV_URL = "https://models.dev/api.json"
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60

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
    "currency",
    "release_date",
    "last_updated",
    "knowledge",
    "open_weights",
)

_lock = threading.Lock()
_refresh_lock = threading.Lock()
_snapshot: dict[str, Any] | None = None
_snapshot_cache_mtime: int | None = None


def _bundled_path() -> Path:
    # Lives beside the module (not in a data/ subdirectory) because the
    # backend .gitignore excludes every data/ directory.
    return Path(__file__).resolve().parent / "models_dev_snapshot.json"


def _cache_path() -> Path:
    from app.core.config import get_settings

    return get_settings().storage_root.parent / "models_dev_snapshot.json"


def _cache_mtime() -> int | None:
    try:
        return _cache_path().stat().st_mtime_ns
    except OSError:
        return None


def _normalize(model_id: str) -> str:
    return model_id.strip().casefold()


def build_snapshot(raw: dict[str, Any], *, origin: str, fetched_at: str) -> dict[str, Any]:
    """Collapse the per-provider api.json tree into one index keyed by model id."""

    index: dict[str, tuple[int, str, dict[str, Any]]] = {}
    pricing_records: dict[str, dict[str, Any]] = {}
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
            tariff = {field: model[field] for field in _KEEP_FIELDS if field in model}
            tariff.update(provider=provider_id, provider_name=provider.get("name", provider_id), doc=provider.get("doc"))
            if provider.get("currency") and not tariff.get("currency"):
                tariff["currency"] = provider["currency"]
            if cost:
                pricing_records[f"{provider_id}:{key}"] = tariff
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
        "pricing_records": pricing_records,
    }


def _read_snapshot_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_float=str)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        return None
    return data


def _load_locked() -> dict[str, Any]:
    global _snapshot, _snapshot_cache_mtime
    if _snapshot is not None:
        return _snapshot
    for path, origin in ((_cache_path(), "network_cache"), (_bundled_path(), "bundled")):
        cache_mtime = _cache_mtime()
        data = _read_snapshot_file(path)
        if data is not None:
            data.setdefault("_meta", {})
            data["_meta"]["origin"] = origin
            _snapshot = data
            _snapshot_cache_mtime = cache_mtime
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
    global _snapshot, _snapshot_cache_mtime
    with _lock:
        # Other API workers and the manual refresh endpoint share this cache.
        mtime = _cache_mtime()
        if _snapshot is not None and mtime is not None and mtime != _snapshot_cache_mtime:
            cached = _read_snapshot_file(_cache_path())
            if cached is not None:
                cached.setdefault("_meta", {})["origin"] = "network_cache"
                _snapshot, _snapshot_cache_mtime = cached, mtime
        return _load_locked()


def refresh_snapshot(*, timeout_seconds: float = 45.0) -> dict[str, Any]:
    """Re-download api.json, rebuild the index, persist it, and swap it in."""

    with _refresh_lock:
        return _refresh_snapshot(timeout_seconds=timeout_seconds)


def _refresh_snapshot(*, timeout_seconds: float) -> dict[str, Any]:

    response = httpx.get(
        MODELS_DEV_URL,
        timeout=timeout_seconds,
        follow_redirects=True,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    # Preserve the published decimal price before any binary-float rounding.
    raw = response.json(parse_float=str)
    if not isinstance(raw, dict) or not raw:
        raise ValueError("models.dev returned an unexpected payload shape")
    snapshot = build_snapshot(
        raw,
        origin="network",
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
    if not snapshot["models"] or not any(
        _entries_for_record(record, key, snapshot["_meta"]["fetched_at"][:10])
        for key, record in snapshot["pricing_records"].items()
    ):
        raise ValueError("models.dev returned an empty model or price catalogue")
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        cache_mtime = tmp.stat().st_mtime_ns
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    global _snapshot, _snapshot_cache_mtime
    with _lock:
        _snapshot = snapshot
        _snapshot_cache_mtime = cache_mtime
    return snapshot_status()


def snapshot_status() -> dict[str, Any]:
    from app.core.config import get_settings

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
    next_refresh = None
    if meta.get("origin") in {"network", "network_cache"} and meta.get("fetched_at"):
        try:
            fetched = datetime.fromisoformat(str(meta["fetched_at"]).replace("Z", "+00:00"))
            fetched = fetched.replace(tzinfo=timezone.utc) if fetched.tzinfo is None else fetched.astimezone(timezone.utc)
            next_refresh = fetched + timedelta(seconds=REFRESH_INTERVAL_SECONDS)
        except ValueError:
            pass
    return {
        "source": str(meta.get("source") or MODELS_DEV_URL),
        "origin": str(meta.get("origin") or "missing"),
        "fetched_at": meta.get("fetched_at"),
        "provider_count": int(meta.get("provider_count") or 0),
        "model_count": len(models),
        "priced_model_count": priced if meta.get("origin") != "bundled" else 0,
        "auto_refresh_enabled": get_settings().models_dev_refresh_enabled,
        "refresh_interval_seconds": REFRESH_INTERVAL_SECONDS,
        "next_refresh_at": next_refresh.isoformat() if next_refresh else None,
        "stale": next_refresh is None or datetime.now(timezone.utc) >= next_refresh,
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


def _is_always_thinking_claude(model_id: str) -> bool:
    """Claude Fable/Mythos always think and reject ``thinking: disabled`` (400)."""
    model = model_id.strip().casefold()
    return model.startswith(("claude-fable-", "claude-mythos-"))


def _is_openai_undisablable_reasoning(model_id: str) -> bool:
    """OpenAI reasoning models that cannot turn thinking off on the wire.

    The Responses / Chat Completions protocols expose no ``disabled`` tier for
    these — ``reasoning.effort`` only lowers depth.  The product therefore marks
    them ``thinking_required`` so 极速 is disabled in the UI instead of silently
    still reasoning.  DashScope-hosted reasoning models (qwen*, deepseek*, …)
    never match these prefixes.
    """
    model = model_id.strip().casefold()
    return model.startswith(("gpt-5", "o1", "o3", "o4", "gpt-oss"))


def capability_overlay(model_id: str, base: dict[str, Any]) -> dict[str, Any]:
    """Project-shape defaults derived from the models.dev record, if any."""

    record = lookup_model(model_id)
    if record is None:
        # Unknown models still need the thinking-only override so 极速 is
        # disabled for protocols/models that cannot turn reasoning off.
        overlay: dict[str, Any] = {}
        if _is_always_thinking_claude(model_id) or _is_openai_undisablable_reasoning(
            model_id
        ):
            overlay["thinking_required"] = True
            overlay["reasoning_efforts"] = list(LEARNGRAPH_EFFORTS)
            overlay["default_thinking_mode"] = "medium"
        return overlay
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
    outputs = {
        str(value).casefold()
        for value in (modalities.get("output") or [])
        if str(value).strip()
    }
    if outputs:
        # Image-only models (gpt-image-2, qwen-image-edit, ...) must not be
        # selected as text chat models, and they edit from reference images.
        overlay["supports_text_output"] = "text" in outputs
        overlay["supports_image_edit"] = "image" in inputs and "text" not in outputs
    if isinstance(record.get("tool_call"), bool):
        overlay["supports_agent_tools"] = record["tool_call"]
    if isinstance(record.get("structured_output"), bool):
        overlay["supports_structured_output"] = record["structured_output"]
    overlay.update(_thinking_overlay(record, base))
    # Fast mode is unavailable on models the protocol cannot turn thinking off
    # for.  Flag them so the UI disables 极速 and shows the reasoning normally
    # instead of a silent no-op.  This is id-based (not provider-based) so
    # DashScope-hosted reasoning models stay configurable.  A thinking-only
    # model must carry a non-off default and at least one effort level to pass
    # validate_model_capability_update.
    if _is_always_thinking_claude(model_id) or _is_openai_undisablable_reasoning(
        model_id
    ):
        overlay["thinking_required"] = True
        efforts = overlay.get("reasoning_efforts")
        if not isinstance(efforts, list) or not efforts:
            overlay["reasoning_efforts"] = list(LEARNGRAPH_EFFORTS)
        if not overlay.get("default_thinking_mode") or overlay.get(
            "default_thinking_mode"
        ) == "off":
            overlay["default_thinking_mode"] = "medium"
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
    def number(name: str) -> str | None:
        from app.core.money import amount_text
        value = values.get(name)
        if value is not None:
            try:
                return amount_text(value)
            except ValueError:
                return None
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
        "catalog_id": f"mdev:{record.get('provider')}:{key}{suffix}",
        "provider_key": str(record.get("provider") or "models.dev"),
        "model_id": str(record.get("id") or key),
        "currency": str(values.get("currency") or record.get("currency") or "USD").upper().replace("RMB", "CNY"),
        "native_input_per_million": input_price,
        "native_cached_input_per_million": cached,
        "native_cache_write_per_million": cache_write,
        "native_output_per_million": output_price,
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
    """Native tariffs, retaining every supplier instead of merging their prices."""

    snapshot = get_snapshot()
    if (snapshot.get("_meta") or {}).get("origin") in {"bundled", "missing"}:
        return []
    as_of = _snapshot_as_of()
    entries: list[dict[str, Any]] = []
    records = snapshot.get("pricing_records") or snapshot.get("models") or {}
    for record in records.values():
        key = _normalize(str(record.get("id") or ""))
        entries.extend(_entries_for_record(record, key, as_of))
    return entries


def price_entry_for_model(model_id: str, input_tokens: int, *, provider_key: str | None = None,
                          currency: str | None = None) -> dict[str, Any] | None:
    """Select a native tariff only when its vendor binding is known."""

    canonical = lookup_model(model_id)
    if canonical is None:
        return None
    aliases = {"qwen": {"alibaba", "alibaba-cn"}, "kimi": {"moonshotai"}, "zai": {"zhipuai"}, "xiaomi_mimo": {"xiaomi"}}
    key = _normalize(str(canonical.get("id") or model_id))
    snapshot = get_snapshot()
    if (snapshot.get("_meta") or {}).get("origin") in {"bundled", "missing"}:
        return None
    records = snapshot.get("pricing_records") or {key: canonical}
    bindings = aliases.get(provider_key, {provider_key}) if provider_key else None
    matches = []
    entries = [entry for record in records.values()
        if _normalize(str(record.get("id") or "")) == key and (bindings is None or record.get("provider") in bindings)
        for entry in _entries_for_record(record, key, _snapshot_as_of())]
    for entry in entries:
        if entry["currency"] not in {"USD", "CNY"} or (currency and entry["currency"] != currency.upper().replace("RMB", "CNY")):
            continue
        conditions = entry.get("conditions") or {}
        minimum = conditions.get("min_input_tokens")
        maximum = conditions.get("max_input_tokens")
        if minimum is not None and input_tokens < int(minimum):
            continue
        if maximum is not None and input_tokens > int(maximum):
            continue
        matches.append(entry)
    if len({entry["currency"] for entry in matches}) > 1:
        return None
    return matches[-1] if matches else None
