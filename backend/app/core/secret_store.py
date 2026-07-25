from __future__ import annotations

import secrets
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from app.core.config import Settings


class SecretStoreUnavailable(RuntimeError):
    """Raised when the configured master-key provider cannot be used safely."""


class MasterKeyNotFound(SecretStoreUnavailable):
    """Raised when a ciphertext references a master-key version that is absent."""


@dataclass(frozen=True, slots=True)
class MasterKeyMaterial:
    version: int
    secret: str


@dataclass(frozen=True, slots=True)
class SecretStoreStatus:
    provider: str
    available: bool
    secure_backend: bool
    backend_name: str
    active_key_version: int | None


class SecretStore(Protocol):
    provider_name: str

    def status(self) -> SecretStoreStatus: ...

    def active_key(self, *, create: bool = False) -> MasterKeyMaterial: ...

    def key(self, version: int) -> MasterKeyMaterial: ...

    def identity_key(self, *, create: bool = False) -> str: ...

    def rotate_key(self) -> tuple[MasterKeyMaterial, MasterKeyMaterial]: ...


class EnvironmentSecretStore:
    """Explicit compatibility provider backed by a deployment environment secret."""

    provider_name = "environment"

    def __init__(self, settings: Settings) -> None:
        self._secret = (settings.master_key or "").strip()
        self._version = settings.master_key_version

    def status(self) -> SecretStoreStatus:
        available = bool(self._secret)
        return SecretStoreStatus(
            provider=self.provider_name,
            available=available,
            secure_backend=available,
            backend_name="deployment_environment",
            active_key_version=self._version if available else None,
        )

    def active_key(self, *, create: bool = False) -> MasterKeyMaterial:
        del create
        if not self._secret:
            raise SecretStoreUnavailable(
                "The environment secret provider has no configured master key"
            )
        return MasterKeyMaterial(self._version, self._secret)

    def key(self, version: int) -> MasterKeyMaterial:
        if version != self._version:
            raise MasterKeyNotFound(
                f"Environment master-key version {version} is not configured"
            )
        return self.active_key()

    def identity_key(self, *, create: bool = False) -> str:
        return self.active_key(create=create).secret

    def rotate_key(self) -> tuple[MasterKeyMaterial, MasterKeyMaterial]:
        raise SecretStoreUnavailable(
            "Environment master keys must be rotated by deployment configuration; "
            "use the keyring provider for in-application master-key rotation"
        )


_KEYRING_WRITE_LOCK = RLock()


class KeyringSecretStore:
    """Versioned master keys held by the operating-system Keyring backend."""

    provider_name = "keyring"

    def __init__(self, settings: Settings) -> None:
        self._service = settings.keyring_service_name.strip()
        self._prefix = settings.keyring_account_prefix.strip()
        if not self._service or not self._prefix:
            raise SecretStoreUnavailable("Keyring service and account names must be non-empty")

    @staticmethod
    def _module() -> object:
        try:
            import keyring
            from keyring.errors import KeyringError, NoKeyringError
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise SecretStoreUnavailable("The keyring package is not installed") from exc
        return keyring, KeyringError, NoKeyringError

    def _backend(self):
        keyring, _, _ = self._module()
        try:
            backend = keyring.get_keyring()
            priority = float(getattr(backend, "priority", 0))
        except Exception as exc:
            raise SecretStoreUnavailable("The operating-system Keyring backend is unavailable") from exc
        backend_name = f"{backend.__class__.__module__}.{backend.__class__.__name__}"
        lowered = backend_name.casefold()
        if priority <= 0 or "fail.keyring" in lowered or "plaintext" in lowered:
            raise SecretStoreUnavailable(
                "The configured Keyring backend is unavailable or stores plaintext"
            )
        return keyring, backend, backend_name

    @property
    def _active_account(self) -> str:
        return f"{self._prefix}.active-version"

    @property
    def _identity_account(self) -> str:
        return f"{self._prefix}.identity-key"

    def _key_account(self, version: int) -> str:
        return f"{self._prefix}.v{version}"

    def _get_password(self, account: str) -> str | None:
        keyring, _, _ = self._backend()
        try:
            return keyring.get_password(self._service, account)
        except Exception as exc:
            raise SecretStoreUnavailable("The operating-system Keyring could not be read") from exc

    def _set_password(self, account: str, value: str) -> None:
        keyring, _, _ = self._backend()
        try:
            keyring.set_password(self._service, account, value)
        except Exception as exc:
            raise SecretStoreUnavailable("The operating-system Keyring could not be written") from exc

    def status(self) -> SecretStoreStatus:
        try:
            _, _, backend_name = self._backend()
            active_text = self._get_password(self._active_account)
            version = int(active_text) if active_text else None
            if version is not None and not self._get_password(self._key_account(version)):
                raise SecretStoreUnavailable(
                    "The active Keyring version points to a missing master key"
                )
            return SecretStoreStatus(
                provider=self.provider_name,
                available=True,
                secure_backend=True,
                backend_name=backend_name,
                active_key_version=version,
            )
        except (TypeError, ValueError) as exc:
            raise SecretStoreUnavailable("The Keyring active version is invalid") from exc

    def active_key(self, *, create: bool = False) -> MasterKeyMaterial:
        with _KEYRING_WRITE_LOCK:
            active_text = self._get_password(self._active_account)
            if not active_text:
                if not create:
                    raise MasterKeyNotFound("No active Keyring master key exists")
                version = 1
                material = secrets.token_urlsafe(48)
                self._set_password(self._key_account(version), material)
                self._set_password(self._active_account, str(version))
                return MasterKeyMaterial(version, material)
            try:
                version = int(active_text)
            except ValueError as exc:
                raise SecretStoreUnavailable("The Keyring active version is invalid") from exc
            return self.key(version)

    def key(self, version: int) -> MasterKeyMaterial:
        if version < 1:
            raise MasterKeyNotFound("Master-key versions start at 1")
        material = self._get_password(self._key_account(version))
        if not material:
            raise MasterKeyNotFound(f"Keyring master-key version {version} is missing")
        return MasterKeyMaterial(version, material)

    def identity_key(self, *, create: bool = False) -> str:
        with _KEYRING_WRITE_LOCK:
            material = self._get_password(self._identity_account)
            if material:
                return material
            if not create:
                raise MasterKeyNotFound("No Keyring identity key exists")
            material = secrets.token_urlsafe(48)
            self._set_password(self._identity_account, material)
            return material

    def rotate_key(self) -> tuple[MasterKeyMaterial, MasterKeyMaterial]:
        with _KEYRING_WRITE_LOCK:
            previous = self.active_key(create=True)
            current = MasterKeyMaterial(previous.version + 1, secrets.token_urlsafe(48))
            # Store key material before publishing the active pointer.  A crash can
            # leave an unused version, but can never make existing ciphertext unreadable.
            self._set_password(self._key_account(current.version), current.secret)
            self._set_password(self._active_account, str(current.version))
            return previous, current


def secret_store_from_settings(
    settings: Settings, *, provider_name: str | None = None
) -> SecretStore:
    selected = provider_name or settings.secret_provider
    if selected == "keyring":
        return KeyringSecretStore(settings)
    if selected == "environment":
        return EnvironmentSecretStore(settings)
    raise SecretStoreUnavailable(f"Unknown secret provider: {selected}")
