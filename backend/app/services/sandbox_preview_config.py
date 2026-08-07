"""Deployment-scoped subapp preview origin configuration.

The preview origin is a deployment-wide (not per-workspace) setting: every
bundle capability URL points at the same independent origin. An administrator
persists the origin here through the frontend settings page; the environment
variable ``LEARNGRAPH_SUBAPP_PREVIEW_ORIGIN`` and the local preview port provide
defaults so development needs no configuration.

Priority: persisted config → env override → derived ``http://127.0.0.1:PORT`` → None.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from app.core.config import Settings

_lock = threading.RLock()

_LOCAL_PREVIEW_HOSTS = frozenset({"localhost", "127.0.0.1"})
_PREVIEW_ORIGIN_MAX_LENGTH = 255


def preview_config_path(settings: Settings) -> Path:
    """Sibling of sandbox-runtime.json; deployment-scoped preview origin."""

    from app.services.sandbox_runtime import runtime_config_path

    return runtime_config_path(settings).with_name("sandbox-preview-config.json")


@dataclass(frozen=True, slots=True)
class PreviewOriginConfig:
    origin: str
    updated_at: str | None
    updated_by: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def load_preview_config(settings: Settings) -> PreviewOriginConfig | None:
    path = preview_config_path(settings)
    with _lock:
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        origin = raw.get("origin")
        if not isinstance(origin, str) or not origin.strip():
            return None
        try:
            validate_preview_origin(origin)
        except ValueError:
            return None
        return PreviewOriginConfig(
            origin=origin.strip(),
            updated_at=str(raw.get("updated_at")) if raw.get("updated_at") else None,
            updated_by=str(raw.get("updated_by")) if raw.get("updated_by") else None,
        )


def save_preview_config(
    settings: Settings, *, origin: str, actor_id: str | None
) -> PreviewOriginConfig:
    normalized = validate_preview_origin(origin)
    config = PreviewOriginConfig(
        origin=normalized,
        updated_at=datetime.now(timezone.utc).isoformat(),
        updated_by=(actor_id or "").strip() or None,
    )
    path = preview_config_path(settings)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return config


def validate_preview_origin(origin: str) -> str:
    """Normalize and validate an independent preview origin.

    Accepts:
      - ``https://host[:port]`` — any public/TLS preview domain;
      - ``http://localhost:<port>`` / ``http://127.0.0.1:<port>`` — local dev.

    Rejects plaintext non-local origins, origins with a path/query/fragment,
    userinfo credentials, and empty values.
    """
    if not isinstance(origin, str) or not origin.strip():
        raise ValueError("preview origin must be a non-empty https:// or local http:// URL")
    value = origin.strip()
    if len(value) > _PREVIEW_ORIGIN_MAX_LENGTH:
        raise ValueError("preview origin exceeds the length limit")
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise ValueError("preview origin is not a valid URL") from exc
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise ValueError("preview origin must be an absolute https:// or http:// URL")
    if parts.username or parts.password:
        raise ValueError("preview origin must not contain userinfo credentials")
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ValueError("preview origin must be a bare origin without path, query, or fragment")
    if parts.scheme == "http" and parts.hostname.casefold() not in _LOCAL_PREVIEW_HOSTS:
        raise ValueError("plaintext http preview origins must use localhost or 127.0.0.1")
    return f"{parts.scheme}://{parts.netloc}".rstrip("/")


def effective_subapp_preview_origin(settings: Settings) -> str | None:
    """Resolve the origin minted into bundle capability URLs.

    Persisted administrator choice wins; then the env override; then a local
    derivation from ``LEARNGRAPH_SUBAPP_PREVIEW_PORT``; otherwise None (fail
    closed when no preview origin is available).
    """
    persisted = load_preview_config(settings)
    if persisted is not None:
        return persisted.origin
    env_value = (settings.subapp_preview_origin or "").strip()
    if env_value:
        try:
            return validate_preview_origin(env_value)
        except ValueError:
            return None
    if settings.subapp_preview_port:
        return f"http://127.0.0.1:{int(settings.subapp_preview_port)}"
    return None
