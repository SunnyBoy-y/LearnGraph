"""Deployment-wide voice relay (Cloudflare TURN) API.

Two very different audiences share these routes:

* ``GET /voice/ice-servers`` is read by **every** signed-in user: the browser
  needs ICE servers before it can build a peer connection, and the credential
  Cloudflare hands out is short-lived and designed to be held by clients.
* every other route is **deployment-administrator only** and never returns the
  stored secret -- a mask and a fingerprint only, mirroring the provider pages.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import AppSettings, CurrentPrincipal, DB, SystemAdminContext
from app.repositories.audit import AuditRepository
from app.services.voice_relay import VoiceRelayService

router = APIRouter(prefix="/voice", tags=["voice-relay"])

RESOURCE_TYPE = "voice_relay"
SCOPE_DEPLOYMENT = "deployment"


def _audit(
    db: DB,
    context: Any,
    *,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record an instance-wide change.

    ``AuditRepository`` is workspace-scoped, so the row lands in the workspace the
    administrator acted from -- the trail is per-workspace by design, while the
    change itself is instance-wide.  ``resource_id`` stays the constant
    ``deployment`` so every edit of this singleton is findable.
    """

    AuditRepository(db, context.workspace.id).record(
        actor_id=context.principal.user_id,
        action=action,
        resource_type=RESOURCE_TYPE,
        resource_id=SCOPE_DEPLOYMENT,
        details=details or {},
    )
    db.commit()


@router.get("/ice-servers")
def voice_ice_servers(principal: CurrentPrincipal, db: DB, settings: AppSettings):
    """ICE servers for the calling browser (empty list when unconfigured).

    Never fails: a broken relay configuration degrades to "no ICE servers",
    which is exactly the behaviour of a deployment that never configured one.
    """

    resolved = VoiceRelayService(db, settings).resolve()
    return {
        "iceServers": [resolved.as_client_config()] if resolved.configured else [],
        "source": resolved.source,
        "detail": resolved.detail,
    }


@router.get("/relay")
def read_voice_relay(context: SystemAdminContext, db: DB, settings: AppSettings):
    return VoiceRelayService(db, settings).public_config()


@router.put("/relay")
def update_voice_relay(
    payload: dict[str, Any],
    context: SystemAdminContext,
    db: DB,
    settings: AppSettings,
):
    service = VoiceRelayService(db, settings)
    result = service.save(dict(payload or {}), actor_id=context.principal.user_id)
    _audit(
        db,
        context,
        action="voice_relay.update",
        details={
            "mode": result.get("mode"),
            "urls": result.get("urls"),
            "secret_rotated": bool((payload or {}).get("secret")),
        },
    )
    return result


@router.post("/relay/enabled")
def set_voice_relay_enabled(
    payload: dict[str, Any],
    context: SystemAdminContext,
    db: DB,
    settings: AppSettings,
):
    """Emergency off-switch: keeps the configuration but stops handing it out."""

    service = VoiceRelayService(db, settings)
    result = service.set_enabled(bool((payload or {}).get("enabled")), actor_id=context.principal.user_id)
    _audit(db, context, action="voice_relay.enabled", details={"enabled": result.get("enabled")})
    return result


@router.post("/relay/test")
async def test_voice_relay(
    payload: dict[str, Any],
    context: SystemAdminContext,
    db: DB,
    settings: AppSettings,
):
    """Mint a credential and probe every URL from this machine.

    Returns Cloudflare's own URL set alongside the configured one: the first
    real run is what confirms the response shape and which transport actually
    works from inside the deployment.
    """

    service = VoiceRelayService(db, settings)
    result = await service.test(dict(payload or {}))
    _audit(
        db,
        context,
        action="voice_relay.test",
        details={"ok": result.get("ok"), "detail": result.get("detail")},
    )
    return result


@router.delete("/relay")
def clear_voice_relay(context: SystemAdminContext, db: DB, settings: AppSettings):
    service = VoiceRelayService(db, settings)
    service.clear()
    _audit(db, context, action="voice_relay.clear")
    return {"configured": False}
