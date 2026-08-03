from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import ComponentIssuer
from app.domain.schemas.components import ComponentManifestImportRequest, ComponentSignatureDeclaration


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> bytes:
    import json

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes | Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical_json(value)
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class SignatureVerificationResult:
    status: str
    info: dict[str, Any]
    trusted_bundle_eligible: bool
    issuer_id: str | None = None


def signing_material(
    payload: ComponentManifestImportRequest,
    *,
    renderer: str,
    permissions: dict[str, Any],
) -> dict[str, Any]:
    """Canonical bytes covered by a component package signature."""

    return {
        "component_id": payload.component_id,
        "version": payload.version,
        "display_name": payload.display_name,
        "renderer": renderer,
        "source": payload.source,
        "author": payload.author,
        "package_hash": payload.package_hash,
        "compatible_learngraph": payload.compatible_learngraph,
        "uninstall_behavior": payload.uninstall_behavior,
        "data_schema": payload.data_schema,
        "event_schema": payload.event_schema,
        "permissions": permissions,
        "size_limits": payload.size_limits.model_dump(),
        "skill_triggers": payload.skill_triggers,
        "example_data": payload.example_data,
    }


def verify_component_signature(
    db: Session,
    workspace_id: str,
    payload: ComponentManifestImportRequest,
    *,
    renderer: str,
    permissions: dict[str, Any],
) -> SignatureVerificationResult:
    """Server-side only signature verification against registered issuers.

    Client/agent declarations never self-assert trusted status. Missing trust
    store material yields an explicit non-trusted status and keeps the safe
    sandbox_artifact path.
    """

    if payload.signature is None:
        return SignatureVerificationResult(
            status="unsigned",
            info={},
            trusted_bundle_eligible=False,
        )

    signature = payload.signature
    try:
        signature_bytes = base64.b64decode(signature.signature_base64, validate=True)
    except Exception as exc:  # noqa: BLE001 - surface as validation error
        raise AppError(
            422,
            "component_signature_invalid",
            "Component signature is not valid base64",
        ) from exc

    issuer = db.scalar(
        select(ComponentIssuer).where(
            ComponentIssuer.workspace_id == workspace_id,
            ComponentIssuer.key_id == signature.key_id,
            ComponentIssuer.algorithm == signature.algorithm,
        )
    )
    if issuer is None:
        return SignatureVerificationResult(
            status="unverified",
            info={
                "algorithm": signature.algorithm,
                "key_id": signature.key_id,
                "signature_sha256": _sha256(signature_bytes),
                "reason": "issuer_not_registered",
            },
            trusted_bundle_eligible=False,
        )
    if issuer.status != "active" or issuer.revoked_at is not None:
        return SignatureVerificationResult(
            status="revoked",
            info={
                "algorithm": signature.algorithm,
                "key_id": signature.key_id,
                "issuer_id": issuer.id,
                "signature_sha256": _sha256(signature_bytes),
                "reason": "issuer_revoked",
                "revoke_reason": issuer.revoke_reason,
            },
            trusted_bundle_eligible=False,
            issuer_id=issuer.id,
        )

    material = signing_material(payload, renderer=renderer, permissions=permissions)
    message = _canonical_json(material)
    if signature.algorithm != "ed25519":
        return SignatureVerificationResult(
            status="unverified",
            info={
                "algorithm": signature.algorithm,
                "key_id": signature.key_id,
                "issuer_id": issuer.id,
                "signature_sha256": _sha256(signature_bytes),
                "reason": "algorithm_not_supported_for_verification",
            },
            trusted_bundle_eligible=False,
            issuer_id=issuer.id,
        )

    try:
        public_key = serialization.load_pem_public_key(issuer.public_key_pem.encode("utf-8"))
        if not isinstance(public_key, Ed25519PublicKey):
            raise AppError(
                422,
                "component_issuer_key_invalid",
                "Registered issuer public key is not an Ed25519 key",
            )
        public_key.verify(signature_bytes, message)
    except InvalidSignature:
        return SignatureVerificationResult(
            status="invalid",
            info={
                "algorithm": signature.algorithm,
                "key_id": signature.key_id,
                "issuer_id": issuer.id,
                "signature_sha256": _sha256(signature_bytes),
                "reason": "signature_mismatch",
            },
            trusted_bundle_eligible=False,
            issuer_id=issuer.id,
        )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            422,
            "component_issuer_key_invalid",
            "Registered issuer public key could not be loaded",
            {"error": type(exc).__name__},
        ) from exc

    return SignatureVerificationResult(
        status="verified",
        info={
            "algorithm": signature.algorithm,
            "key_id": signature.key_id,
            "issuer_id": issuer.id,
            "issuer_key": issuer.issuer_key,
            "signature_sha256": _sha256(signature_bytes),
            "package_hash": payload.package_hash,
            "verified_at": _utc_now().isoformat(),
        },
        trusted_bundle_eligible=True,
        issuer_id=issuer.id,
    )


