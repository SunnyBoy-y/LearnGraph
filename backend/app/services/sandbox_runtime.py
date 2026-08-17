"""Persisted sandbox runtime configuration (image digest).

Product path: Bootstrap writes an immutable digest here so operators and
end users do not need to hand-edit LEARNGRAPH_SANDBOX_IMAGE.  An explicit
environment variable still wins for CI / offline overrides.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.providers.remote.sandbox import image_ref_is_pinned

_lock = threading.RLock()


def runtime_config_path(settings: Settings) -> Path:
    """Store next to the local data root (sibling of storage/)."""

    storage = Path(settings.storage_root).expanduser()
    try:
        storage = storage.resolve()
    except OSError:
        storage = storage.absolute()
    return storage.parent / "sandbox-runtime.json"


def bootstrap_policy_path(settings: Settings) -> Path:
    """Sibling of sandbox-runtime.json; deployment-scoped bootstrap gate."""

    return runtime_config_path(settings).with_name("sandbox-bootstrap-policy.json")


BOOTSTRAP_SOURCE_MODES = ("auto", "prebuilt", "build")


@dataclass(frozen=True, slots=True)
class SandboxBootstrapSourceConfig:
    """Deployment-level sandbox image source selection (page-configurable).

    ``mode`` is the operator's intended strategy:

    - ``auto``: pull the prebuilt image when one is configured, otherwise
      fall back to a local Docker build (default when nothing is persisted).
    - ``prebuilt``: always require the configured prebuilt image; never build
      locally.
    - ``build``: always build locally, ignoring any prebuilt image.

    ``prebuilt_image`` holds the registry reference chosen on the settings
    page; the deployment environment variable
    ``LEARNGRAPH_SANDBOX_PREBUILT_IMAGE`` takes precedence over it when set.
    """

    mode: str
    prebuilt_image: str | None
    updated_at: str | None
    updated_by: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "prebuilt_image": self.prebuilt_image,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def bootstrap_source_path(settings: Settings) -> Path:
    """Sibling of sandbox-bootstrap-policy.json; page-configured image source."""

    return bootstrap_policy_path(settings).with_name("sandbox-bootstrap-source.json")


def load_bootstrap_source(settings: Settings) -> SandboxBootstrapSourceConfig | None:
    path = bootstrap_source_path(settings)
    with _lock:
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if not isinstance(raw, dict):
        return None
    mode = str(raw.get("mode") or "").strip()
    if mode not in BOOTSTRAP_SOURCE_MODES:
        return None
    prebuilt = str(raw.get("prebuilt_image") or "").strip() or None
    return SandboxBootstrapSourceConfig(
        mode=mode,
        prebuilt_image=prebuilt,
        updated_at=str(raw.get("updated_at")) if raw.get("updated_at") else None,
        updated_by=str(raw.get("updated_by")) if raw.get("updated_by") else None,
    )


def save_bootstrap_source(
    settings: Settings,
    *,
    mode: str,
    prebuilt_image: str | None,
    actor_id: str | None,
) -> SandboxBootstrapSourceConfig:
    if mode not in BOOTSTRAP_SOURCE_MODES:
        raise ValueError(f"Unsupported sandbox bootstrap source mode: {mode}")
    config = SandboxBootstrapSourceConfig(
        mode=mode,
        prebuilt_image=(prebuilt_image or "").strip() or None,
        updated_at=datetime.now(timezone.utc).isoformat(),
        updated_by=(actor_id or "").strip() or None,
    )
    path = bootstrap_source_path(settings)
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


def effective_bootstrap_source(settings: Settings) -> tuple[str, str | None]:
    """Resolve (mode, prebuilt_image) with env taking precedence for the ref.

    The persisted mode decides the strategy; the deployment environment
    variable ``LEARNGRAPH_SANDBOX_PREBUILT_IMAGE`` overrides the persisted
    image reference when present (backwards compatible with env-only
    deployments). A ``build`` mode still ignores any prebuilt reference.
    """

    source = load_bootstrap_source(settings)
    mode = source.mode if source is not None else "auto"
    prebuilt = source.prebuilt_image if source is not None else None
    env_ref = (settings.effective_sandbox_prebuilt_image or "").strip()
    if env_ref:
        prebuilt = env_ref
    return mode, prebuilt


@dataclass(frozen=True, slots=True)
class SandboxBootstrapPolicy:
    """Deployment-level gate for sandbox runtime bootstrap.

    ``member_allowed=True`` lets ordinary workspace members trigger the local
    image build; ``False`` restricts it to administrators.  The deployment
    default comes from ``Settings.sandbox_bootstrap_member_allowed`` until an
    administrator persists an explicit choice here.
    """

    member_allowed: bool
    updated_at: str | None
    updated_by: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "member_allowed": self.member_allowed,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def load_bootstrap_policy(settings: Settings) -> SandboxBootstrapPolicy | None:
    path = bootstrap_policy_path(settings)
    with _lock:
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if not isinstance(raw, dict) or not isinstance(raw.get("member_allowed"), bool):
        return None
    return SandboxBootstrapPolicy(
        member_allowed=raw["member_allowed"],
        updated_at=str(raw.get("updated_at")) if raw.get("updated_at") else None,
        updated_by=str(raw.get("updated_by")) if raw.get("updated_by") else None,
    )


def save_bootstrap_policy(
    settings: Settings, *, member_allowed: bool, actor_id: str | None
) -> SandboxBootstrapPolicy:
    policy = SandboxBootstrapPolicy(
        member_allowed=member_allowed,
        updated_at=datetime.now(timezone.utc).isoformat(),
        updated_by=(actor_id or "").strip() or None,
    )
    path = bootstrap_policy_path(settings)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(policy.to_dict(), ensure_ascii=False, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return policy


def effective_member_bootstrap_allowed(settings: Settings) -> bool:
    """Persisted administrator choice wins; otherwise the deployment default."""

    persisted = load_bootstrap_policy(settings)
    if persisted is not None:
        return persisted.member_allowed
    return settings.sandbox_bootstrap_member_allowed


@dataclass(frozen=True, slots=True)
class SandboxRuntimeConfig:
    image_digest: str
    browser_image_digest: str | None
    source: str
    built_at: str | None
    builder_user_id: str | None
    tag: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_digest": self.image_digest,
            "browser_image_digest": self.browser_image_digest,
            "source": self.source,
            "built_at": self.built_at,
            "builder_user_id": self.builder_user_id,
            "tag": self.tag,
        }


def load_runtime_config(settings: Settings) -> SandboxRuntimeConfig | None:
    path = runtime_config_path(settings)
    with _lock:
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if not isinstance(raw, dict):
        return None
    digest = str(raw.get("image_digest") or "").strip()
    if not digest or not image_ref_is_pinned(digest):
        return None
    return SandboxRuntimeConfig(
        image_digest=digest,
        browser_image_digest=(
            str(raw.get("browser_image_digest"))
            if raw.get("browser_image_digest")
            and image_ref_is_pinned(str(raw.get("browser_image_digest")))
            else None
        ),
        source=str(raw.get("source") or "unknown"),
        built_at=str(raw.get("built_at")) if raw.get("built_at") else None,
        builder_user_id=str(raw.get("builder_user_id")) if raw.get("builder_user_id") else None,
        tag=str(raw.get("tag")) if raw.get("tag") else None,
    )


def save_runtime_config(
    settings: Settings,
    *,
    image_digest: str,
    source: str,
    builder_user_id: str | None,
    tag: str | None = None,
    browser_image_digest: str | None = None,
) -> SandboxRuntimeConfig:
    digest = image_digest.strip()
    if not image_ref_is_pinned(digest):
        raise ValueError("Sandbox runtime image must be an immutable sha256 digest")
    if browser_image_digest and not image_ref_is_pinned(browser_image_digest):
        raise ValueError("Sandbox browser image must be an immutable sha256 digest")
    config = SandboxRuntimeConfig(
        image_digest=digest,
        browser_image_digest=browser_image_digest,
        source=source,
        built_at=datetime.now(timezone.utc).isoformat(),
        builder_user_id=builder_user_id,
        tag=tag,
    )
    path = runtime_config_path(settings)
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


def resolve_sandbox_image(settings: Settings) -> str | None:
    """Env override first, then persisted Bootstrap config."""

    env_image = (settings.sandbox_image or "").strip()
    if env_image:
        return env_image
    persisted = load_runtime_config(settings)
    if persisted is not None:
        return persisted.image_digest
    return None


def resolve_sandbox_image_for_runtime(
    settings: Settings, runtime_kind: str
) -> str | None:
    """Resolve an immutable image without exposing image selection to callers.

    Both runtime kinds map to the unified runner image (Chromium, ffmpeg and
    the frontend toolchain ship in one image).  The ``browser_image_digest``
    runtime-config field is kept for compatibility and mirrors the same
    digest.
    """

    if runtime_kind in {"python-node", "python-node-browser"}:
        return resolve_sandbox_image(settings)
    return None
