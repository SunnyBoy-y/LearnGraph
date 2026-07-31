from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class EventPayloadUnavailable(RuntimeError):
    pass


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def payload_hash(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _derive_kek(master_secret: str, key_version: int) -> bytes:
    import base64

    digest = hmac.new(
        master_secret.encode("utf-8"),
        f"learngraph-memory-events:{key_version}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest)


@dataclass(frozen=True, slots=True)
class EncryptedEventPayload:
    ciphertext: bytes
    wrapped_dek: bytes
    plaintext_hash: str
    algorithm: str = "fernet-dek-v1"


class MemoryPayloadCipher:
    """Envelope encryption using a random DEK wrapped by a versioned master key."""

    def __init__(self, master_secret: str, key_version: int = 1) -> None:
        if not master_secret.strip():
            raise ValueError("memory event master secret is required")
        self.key_version = key_version
        self._kek = Fernet(_derive_kek(master_secret, key_version))

    def encrypt(self, payload: dict[str, Any]) -> EncryptedEventPayload:
        dek = Fernet.generate_key()
        plaintext = canonical_json_bytes(payload)
        return EncryptedEventPayload(
            ciphertext=Fernet(dek).encrypt(plaintext),
            wrapped_dek=self._kek.encrypt(dek),
            plaintext_hash="sha256:" + hashlib.sha256(plaintext).hexdigest(),
        )

    def decrypt(self, ciphertext: bytes | None, wrapped_dek: bytes | None) -> dict[str, Any]:
        if not ciphertext or not wrapped_dek:
            raise EventPayloadUnavailable("event payload key was destroyed or payload was redacted")
        try:
            dek = self._kek.decrypt(wrapped_dek)
            plaintext = Fernet(dek).decrypt(ciphertext)
        except InvalidToken as exc:
            raise EventPayloadUnavailable("event payload could not be decrypted") from exc
        decoded = json.loads(plaintext.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise EventPayloadUnavailable("event payload must decode to an object")
        return decoded


def development_event_secret() -> str:
    """Process-local fallback for dev/test only; production must configure a secret."""

    return os.environ.setdefault(
        "LEARNGRAPH_MEMORY_EVENT_EPHEMERAL_KEY",
        Fernet.generate_key().decode("ascii"),
    )
