from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEPLOYMENT_PROFILES = frozenset({"personal_desktop", "self_hosted_team", "cloud_saas"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LEARNGRAPH_",
        case_sensitive=False,
        extra="ignore",
    )

    # Deployment profile sets default listen host, CORS policy, and sandbox
    # configurability.  personal_desktop is the single-user local default;
    # self_hosted_team relaxes the listen address; cloud_saas expects a
    # managed TLS termination / reverse proxy in front.
    deployment_profile: str = "personal_desktop"

    env: str = "development"
    database_url: str = "sqlite:///./data/learngraph.db"
    storage_root: Path = Path("./data/storage")
    memory_root: Path = Path("./data/memory")
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    demo_username: str = "demo"
    demo_password: str = "learn-graph-local"
    enable_demo_seed: bool = False
    auth_session_hours: int = 12
    auth_max_failed_logins: int = 5
    auth_lockout_minutes: int = 15
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str | None = None
    enable_local_demo_provider: bool = False
    # Desktop/host installs use the operating-system credential vault by
    # default.  It creates the first versioned master key when a user first
    # saves a secret, so page-level Provider configuration does not require a
    # master key in .env.  ``environment`` remains an explicit compatibility
    # mode for managed deployments.
    secret_provider: str = "keyring"
    master_key: str | None = None
    master_key_version: int = 1
    keyring_service_name: str = "LearnGraph"
    keyring_account_prefix: str = "master-key"
    max_upload_bytes: int = 200 * 1024 * 1024
    max_backup_bytes: int = 1024 * 1024 * 1024
    max_document_parse_bytes: int = 50 * 1024 * 1024
    research_poll_seconds: float = 0.25
    research_max_polls: int = 20
    durable_queue_enabled: bool = True
    durable_queue_poll_seconds: float = 0.25
    durable_queue_lease_seconds: int = 300
    durable_queue_max_attempts: int = 3
    mastery_message_threshold: int = 8
    mastery_idle_seconds: int = 300
    mastery_embedded_scheduler_enabled: bool = True
    mastery_scheduler_interval_seconds: int = 60
    mastery_job_lease_seconds: int = 300
    mastery_job_max_attempts: int = 3
    memory_retention_scheduler_enabled: bool = True
    memory_retention_interval_seconds: int = 60
    # Background memory extraction ("dreaming"): reviews quiet sessions and
    # proposes MemoryDrafts through the per-workspace enhancement settings.
    memory_extraction_scheduler_enabled: bool = True
    memory_extraction_interval_seconds: int = 120
    memory_extraction_idle_seconds: int = 180
    memory_extraction_sessions_per_sweep: int = 3
    # Docker is the cross-platform hardened baseline: Docker Engine on Linux,
    # Docker Desktop/WSL2 on Windows, and Docker Desktop on macOS.  Enabling
    # the feature by default does not make an unpinned/missing runner image
    # executable; the backend still reports an explicit unavailable state.
    sandbox_enabled: bool = True
    sandbox_backend: str = "docker"
    sandbox_image: str | None = None
    sandbox_task_ttl_seconds: int = 3_600
    sandbox_container_idle_ttl_seconds: int = 180
    sandbox_container_absolute_ttl_seconds: int = 1_800
    sandbox_workspace_idle_ttl_seconds: int = 1_800
    sandbox_workspace_absolute_ttl_seconds: int = 86_400
    sandbox_workspace_root: str = "./data/sandbox-workspaces"
    sandbox_wall_time_seconds: int = 180
    sandbox_cpu_count: float = 2.0
    sandbox_memory_bytes: int = 2 * 1024 * 1024 * 1024
    sandbox_memory_swap_bytes: int = 2 * 1024 * 1024 * 1024
    # pids_limit counts threads; the unified image runs Chromium, which needs
    # headroom beyond a plain interpreter workload.
    sandbox_pids_max: int = 512
    # Optional build-time mirrors for the Bootstrap image build (useful on
    # constrained networks); empty means the public defaults baked into the
    # Dockerfile ARGs.
    sandbox_build_pip_index_url: str | None = None
    sandbox_build_npm_registry: str | None = None
    sandbox_disk_bytes: int = 256 * 1024 * 1024
    sandbox_file_count: int = 20_000
    sandbox_directory_count: int = 5_000
    sandbox_snapshot_reserve_bytes: int = 256 * 1024 * 1024
    sandbox_output_bytes: int = 5 * 1024 * 1024
    sandbox_active_per_user: int = 2
    sandbox_queued_tasks_per_user: int = 5
    sandbox_retained_workspaces_per_user: int = 10
    sandbox_host_max_active: int = 20
    sandbox_host_max_allocated_memory_ratio: float = 0.70
    sandbox_host_max_allocated_cpu_ratio: float = 0.80
    sandbox_host_minimum_free_disk_bytes: int = 20 * 1024 * 1024 * 1024
    sandbox_agent_enabled: bool = True
    sandbox_agent_file_bytes: int = 1 * 1024 * 1024
    sandbox_agent_command_args_max: int = 32
    sandbox_cleanup_scheduler_enabled: bool = True
    sandbox_cleanup_interval_seconds: int = 60
    # --- Reviewed outbound egress (P2-C) -------------------------------------
    # Default sandbox posture stays fully offline. Enabling egress is a
    # deployment decision that must point at a reviewed policy directory; every
    # CONNECT is re-authorized by the egress proxy at connection time.
    sandbox_egress_enabled: bool = False
    sandbox_egress_policy_dir: str = "./data/egress-policies"
    sandbox_egress_network: str = "learngraph-egress"
    sandbox_egress_proxy_host: str = "127.0.0.1"
    sandbox_egress_proxy_port: int = 8888
    # Sandbox-visible proxy endpoint on the internal egress network.
    sandbox_egress_proxy_url: str = "http://egress-proxy:8888"
    # --- Isolated component renderer (P2-A) --------------------------------
    # Third-party component data is rendered into a server-owned, inert HTML
    # template with a strict CSP and delivered through the existing opaque-origin
    # iframe. Rendering only becomes executable when the offline Docker sandbox
    # image is pinned and the backend probes available; otherwise components
    # keep the safe ``sandbox_artifact`` unavailable baseline.
    component_renderer_enabled: bool = True
    component_render_preview_chars: int = 100_000
    component_render_screenshot_ttl_seconds: int = 3_600
    # --- Isolated MCP stdio runner (P2-B) ----------------------------------
    # The FastAPI process never launches a third-party MCP command. stdio
    # execution only becomes available when this flag is on AND an immutable
    # pinned sandbox image provides the fixed ``mcp_stdio`` task; otherwise the
    # default ``UnavailableStdioMCPAdapter`` stays in effect.
    mcp_stdio_runner_enabled: bool = False
    mcp_stdio_command_args_max: int = 16
    mcp_stdio_result_bytes: int = 256 * 1024
    mcp_stdio_request_bytes: int = 64 * 1024
    mcp_stdio_timeout_seconds: int = 60
    mcp_stdio_session_ttl_seconds: int = 900
    # Best-effort periodic sweep that reaps orphaned MCP stdio runner containers
    # (a process crash between provision and terminate leaves a durable
    # ``MCPRunnerSession`` record; the sweep deletes the expired container and
    # marks it terminated). Offline deny-by-default posture is unchanged.
    mcp_stdio_cleanup_scheduler_enabled: bool = True
    mcp_stdio_cleanup_interval_seconds: int = 120
    # --- Skills & extension marketplace -------------------------------------
    # Same-host local skill discovery. Empty string keeps the legacy behavior
    # (also honours the raw LEARNGRAPH_SKILL_LOCAL_PROBE env var); explicit
    # values: "on"/"local" force-allow, "off"/"remote" force-deny.
    skill_local_probe_mode: str = ""
    # Whether /skills/market may refresh its cache from GitHub raw content.
    skill_market_refresh_enabled: bool = True
    # Optional GitHub token used for market fetches (raises rate limits).
    skill_market_github_token: str | None = None
    # External skill catalogs (search-only aggregation; installs still resolve
    # to GitHub or manual import so content stays pinned and reviewable).
    clawhub_enabled: bool = True
    clawhub_api_url: str = "https://clawhub.ai/api/v1"
    # skills.sh has a documented API but authenticated via Vercel OIDC; keep
    # disabled by default until a deployment provides credentials.
    skills_sh_enabled: bool = False
    skills_sh_api_url: str = "https://skills.sh/api/v1"
    # Official MCP Registry (frozen v0.1 API; anonymous reads).
    mcp_registry_enabled: bool = True
    mcp_registry_url: str = "https://registry.modelcontextprotocol.io"
    external_catalog_timeout_seconds: float = 12.0
    # Progressive disclosure for Agent Skill prompt injection: bodies within
    # these budgets are injected inline; the rest become one-line catalog
    # entries the model expands on demand via the lg_skill_read tool.
    skill_prompt_inline_char_limit: int = 4_000
    skill_prompt_total_char_budget: int = 16_000
    skill_prompt_catalog_max_entries: int = 24
    # Agent self-service extension management (lg_skill_install / lg_mcp_register
    # etc.). Installs stay commit-pinned and audited; disable to make skills and
    # MCP servers user-click-only.
    agent_extension_self_service_enabled: bool = True

    @property
    def resolved_sandbox_workspace_root(self) -> Path:
        """Resolve sandbox data relative to backend/, not the launch directory."""

        configured = Path(self.sandbox_workspace_root).expanduser()
        if not configured.is_absolute():
            backend_root = Path(__file__).resolve().parents[2]
            configured = backend_root / configured
        return configured.resolve()

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                decoded = json.loads(stripped)
                if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
                    raise ValueError("CORS origins JSON must be an array of strings")
                return decoded
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def has_master_key(self) -> bool:
        return bool(self.master_key and self.master_key.strip())

    @field_validator("secret_provider")
    @classmethod
    def validate_secret_provider(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"environment", "keyring"}:
            raise ValueError("Secret provider must be 'environment' or 'keyring'")
        return normalized

    @property
    def demo_seed_enabled(self) -> bool:
        if self.env.casefold() not in {"development", "dev", "test", "local"}:
            return False
        return self.enable_demo_seed

    @field_validator("sandbox_backend")
    @classmethod
    def validate_sandbox_backend(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized != "docker":
            raise ValueError("The current backend supports only the explicit docker sandbox")
        return normalized

    @field_validator("sandbox_agent_file_bytes")
    @classmethod
    def validate_sandbox_agent_file_bytes(cls, value: int) -> int:
        if not 1 <= value <= 16 * 1024 * 1024:
            raise ValueError("Sandbox Agent file bytes must be between 1 and 16777216")
        return value

    @field_validator("sandbox_agent_command_args_max")
    @classmethod
    def validate_sandbox_agent_command_args_max(cls, value: int) -> int:
        if not 1 <= value <= 64:
            raise ValueError("Sandbox Agent command argument limit must be between 1 and 64")
        return value

    @field_validator("deployment_profile", mode="before")
    @classmethod
    def validate_deployment_profile(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in DEPLOYMENT_PROFILES:
            raise ValueError(
                f"Deployment profile must be one of {sorted(DEPLOYMENT_PROFILES)}"
            )
        return normalized

    @property
    def profile_listen_host(self) -> str:
        """Return the safe default listen host for the active deployment profile."""
        if self.deployment_profile == "personal_desktop":
            return "127.0.0.1"
        return "0.0.0.0"

    def profile_validate(self) -> list[str]:
        """Check for conflicting deployment profile settings.  Returns a list of
        human-readable warnings; an empty list means the profile is consistent."""
        warnings: list[str] = []
        if self.deployment_profile == "personal_desktop":
            if self.cors_origins and any("0.0.0.0" in o for o in self.cors_origins):
                warnings.append("personal_desktop: CORS origins should not contain 0.0.0.0")
        if self.deployment_profile in ("self_hosted_team", "cloud_saas"):
            if self.sandbox_enabled and self.sandbox_backend != "docker":
                warnings.append(
                    f"{self.deployment_profile}: sandbox_backend must be 'docker'"
                )
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
