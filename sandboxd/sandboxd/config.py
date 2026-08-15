"""sandboxd configuration (env-driven, fail closed)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Protocol / runner ABI negotiation bounds (see protocol.py).
PROTOCOL_MIN = "1.0"
PROTOCOL_MAX = "1.0"
RUNNER_ABI_MIN = "1"
RUNNER_ABI_MAX = "1"


class SandboxdConfigError(RuntimeError):
    """Raised when the daemon configuration is invalid or incomplete."""


def _read_token(path: str | None) -> str:
    if not path:
        raise SandboxdConfigError("SANDBOXD_TOKEN_FILE is required")
    token_file = Path(path)
    if not token_file.is_file():
        raise SandboxdConfigError(f"sandboxd token file does not exist: {path}")
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SandboxdConfigError(f"cannot read sandboxd token file {path}: {exc}") from exc
    if not token:
        raise SandboxdConfigError("sandboxd token file is empty")
    return token


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SandboxdConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise SandboxdConfigError(f"{name} must be within [{minimum}, {maximum}]")
    return value


@dataclass(frozen=True, slots=True)
class SandboxdConfig:
    listen_host: str
    port: int
    token: str
    admin_token: str | None
    state_path: str
    deployment_id: str
    docker_host: str | None
    runtime_image: str | None
    egress_network_enabled: bool
    egress_proxy_url: str | None
    max_request_bytes: int
    max_file_bytes: int
    max_stdout_bytes: int
    max_active: int
    workspace_uid: str
    reconcile_on_start: bool
    reconcile_grace_seconds: int
    ttl_sweep_interval_seconds: int
    network_label: str
    seccomp_dir: str

    @classmethod
    def from_env(cls) -> "SandboxdConfig":
        state_path = (os.environ.get("SANDBOXD_STATE_PATH") or "./var/sandboxd/state.db").strip()
        deployment_id = (os.environ.get("SANDBOXD_DEPLOYMENT_ID") or "default").strip()
        if not deployment_id or any(ch.isspace() for ch in deployment_id):
            raise SandboxdConfigError("SANDBOXD_DEPLOYMENT_ID must be a non-empty label without whitespace")

        runtime_image = (os.environ.get("SANDBOXD_RUNTIME_IMAGE") or "").strip() or None
        if runtime_image and not runtime_image.startswith("sha256:") and "@sha256:" not in runtime_image:
            raise SandboxdConfigError(
                "SANDBOXD_RUNTIME_IMAGE must be an immutable sha256 digest reference"
            )

        egress_network_enabled = os.environ.get("SANDBOXD_EGRESS_ENABLED", "true").strip().casefold() != "false"
        egress_proxy_url = (os.environ.get("SANDBOXD_EGRESS_PROXY_URL") or "").strip() or None
        if egress_network_enabled and not egress_proxy_url:
            # Egress stays opt-in per sandbox (policy digest must be provided),
            # but the daemon needs a proxy endpoint to attach runners to.
            raise SandboxdConfigError("SANDBOXD_EGRESS_PROXY_URL is required when egress is enabled")

        admin_token_path = (os.environ.get("SANDBOXD_ADMIN_TOKEN_FILE") or "").strip() or None
        return cls(
            listen_host=(os.environ.get("SANDBOXD_LISTEN_HOST") or "0.0.0.0").strip(),
            port=_env_int("SANDBOXD_PORT", 8090, minimum=1, maximum=65535),
            token=_read_token(os.environ.get("SANDBOXD_TOKEN_FILE")),
            admin_token=_read_token(admin_token_path) if admin_token_path else None,
            state_path=state_path,
            deployment_id=deployment_id,
            docker_host=(os.environ.get("SANDBOXD_DOCKER_HOST") or "").strip() or None,
            runtime_image=runtime_image,
            egress_network_enabled=egress_network_enabled,
            egress_proxy_url=egress_proxy_url,
            max_request_bytes=_env_int("SANDBOXD_MAX_REQUEST_BYTES", 16 * 1024 * 1024, minimum=64 * 1024),
            max_file_bytes=_env_int("SANDBOXD_MAX_FILE_BYTES", 256 * 1024 * 1024, minimum=1024),
            max_stdout_bytes=_env_int("SANDBOXD_MAX_STDOUT_BYTES", 256 * 1024, minimum=16 * 1024),
            max_active=_env_int("SANDBOXD_MAX_ACTIVE", 20, minimum=1, maximum=1024),
            workspace_uid=(os.environ.get("SANDBOXD_WORKSPACE_UID") or "65532:65532").strip(),
            reconcile_on_start=os.environ.get("SANDBOXD_RECONCILE_ON_START", "true").strip().casefold() != "false",
            reconcile_grace_seconds=_env_int("SANDBOXD_RECONCILE_GRACE_SECONDS", 300, minimum=0),
            ttl_sweep_interval_seconds=_env_int("SANDBOXD_TTL_SWEEP_INTERVAL_SECONDS", 60, minimum=5),
            network_label="com.learngraph.sandbox",
            seccomp_dir=(os.environ.get("SANDBOXD_SECCOMP_DIR") or str(
                Path(__file__).resolve().parents[2] / "backend" / "sandbox"
            )).strip(),
        )
