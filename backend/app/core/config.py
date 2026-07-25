from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LEARNGRAPH_",
        case_sensitive=False,
        extra="ignore",
    )

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
    mastery_message_threshold: int = 8
    mastery_idle_seconds: int = 300
    mastery_embedded_scheduler_enabled: bool = True
    mastery_scheduler_interval_seconds: int = 60
    mastery_job_lease_seconds: int = 300
    mastery_job_max_attempts: int = 3
    memory_retention_scheduler_enabled: bool = True
    memory_retention_interval_seconds: int = 60
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
    sandbox_pids_max: int = 256
    sandbox_disk_bytes: int = 256 * 1024 * 1024
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
