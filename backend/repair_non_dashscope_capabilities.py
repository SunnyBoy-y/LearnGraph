"""Repair persisted model capability snapshots for non-DashScope endpoints.

The reviewed Qwen catalogue applies DashScope-hosted claims by model name
alone: ``hosted_web_search`` / ``hosted_web_fetch`` / ``hosted_image_search``,
``preserve_thinking``, ``chat_search_strategy``, Responses-style native tools
and budget-shaped thinking (``thinking_budget``).  Third-party
OpenAI-compatible relays (for example sub2api-style gateways) reject the
matching request fields with ``UNKNOWN_FIELD`` — the chat stream then fails
with ``Provider returned HTTP 400 ... enable_search``.

This script:
1. persists the wire ``protocol_family`` on every Provider capability snapshot
   (runtime merging gates DashScope-private claims on it), and
2. strips DashScope-private claims from per-model snapshots that came from the
   official catalogue (``capability_source == "official_catalog"``) on any
   Provider whose endpoint is not a DashScope / Model Studio origin.

It only touches persisted capabilities; ``qwen_model_defaults`` /
``catalog_capability_snapshot`` now apply the same rule so future syncs keep
snapshots clean.
"""
import sys

sys.path.insert(0, ".")

from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.models import ProviderConfig
from app.providers.qwen_catalog import (
    PROTOCOL_FAMILY_DASHSCOPE,
    protocol_family_for,
    strip_dashscope_private_capabilities,
)

db = SessionLocal()
try:
    providers = db.scalars(select(ProviderConfig)).all()
    changed_providers = 0
    changed_models = 0
    for provider in providers:
        capabilities = dict(provider.capabilities or {})
        family = protocol_family_for(provider.provider_type, provider.base_url)
        family_written = capabilities.get("protocol_family") != family
        if family_written:
            capabilities["protocol_family"] = family
        models = dict(capabilities.get("models") or {})
        stripped: list[str] = []
        if family != PROTOCOL_FAMILY_DASHSCOPE:
            for model_id, snapshot in models.items():
                if not isinstance(snapshot, dict):
                    continue
                if snapshot.get("capability_source") != "official_catalog":
                    # User-declared claims are respected; only catalogue claims
                    # are being corrected here.
                    continue
                before = dict(snapshot)
                strip_dashscope_private_capabilities(snapshot)
                if snapshot != before:
                    models[model_id] = snapshot
                    stripped.append(model_id)
        if stripped:
            capabilities["models"] = models
            changed_models += len(stripped)
        if family_written or stripped:
            provider.capabilities = capabilities
            changed_providers += 1
            print(
                f"[{provider.id}] {provider.provider_type} {provider.base_url} "
                f"-> family={family}"
                + (f", stripped {len(stripped)} model(s): {', '.join(stripped)}" if stripped else "")
            )
    db.commit()
    print(f"done: {changed_providers} provider(s), {changed_models} model(s) repaired")
finally:
    db.close()
