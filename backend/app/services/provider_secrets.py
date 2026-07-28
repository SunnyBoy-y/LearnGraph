from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.secret_store import SecretStoreUnavailable, secret_store_from_settings
from app.core.security import SecretCipher
from app.domain.models import ProviderSecret


PROVIDER_SECRET_ALGORITHM = "fernet_sha256_v1"


class ProviderSecretUnavailable(RuntimeError):
    """A Provider secret exists but must not be used for a remote call."""


class ProviderSecretRevoked(ProviderSecretUnavailable):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedProviderSecret:
    ciphertext: str
    algorithm: str
    key_provider: str
    key_version: int


def encrypt_provider_secret(settings: Settings, plaintext: str) -> EncryptedProviderSecret:
    if not plaintext:
        raise ValueError("Provider secrets must be non-empty")
    store = secret_store_from_settings(settings)
    key = store.active_key(create=True)
    return EncryptedProviderSecret(
        ciphertext=SecretCipher(key.secret).encrypt(plaintext),
        algorithm=PROVIDER_SECRET_ALGORITHM,
        key_provider=settings.secret_provider,
        key_version=key.version,
    )


def decrypt_provider_secret(settings: Settings, record: ProviderSecret) -> str:
    if record.revoked_at is not None or not record.ciphertext:
        raise ProviderSecretRevoked("The Provider secret has been revoked")
    if record.algorithm != PROVIDER_SECRET_ALGORITHM:
        raise ProviderSecretUnavailable(
            f"Unsupported Provider secret algorithm: {record.algorithm}"
        )
    try:
        key = secret_store_from_settings(
            settings, provider_name=record.key_provider
        ).key(record.key_version)
        return SecretCipher(key.secret).decrypt(record.ciphertext)
    except SecretStoreUnavailable as exc:
        raise ProviderSecretUnavailable("The configured master key is unavailable") from exc
    except ValueError as exc:
        raise ProviderSecretUnavailable("The Provider secret cannot be decrypted") from exc


def decrypt_secret_fields(
    settings: Settings,
    *,
    ciphertext: str,
    algorithm: str,
    key_provider: str,
    key_version: int,
) -> str:
    """Open an opaque labelled secret without exposing a public read API."""

    if algorithm != PROVIDER_SECRET_ALGORITHM:
        raise ProviderSecretUnavailable(
            f"Unsupported secret algorithm: {algorithm}"
        )
    try:
        key = secret_store_from_settings(
            settings, provider_name=key_provider
        ).key(key_version)
        return SecretCipher(key.secret).decrypt(ciphertext)
    except SecretStoreUnavailable as exc:
        raise ProviderSecretUnavailable("The configured master key is unavailable") from exc
    except ValueError as exc:
        raise ProviderSecretUnavailable("The labelled secret cannot be decrypted") from exc
