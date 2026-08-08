"""Versioned secret-store encryption for MCP credentials.

MCP bearer/OAuth ciphertext is opaque to every API and agent surface, but it
must share the same versioned master-key lifecycle as Provider secrets so a
keyring rotation can resolve older keys instead of failing closed. New values
are stored as a self-describing JSON envelope; legacy values encrypted directly
with ``SecretCipher(settings.master_key)`` keep working through the fallback
path so existing registrations remain readable.
"""

from __future__ import annotations

import json

from app.core.config import Settings
from app.core.errors import AppError
from app.core.secret_store import SecretStoreUnavailable, secret_store_from_settings
from app.core.security import SecretCipher
from app.services.provider_secrets import (
    PROVIDER_SECRET_ALGORITHM,
    encrypt_provider_secret,
)

_MCP_SECRET_ENVELOPE_VERSION = 1
_MCP_SECRET_ALGORITHM = PROVIDER_SECRET_ALGORITHM


def encrypt_mcp_secret(settings: Settings, plaintext: str) -> str:
    """Encrypt an MCP credential with the configured versioned secret store.

    The returned envelope carries the algorithm, provider and key version so a
    later key rotation can still resolve the exact key used at write time.
    """

    if not plaintext:
        raise ValueError("MCP secrets must be non-empty")
    try:
        encrypted = encrypt_provider_secret(settings, plaintext)
    except SecretStoreUnavailable as exc:
        raise AppError(
            503,
            "secret_store_unavailable",
            "MCP credentials require the configured encrypted secret store",
        ) from exc
    envelope = {
        "v": _MCP_SECRET_ENVELOPE_VERSION,
        "alg": encrypted.algorithm,
        "provider": encrypted.key_provider,
        "key_version": encrypted.key_version,
        "cipher": encrypted.ciphertext,
    }
    return json.dumps(envelope, separators=(",", ":"))


def decrypt_mcp_secret(settings: Settings, ciphertext: str | None, *, label: str) -> str:
    """Open an MCP credential using the secret store key named by its envelope.

    Legacy ciphertext written by the pre-versioning ``SecretCipher(master_key)``
    path is accepted as-is; it only works while the same master key is still
    configured. New values always carry their own algorithm/key-provider/version.
    """

    if not ciphertext:
        raise AppError(409, "mcp_credential_missing", f"{label} is not available")

    envelope = _parse_envelope(ciphertext, label=label)
    if envelope is not None:
        return _decrypt_envelope(settings, envelope, label=label)

    if not settings.has_master_key:
        raise AppError(
            503,
            "secret_store_unavailable",
            f"{label} cannot be opened because the master key is unavailable",
        )
    try:
        return SecretCipher(settings.master_key).decrypt(ciphertext)
    except ValueError as exc:
        raise AppError(
            500,
            "mcp_credential_decrypt_failed",
            f"{label} cannot be decrypted with the configured secret store key",
        ) from exc


def _parse_envelope(ciphertext: str, *, label: str) -> dict | None:
    if not ciphertext.startswith("{"):
        return None
    try:
        parsed = json.loads(ciphertext)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    version = parsed.get("v")
    algorithm = parsed.get("alg")
    provider = parsed.get("provider")
    key_version = parsed.get("key_version")
    cipher = parsed.get("cipher")
    if (
        version != _MCP_SECRET_ENVELOPE_VERSION
        or not isinstance(algorithm, str)
        or not isinstance(provider, str)
        or not isinstance(key_version, int)
        or not isinstance(cipher, str)
    ):
        raise AppError(
            500,
            "mcp_credential_invalid",
            f"{label} has an invalid encrypted-secret envelope",
        )
    return {
        "alg": algorithm,
        "provider": provider,
        "key_version": key_version,
        "cipher": cipher,
    }


def _decrypt_envelope(settings: Settings, envelope: dict, *, label: str) -> str:
    if envelope["alg"] != _MCP_SECRET_ALGORITHM:
        raise AppError(
            500,
            "mcp_credential_invalid",
            f"{label} uses an unsupported encryption algorithm: {envelope['alg']}",
        )
    try:
        key = secret_store_from_settings(
            settings, provider_name=envelope["provider"]
        ).key(envelope["key_version"])
        return SecretCipher(key.secret).decrypt(envelope["cipher"])
    except SecretStoreUnavailable as exc:
        raise AppError(
            503,
            "secret_store_unavailable",
            f"{label} cannot be opened because the configured secret store is unavailable",
        ) from exc
    except ValueError as exc:
        raise AppError(
            500,
            "mcp_credential_decrypt_failed",
            f"{label} cannot be decrypted with the configured secret store key",
        ) from exc
