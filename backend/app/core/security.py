from __future__ import annotations

import hashlib
import hmac
import base64
import secrets
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    username: str
    tenant_id: str
    session_id: str
    display_name: str = ""
    is_system_admin: bool = False
    must_change_password: bool = False


# B1-1: password hashing moved from PBKDF2-SHA256 (600k) to Argon2id so
# verification runs in C without holding the GIL. Python 3.14's hashlib
# pbkdf2_hmac serializes on the GIL, so concurrent logins (10+) stacked the
# full 200ms+ hash per request; argon2-cffi releases the GIL and parallelizes.
# Legacy "$pbkdf2_sha256$..." hashes remain verifiable (progressive migration).
PASSWORD_SCHEME = "argon2id"
PASSWORD_ITERATIONS = 600_000  # legacy PBKDF2 fallback; new hashes use Argon2id


def normalize_identity(value: str) -> str:
    return value.strip().casefold()


def _argon2():
    from argon2 import PasswordHasher, exceptions

    # time_cost=3, memory_cost=64 MiB, parallelism=1: OWASP-recommended
    # Argon2id parameters; ~60-90ms single-shot on desktop hardware.
    return PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1), exceptions


def hash_password(password: str, *, iterations: int = PASSWORD_ITERATIONS) -> str:
    del iterations  # Argon2id parameters are fixed; iteration param kept for call-site compat
    hasher, _ = _argon2()
    return hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    if encoded.startswith("$argon2id$"):
        hasher, exceptions = _argon2()
        try:
            return hasher.verify(encoded, password)
        except exceptions.VerifyMismatchError:
            return False
        except exceptions.InvalidHashError:
            return False
    # Legacy PBKDF2-SHA256 path (existing users before the migration).
    try:
        scheme, iterations_text, salt_text, expected_text = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mask_secret(secret: str) -> tuple[str, str]:
    visible = secret[-4:] if len(secret) >= 4 else "****"
    masked = f"****{visible}"
    fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]
    return masked, fingerprint


class SecretCipher:
    """Authenticated encryption derived from the deployment master key."""

    def __init__(self, master_key: str) -> None:
        normalized = master_key.strip()
        if not normalized:
            raise ValueError("A non-empty master key is required")
        key = base64.urlsafe_b64encode(hashlib.sha256(normalized.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, secret: str) -> str:
        return self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("The provider secret cannot be decrypted with this master key") from exc
