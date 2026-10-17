"""Short-lived, single-file capabilities for native browser media requests."""
from __future__ import annotations

import json
import time

from app.core.config import Settings
from app.core.errors import AppError
from app.core.secret_store import SecretStoreUnavailable, secret_store_from_settings
from app.core.security import SecretCipher


def issue_file_stream_ticket(settings: Settings, file_id: str, workspace_id: str, bearer: str) -> str:
    try:
        key = secret_store_from_settings(settings).active_key(create=True)
        payload = json.dumps({"file": file_id, "workspace": workspace_id, "bearer": bearer, "expires": int(time.time()) + 3600}, separators=(",", ":"))
        return f"{key.version}.{SecretCipher('file-stream-v1:' + key.secret).encrypt(payload)}"
    except SecretStoreUnavailable as exc:
        raise AppError(503, "file_stream_unavailable", "File streaming requires the configured secret store") from exc


def read_file_stream_ticket(settings: Settings, file_id: str, ticket: str) -> dict:
    try:
        version, encrypted = ticket.split(".", 1)
        key = secret_store_from_settings(settings).key(int(version))
        value = json.loads(SecretCipher('file-stream-v1:' + key.secret).decrypt(encrypted))
        if value["file"] != file_id or value["expires"] <= time.time() or not value["bearer"] or not value["workspace"]:
            raise ValueError("expired or mismatched ticket")
        return value
    except (ValueError, KeyError, TypeError, SecretStoreUnavailable) as exc:
        raise AppError(401, "file_stream_ticket_invalid", "File stream link is invalid or expired") from exc
