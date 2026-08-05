"""Trusted renderer channel protocol: bounded postMessage contract + capability tokens.

This module is purely server-side. The host-side opaque-origin iframe boundary
(``sandbox="allow-scripts"``, NO ``allow-same-origin``, ``connect-src 'none'``)
is unchanged; this code never grants the renderer host DOM access, browser
auth tokens, provider credentials, or unrestricted API endpoints.

The message contract is deliberately minimal and versioned:

* ``version`` — protocol version, only a small supported set is accepted.
* ``event_type`` — one of a bounded set of event types.
* ``payload`` — schema-validated JSON matching the per-event JSON Schema.
* ``token`` — the per-render short-lived capability token presented on every
  inbound message so the host can prove the sender was authorized for THIS
  render, component, and workspace.

Enforcement here covers: supported version, allowed event type, schema-validated
payload, size cap (``MAX_RENDERER_MESSAGE_BYTES``), per-render message rate cap
(``MAX_RENDERER_MESSAGES_PER_RENDER`` tracked on the token record), and source
check (messages must originate from the opaque sandboxed iframe).

Capability tokens are issued server-side per eligible render. Only a SHA-256
hash of the token secret is persisted; the raw secret is returned once inside
the sealed envelope handed to the host. ``redeem_renderer_capability_token``
enforces expiry, audience (component + workspace), render binding
(``data_sha256``), single-render semantics, and the per-render message cap.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from jsonschema import ValidationError
from jsonschema.validators import validator_for
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import ComponentAuthorization, ComponentCapabilityToken, ComponentManifestVersion


# --------------------------------------------------------------------------- #
# Protocol contract
# --------------------------------------------------------------------------- #

RENDERER_MESSAGE_PROTOCOL_VERSION = "1"
SUPPORTED_RENDERER_MESSAGE_VERSIONS = frozenset({RENDERER_MESSAGE_PROTOCOL_VERSION})

# Event types a component iframe may emit toward the host.
RENDERER_READY_EVENT = "component.ready"
RENDERER_EVENT_EVENT = "component.event"
RENDERER_LOG_EVENT = "component.log"
RENDERER_ERROR_EVENT = "component.error"

# Host -> iframe handshake and state delivery. Never accepted inbound.
RENDERER_UNLOCK_EVENT = "renderer.unlock"
RENDERER_STATE_EVENT = "renderer.state"

ALLOWED_IFRAME_EVENT_TYPES = frozenset(
    {RENDERER_READY_EVENT, RENDERER_EVENT_EVENT, RENDERER_LOG_EVENT, RENDERER_ERROR_EVENT}
)
HOST_TO_IFRAME_EVENT_TYPES = frozenset({RENDERER_UNLOCK_EVENT, RENDERER_STATE_EVENT})
ALLOWED_RENDERER_EVENT_TYPES = ALLOWED_IFRAME_EVENT_TYPES | HOST_TO_IFRAME_EVENT_TYPES

# Bounded protocol limits. A renderer can never exceed these regardless of the
# registered manifest.
MAX_RENDERER_MESSAGE_BYTES = 16 * 1024
MAX_RENDERER_MESSAGES_PER_RENDER = 128
MAX_RENDERER_EVENT_VALUE_CHARS = 10_000
MAX_RENDERER_EVENT_ARRAY_ITEMS = 100
MAX_RENDERER_EVENT_ID_CHARS = 64

# Session-level sub-application budgets. Persistence and rate tracking are
# enforced by the sub-application session service; these are the shared limits.
MAX_SUBAPP_STATE_BYTES = 64 * 1024
MAX_SUBAPP_STATES_PER_SESSION = 500
MAX_SUBAPP_EVENTS_PER_SESSION = 1_000
MAX_SUBAPP_STATE_RATE_PER_MINUTE = 60

# The only accepted message source: the sandboxed iframe renders as an opaque
# origin, so ``event.origin`` is ``"null"`` in a browser. The server-side
# source check is enforced against this marker when a message is validated.
RENDERER_IFRAME_SOURCE = "opaque-iframe"

CAPABILITY_TOKEN_TTL_SECONDS = 60

# The iframe boundary is fixed and never relaxed. Exposed on the sealed
# envelope so consumers know the boundary that will enforce it.
IFRAME_BOUNDARY = {
    "sandbox": "allow-scripts",
    "allow_same_origin": False,
    "connect_src": "none",
    "source": RENDERER_IFRAME_SOURCE,
}

RENDERER_PAYLOAD_SCHEMAS: dict[str, dict[str, Any]] = {
    RENDERER_READY_EVENT: {
        "type": "object",
        "additionalProperties": False,
        "required": ["component_id", "manifest_version"],
        "properties": {
            "component_id": {"type": "string", "maxLength": 120},
            "manifest_version": {"type": "string", "maxLength": 40},
            "render_ref": {"type": "string", "maxLength": 64},
        },
    },
    RENDERER_EVENT_EVENT: {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "value"],
        "properties": {
            "type": {"type": "string", "enum": ["submit", "change", "select"]},
            "value": {
                "oneOf": [
                    {"type": "string", "maxLength": MAX_RENDERER_EVENT_VALUE_CHARS},
                    {
                        "type": "array",
                        "maxItems": MAX_RENDERER_EVENT_ARRAY_ITEMS,
                        "items": {"type": "string", "maxLength": 1_000},
                    },
                ]
            },
            "event_id": {"type": "string", "maxLength": MAX_RENDERER_EVENT_ID_CHARS},
        },
    },
    RENDERER_LOG_EVENT: {
        "type": "object",
        "additionalProperties": False,
        "required": ["level", "message"],
        "properties": {
            "level": {"type": "string", "enum": ["info", "warn", "error"]},
            "message": {"type": "string", "maxLength": 2_000},
            "count": {"type": "integer", "minimum": 1, "maximum": 10_000},
        },
    },
    RENDERER_ERROR_EVENT: {
        "type": "object",
        "additionalProperties": False,
        "required": ["code", "message"],
        "properties": {
            "code": {"type": "string", "maxLength": 80},
            "message": {"type": "string", "maxLength": 2_000},
        },
    },
    RENDERER_UNLOCK_EVENT: {
        "type": "object",
        "additionalProperties": False,
        "required": ["token", "component_id", "render_ref"],
        "properties": {
            "token": {"type": "string", "minLength": 32, "maxLength": 128},
            "component_id": {"type": "string", "maxLength": 120},
            "render_ref": {"type": "string", "maxLength": 64},
        },
    },
    RENDERER_STATE_EVENT: {
        "type": "object",
        "additionalProperties": False,
        "required": ["state_version", "state_sha256", "state"],
        "properties": {
            "state_version": {"type": "integer", "minimum": 1},
            "state_sha256": {
                "type": "string",
                "pattern": "^[0-9a-fA-F]{64}$",
            },
            "state": {"type": "object"},
        },
    },
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes | Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical_json(value)
    return hashlib.sha256(payload).hexdigest()


def _token_sha256(token: str) -> str:
    """Hash a raw token secret (never canonical-JSON encoded)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Message contract
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RendererMessageView:
    version: str
    event_type: str
    payload: dict[str, Any]
    source: str