def register_component_issuer(
    db: Session,
    *,
    workspace_id: str,
    issuer_key: str,
    display_name: str,
    key_id: str,
    algorithm: str,
    public_key_pem: str,
    rotated_from_key_id: str | None = None,
) -> ComponentIssuer:
    """Register or replace an active issuer key for the workspace trust store."""

    if algorithm != "ed25519":
        raise AppError(
            422,
            "component_issuer_algorithm_unsupported",
            "Only ed25519 issuers are accepted in the current trust store",
        )
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            422,
            "component_issuer_key_invalid",
            "Issuer public key PEM is invalid",
        ) from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise AppError(
            422,
            "component_issuer_key_invalid",
            "Issuer public key must be Ed25519",
        )

    existing = db.scalar(
        select(ComponentIssuer).where(
            ComponentIssuer.workspace_id == workspace_id,
            ComponentIssuer.key_id == key_id,
        )
    )
    if existing is not None:
        existing.display_name = display_name
        existing.issuer_key = issuer_key
        existing.algorithm = algorithm
        existing.public_key_pem = public_key_pem
        existing.status = "active"
        existing.revoked_at = None
        existing.revoke_reason = ""
        existing.rotated_from_key_id = rotated_from_key_id
        db.commit()
        db.refresh(existing)
        return existing

    issuer = ComponentIssuer(
        workspace_id=workspace_id,
        issuer_key=issuer_key,
        display_name=display_name,
        key_id=key_id,
        algorithm=algorithm,
        public_key_pem=public_key_pem,
        rotated_from_key_id=rotated_from_key_id,
    )
    db.add(issuer)
    db.commit()
    db.refresh(issuer)
    return issuer


def revoke_component_issuer(
    db: Session,
    *,
    workspace_id: str,
    key_id: str,
    reason: str,
) -> ComponentIssuer:
    issuer = db.scalar(
        select(ComponentIssuer).where(
            ComponentIssuer.workspace_id == workspace_id,
            ComponentIssuer.key_id == key_id,
        )
    )
    if issuer is None:
        raise AppError(404, "component_issuer_not_found", "Component issuer key was not found")
    issuer.status = "revoked"
    issuer.revoked_at = _utc_now()
    issuer.revoke_reason = reason[:240]
    db.commit()
    db.refresh(issuer)
    return issuer


def resolve_active_issuer(
    db: Session,
    workspace_id: str,
    issuer_id: str | None,
) -> ComponentIssuer | None:
    """Return the issuer only when it is still active in this workspace.

    Called again at delivery time (not just at registration) so a revocation or
    cross-workspace row can never keep a stored trust flag effective.
    """
    if not issuer_id:
        return None
    issuer = db.get(ComponentIssuer, issuer_id)
    if issuer is None or issuer.workspace_id != workspace_id:
        return None
    if issuer.status != "active" or issuer.revoked_at is not None:
        return None
    return issuer


def assert_signature_declaration(signature: ComponentSignatureDeclaration | None) -> None:
    if signature is None:
        return
    if signature.algorithm not in {"ed25519", "ecdsa-p256-sha256"}:
        raise AppError(
            422,
            "component_signature_algorithm_unsupported",
            "Unsupported component signature algorithm",
        )
