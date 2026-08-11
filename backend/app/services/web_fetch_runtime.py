"""Workspace-level web fetch runtime preferences (Provider 管理 -> 网页抓取).

Stores two knobs per workspace in a single ``WorkspaceSetting`` row:

* ``sandbox_enabled`` — whether the sandbox-isolated fetch lane is preferred
  for this workspace. The global env gate ``sandbox_web_fetch_enabled`` still
  applies on top, so flipping this switch cannot widen the deployment policy.
* ``priority`` — ordered list of fetch channels (``sandbox`` / ``remote`` /
  ``hosted``). ``fetch_provider_for_workspace`` resolves the first channel
  that is actually usable and falls through when one is unavailable.

The row is read through the provider-plan cache (``cached_workspace_setting_value``)
and invalidated on save so a change takes effect on the next resolution.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.models import ProviderConfig, WorkspaceSetting
from app.domain.schemas.fetch_authorization import (
    FetchChannel,
    WebFetchRuntimeUpdateRequest,
    WebFetchRuntimeView,
)
from app.providers.catalog import FETCH_PROVIDER_TYPES
from app.providers.provider_plan_cache import (
    cached_workspace_setting_value,
    invalidate_provider_plan_cache,
)
from app.repositories.audit import AuditRepository

WEB_FETCH_RUNTIME_SETTING_KEY = "web_fetch.runtime"
_DEFAULT_PRIORITY: list[str] = ["sandbox", "remote", "hosted"]


def default_web_fetch_runtime() -> dict[str, Any]:
    return {
        "sandbox_enabled": True,
        "priority": list(_DEFAULT_PRIORITY),
    }


def get_web_fetch_runtime(db: Session, workspace_id: str) -> dict[str, Any]:
    """Read and normalize the workspace web fetch runtime preferences."""
    raw = cached_workspace_setting_value(
        db, workspace_id, WEB_FETCH_RUNTIME_SETTING_KEY
    )
    if not isinstance(raw, dict):
        return default_web_fetch_runtime()
    sandbox_enabled = raw.get("sandbox_enabled")
    priority = raw.get("priority")
    if not isinstance(sandbox_enabled, bool):
        sandbox_enabled = True
    if not isinstance(priority, list) or not priority:
        priority = list(_DEFAULT_PRIORITY)
    valid = {item for item in priority if isinstance(item, str)}
    if len(valid) != len(priority):
        priority = [
            item for item in priority if isinstance(item, str)
        ] or list(_DEFAULT_PRIORITY)
    return {"sandbox_enabled": sandbox_enabled, "priority": priority}


def save_web_fetch_runtime(
    db: Session,
    workspace_id: str,
    actor_id: str,
    payload: WebFetchRuntimeUpdateRequest,
) -> dict[str, Any]:
    """Persist the runtime preferences and invalidate the plan cache."""
    row = db.scalar(
        select(WorkspaceSetting).where(
            WorkspaceSetting.workspace_id == workspace_id,
            WorkspaceSetting.key == WEB_FETCH_RUNTIME_SETTING_KEY,
        )
    )
    value = payload.model_dump()
    if row is None:
        row = WorkspaceSetting(
            workspace_id=workspace_id,
            key=WEB_FETCH_RUNTIME_SETTING_KEY,
            value=value,
        )
        db.add(row)
    else:
        row.value = value
    AuditRepository(db, workspace_id).record(
        actor_id=actor_id,
        action="web_fetch.runtime_updated",
        resource_type="workspace_setting",
        resource_id=WEB_FETCH_RUNTIME_SETTING_KEY,
        outcome="updated",
        details={"sandbox_enabled": value["sandbox_enabled"], "priority": value["priority"]},
    )
    db.commit()
    db.refresh(row)
    invalidate_provider_plan_cache(
        workspace_id, setting_key=WEB_FETCH_RUNTIME_SETTING_KEY
    )
    return value


def _remote_fetch_configured(db: Session, workspace_id: str) -> bool:
    return (
        db.scalar(
            select(ProviderConfig.id)
            .where(
                ProviderConfig.workspace_id == workspace_id,
                ProviderConfig.enabled.is_(True),
                ProviderConfig.remote_capability.is_(True),
                ProviderConfig.provider_type.in_(FETCH_PROVIDER_TYPES),
            )
            .limit(1)
        )
        is not None
    )


def web_fetch_runtime_status(
    db: Session,
    workspace_id: str,
    settings: Settings,
) -> WebFetchRuntimeView:
    """Settings plus effective channel status for the Provider 管理 UI."""
    runtime = get_web_fetch_runtime(db, workspace_id)
    from app.providers.factory import (
        _qwen_companion_for_workspace,
        _sandbox_fetch_available,
        resolve_fetch_channel,
    )
    from app.services.sandbox_runtime import resolve_sandbox_image

    sandbox_enabled = bool(runtime["sandbox_enabled"])
    global_gate = bool(settings.sandbox_web_fetch_enabled)
    egress_enabled = bool(settings.sandbox_egress_enabled)
    allowlist_count = 0
    if sandbox_enabled and global_gate and egress_enabled:
        from app.providers.factory import _web_fetch_policy_domains

        allowlist_count = len(_web_fetch_policy_domains(db, workspace_id))
    image_available = bool(resolve_sandbox_image(settings))
    sandbox_effective = bool(
        sandbox_enabled
        and global_gate
        and egress_enabled
        and allowlist_count > 0
        and image_available
    )
    remote_configured = _remote_fetch_configured(db, workspace_id)
    hosted_configured = (
        _qwen_companion_for_workspace(
            db,
            workspace_id,
            settings,
            capability="hosted_web_fetch",
        )
        is not None
    )
    effective_channel, _ = resolve_fetch_channel(
        db, workspace_id, settings, runtime["priority"]
    )
    effective: FetchChannel | None = (
        effective_channel if effective_channel in {"sandbox", "remote", "hosted"} else None
    )
    return WebFetchRuntimeView(
        sandbox_enabled=sandbox_enabled,
        priority=runtime["priority"],
        persisted=cached_workspace_setting_value(
            db, workspace_id, WEB_FETCH_RUNTIME_SETTING_KEY
        )
        is not None,
        global_sandbox_gate=global_gate,
        egress_enabled=egress_enabled,
        allowlist_count=allowlist_count,
        image_available=image_available,
        sandbox_effective=sandbox_effective,
        remote_configured=remote_configured,
        hosted_configured=hosted_configured,
        effective_channel=effective,
    )