@dataclass(frozen=True)
class RendererMessageResult:
    accepted: bool
    reason: str | None = None
    event: RendererMessageView | None = None


def build_renderer_message(
    *,
    event_type: str,
    payload: dict[str, Any],
    token: str | None = None,
    version: str = RENDERER_MESSAGE_PROTOCOL_VERSION,
) -> dict[str, Any]:
    """Build a protocol message envelope (host <-> iframe)."""
    message: dict[str, Any] = {
        "version": version,
        "event_type": event_type,
        "payload": payload,
    }
    if token is not None:
        message["token"] = token
    return message


def _validate_message_structure(
    message: Any,
    *,
    source: str,
    event_schema: dict[str, Any] | None,
) -> RendererMessageView | str:
    """Structural + schema validation. Returns the view or a reason code."""
    if source != RENDERER_IFRAME_SOURCE:
        return "render_source_not_opaque"
    if not isinstance(message, dict):
        return "render_message_invalid"
    version = message.get("version")
    if version not in SUPPORTED_RENDERER_MESSAGE_VERSIONS:
        return "render_message_unsupported_version"
    event_type = message.get("event_type")
    if event_type not in ALLOWED_RENDERER_EVENT_TYPES:
        return "render_event_unknown"
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return "render_payload_invalid"
    if len(_canonical_json(message)) > MAX_RENDERER_MESSAGE_BYTES:
        return "render_message_too_large"

    schema = RENDERER_PAYLOAD_SCHEMAS[event_type]
    try:
        validator_for(schema).check_schema(schema)
        validator_for(schema)(schema).validate(payload)
    except ValidationError:
        return "render_payload_schema_rejected"

    # The component event must additionally match the registered manifest
    # event_schema when the caller supplies it (server-side re-validation).
    if event_type == RENDERER_EVENT_EVENT and event_schema is not None:
        try:
            validator_for(event_schema).check_schema(event_schema)
            validator_for(event_schema)(event_schema).validate(payload)
        except ValidationError:
            return "render_event_schema_rejected"

    return RendererMessageView(
        version=version,
        event_type=event_type,
        payload=payload,
        source=source,
    )


