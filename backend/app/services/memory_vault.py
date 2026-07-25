from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.errors import AppError


_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_-]+")


class MemoryRecoveryVault:
    """Stores short-lived deletion keys outside the canonical SQLite journal."""

    def __init__(self, root: Path, workspace_id: str) -> None:
        safe_workspace = _SAFE_SEGMENT.sub("_", workspace_id)
        self.root = (root.resolve() / safe_workspace / ".recovery-keys").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, memory_id: str) -> Path:
        safe_id = _SAFE_SEGMENT.sub("_", memory_id)
        path = (self.root / f"{safe_id}.key").resolve()
        if self.root not in path.parents:
            raise AppError(400, "invalid_memory_recovery_path", "Recovery key path is invalid")
        return path

    def encrypt(self, memory_id: str, payload: dict[str, Any]) -> tuple[str, str]:
        key = Fernet.generate_key()
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ciphertext = Fernet(key).encrypt(serialized).decode("ascii")
        path = self._path(memory_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(key)
        temporary.replace(path)
        relative_path = path.relative_to(self.root.parent).as_posix()
        return ciphertext, relative_path

    def decrypt(self, memory_id: str, ciphertext: str) -> dict[str, Any]:
        path = self._path(memory_id)
        if not path.exists():
            raise AppError(
                410,
                "memory_recovery_key_destroyed",
                "The memory recovery key has been destroyed",
            )
        try:
            decoded = Fernet(path.read_bytes()).decrypt(ciphertext.encode("ascii"))
            payload = json.loads(decoded)
        except (InvalidToken, json.JSONDecodeError, ValueError) as exc:
            raise AppError(
                409,
                "memory_recovery_payload_invalid",
                "The encrypted memory recovery payload failed integrity validation",
            ) from exc
        if not isinstance(payload, dict):
            raise AppError(
                409,
                "memory_recovery_payload_invalid",
                "The encrypted memory recovery payload must be an object",
            )
        return payload

    def destroy(self, memory_id: str) -> None:
        self._path(memory_id).unlink(missing_ok=True)

    def key_exists(self, memory_id: str) -> bool:
        return self._path(memory_id).exists()