def validate_renderer_state_payload(
    payload: Any,
    *,
    state_schema: dict[str, Any],
) -> RendererMessageView | str:
    """Validate a host-bound ``renderer.state`` payload before it is sent.

    ``renderer.state`` is host -> iframe only and is never accepted on the
    inbound renderer-message path. The host validates the protocol payload
    schema and the registered sub-application ``state_schema`` before
    forwarding a versioned state snapshot.
    """
    renderer_state_schema = RENDERER_PAYLOAD_SCHEMAS[RENDERER_STATE_EVENT]
    try:
        validator_for(renderer_state_schema).check_schema(renderer_state_schema)
        validator_for(renderer_state_schema)(renderer_state_schema).validate(payload)
    except ValidationError:
        return "render_state_payload_schema_rejected"

    state = payload["state"]
    if len(_canonical_json(state)) > MAX_SUBAPP_STATE_BYTES:
        return "render_state_too_large"
    if payload["state_sha256"].lower() != _sha256(state):
        return "render_state_sha256_mismatch"

    try:
        validator_for(state_schema).check_schema(state_schema)
        validator_for(state_schema)(state_schema).validate(state)
    except ValidationError:
        return "render_state_schema_rejected"

    return RendererMessageView(
        version=RENDERER_MESSAGE_PROTOCOL_VERSION,
        event_type=RENDERER_STATE_EVENT,
        payload=payload,
        source="host",
    )


def validate_renderer_message(
    db: Session,
    *,
    message: Any,
    source: str,
    workspace_id: str,
    component_id: str,
    event_schema: dict[str, Any] | None = None,
) -> RendererMessageResult:
    """Server-side validation of an inbound renderer message.

    Rejects unsupported versions, unknown event types, schema-invalid payloads,
    oversized messages, non-opaque sources, host->iframe message attempts, and
    any message that cannot redeem a valid capability token for this component
    in this workspace.
    """
    view = _validate_message_structure(message, source=source, event_schema=event_schema)
    if isinstance(view, str):
        return RendererMessageResult(accepted=False, reason=view)

    if view.event_type in HOST_TO_IFRAME_EVENT_TYPES:
        # Host -> iframe messages are never accepted on the inbound channel.
        return RendererMessageResult(accepted=False, reason="render_unlock_not_accepting")

    token = message.get("token") if isinstance(message, dict) else None
    if not isinstance(token, str) or not token:
        return RendererMessageResult(accepted=False, reason="render_token_missing")

    result = redeem_renderer_capability_token(
        db,
        token=token,
        workspace_id=workspace_id,
        component_id=component_id,
        render_ref=(view.payload or {}).get("render_ref"),
    )
    if not result.valid:
        return RendererMessageResult(accepted=False, reason=result.reason, event=view)

    return RendererMessageResult(accepted=True, reason=None, event=view)


# --------------------------------------------------------------------------- #
# Capability tokens
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RendererTokenResult:
    valid: bool
    reason: str | None
    token_id: str | None = None
    component_id: str | None = None
    workspace_id: str | None = None


@dataclass(frozen=True)
class CapabilityTokenBundle:
    token: str
    record: ComponentCapabilityToken


def issue_renderer_capability_token(
    db: Session,
    *,
    workspace_id: str,
    plugin_id: str,
    manifest_version_id: str,
    authorization_id: str,
    component_id: str,
    data_sha256: str,
    issued_by: str,
    ttl_seconds: int = CAPABILITY_TOKEN_TTL_SECONDS,
    max_messages: int = MAX_RENDERER_MESSAGES_PER_RENDER,
) -> CapabilityTokenBundle:
    """Issue a per-render capability token and persist only its SHA-256 hash."""
    raw = secrets.token_urlsafe(32)
    token_hash = _token_sha256(raw)
    now = _utc_now()
    record = ComponentCapabilityToken(
        workspace_id=workspace_id,
        plugin_id=plugin_id,
        manifest_version_id=manifest_version_id,
        authorization_id=authorization_id,
        component_id=component_id,
        token_hash=token_hash,
        token_prefix=raw[:8],
        audience=f"component:{component_id}@workspace:{workspace_id}",
        protocol_version=RENDERER_MESSAGE_PROTOCOL_VERSION,
        data_sha256=data_sha256,
        status="active",
        single_use=True,
        max_messages=max_messages,
        message_count=0,
        issued_by=issued_by,
        issued_at=now,
        expires_at=now + timedelta(seconds=max(1, ttl_seconds)),
    )
    db.add(record)
    db.flush()
    return CapabilityTokenBundle(token=raw, record=record)


def redeem_renderer_capability_token(
    db: Session,
    *,
    token: str,
    workspace_id: str,
    component_id: str,
    render_ref: str | None = None,
) -> RendererTokenResult:
    """Validate and consume one message slot for a capability token.

    The token must be active, unexpired, audience-matched to the exact
    component + workspace, bound to the same render, and below the per-render
    message cap. On success ``message_count`` is incremented (single-render
    semantics: a token is short-lived and belongs to exactly one render).
    """
    if not isinstance(token, str) or not token:
        return RendererTokenResult(valid=False, reason="render_token_missing")
    record = db.scalar(
        select(ComponentCapabilityToken).where(
            ComponentCapabilityToken.token_hash == _token_sha256(token)
        )
    )
    if record is None:
        return RendererTokenResult(valid=False, reason="render_token_not_found")

    if record.status != "active":
        return RendererTokenResult(
            valid=False,
            reason="render_token_inactive",
            token_id=record.id,
            component_id=record.component_id,
            workspace_id=record.workspace_id,
        )
    if record.workspace_id != workspace_id:
        return RendererTokenResult(
            valid=False,
            reason="render_workspace_mismatch",
            token_id=record.id,
            component_id=record.component_id,
            workspace_id=record.workspace_id,
        )
    if record.component_id != component_id:
        return RendererTokenResult(
            valid=False,
            reason="render_audience_mismatch",
            token_id=record.id,
            component_id=record.component_id,
            workspace_id=record.workspace_id,
        )
    if record.expires_at <= _utc_now():
        return RendererTokenResult(
            valid=False,
            reason="render_token_expired",
            token_id=record.id,
            component_id=record.component_id,
            workspace_id=record.workspace_id,
        )
    if render_ref is not None and record.data_sha256 != render_ref:
        return RendererTokenResult(
            valid=False,
            reason="render_binding_mismatch",
            token_id=record.id,
            component_id=record.component_id,
            workspace_id=record.workspace_id,
        )
    if record.message_count >= record.max_messages:
        return RendererTokenResult(
            valid=False,
            reason="render_message_rate_exceeded",
            token_id=record.id,
            component_id=record.component_id,
            workspace_id=record.workspace_id,
        )
    record.message_count += 1
    db.flush()
    return RendererTokenResult(
        valid=True,
        reason=None,
        token_id=record.id,
        component_id=record.component_id,
        workspace_id=record.workspace_id,
    )


def consume_renderer_capability_token(
    db: Session,
    *,
    token_id: str,
    reason: str = "render_ended",
) -> None:
    """Mark a capability token consumed (render teardown)."""
    record = db.get(ComponentCapabilityToken, token_id)
    if record is None or record.status != "active":
        return
    record.status = "consumed"
    record.consumed_at = _utc_now()
    record.reason = reason
    db.flush()


def revoke_renderer_capability_token(
    db: Session,
    *,
    token_id: str,
    reason: str = "renderer_revoked",
) -> None:
    """Revoke a capability token before it is consumed (authorization loss)."""
    record = db.get(ComponentCapabilityToken, token_id)
    if record is None or record.status != "active":
        return
    record.status = "revoked"
    record.revoked_at = _utc_now()
    record.reason = reason
    db.flush()


# --------------------------------------------------------------------------- #
# Sealed envelope
# --------------------------------------------------------------------------- #

def build_trusted_renderer_envelope(
    *,
    token_bundle: CapabilityTokenBundle,
    manifest: ComponentManifestVersion,
    authorization: ComponentAuthorization,
    workspace_id: str,
    data_sha256: str,
    data_size_bytes: int,
) -> dict[str, Any]:
    """Build the capability-token-sealed envelope for the trusted renderer.

    The raw token is handed to the host exactly once inside this envelope; the
    host programs the sandboxed iframe with ``unlock_message`` to open the
    bounded message channel. The iframe boundary and protocol caps are carried
    on the envelope so consumers never relax them.
    """
    record = token_bundle.record
    return {
        "channel": "trusted_renderer",
        "protocol_version": RENDERER_MESSAGE_PROTOCOL_VERSION,
        "sealed": True,
        "token": token_bundle.token,
        "token_id": record.id,
        "token_prefix": record.token_prefix,
        "audience": record.audience,
        "component_id": manifest.component_id,
        "workspace_id": workspace_id,
        "manifest_version_id": manifest.id,
        "manifest_version": manifest.version,
        "authorization_id": authorization.id,
        "issuer_id": manifest.issuer_id,
        "data_sha256": data_sha256,
        "data_size_bytes": data_size_bytes,
        "issued_at": record.issued_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
        "max_messages": record.max_messages,
        "max_message_bytes": MAX_RENDERER_MESSAGE_BYTES,
        "allowed_event_types": sorted(ALLOWED_IFRAME_EVENT_TYPES),
        "iframe_boundary": IFRAME_BOUNDARY,
        "unlock_message": build_renderer_message(
            event_type=RENDERER_UNLOCK_EVENT,
            payload={
                "token": token_bundle.token,
                "component_id": manifest.component_id,
                "render_ref": data_sha256,
            },
        ),
    }
