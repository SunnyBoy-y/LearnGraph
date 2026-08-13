from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
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
    # SQLite write-lock busy wait (milliseconds). Feeds both the pysqlite
    # ``timeout`` and the per-connection PRAGMA busy_timeout. 10s bounds
    # interactive-request stalls when a background sweep owns the single write
    # lock, while staying well above the old 5s budget that lost races to
    # multi-second chat/memory commits; the retry helpers and the B1-7 sweep
    # mutex absorb the residual contention.
    sqlite_busy_timeout_ms: int = 10_000
    # Period (seconds) of the background SQLite WAL checkpoint maintenance
    # loop. A multi-MB WAL makes the next autocheckpoint write many MB back
    # into the main file while holding the single SQLite write lock, which can
    # starve concurrent chat streams with ``database is locked``. Keeping the
    # WAL small (TRUNCATE checkpoint every interval, skipped when busy) keeps
    # autocheckpoint cheap. 0 disables the loop.
    wal_checkpoint_interval_seconds: int = 30
    storage_root: Path = Path("./data/storage")
    memory_root: Path = Path("./data/memory")
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    demo_username: str = "demo"
    demo_password: str = "learn-graph-local"
    # Demo seed data is on by default so the demo workspace opens with sample
    # goals/graphs/sessions; demo_seed_enabled() gates it to non-production
    # envs. Set LEARNGRAPH_ENABLE_DEMO_SEED=false to disable explicitly.
    enable_demo_seed: bool = True
    # Demo login is on by default for the local product experience; the fixed
    # demo credential is never reachable in production-like envs because
    # demo_login_enabled() additionally gates on LEARNGRAPH_ENV. Set
    # LEARNGRAPH_ENABLE_DEMO_LOGIN=false to disable explicitly.
    enable_demo_login: bool = True
    auth_session_hours: int = 12
    auth_max_failed_logins: int = 5
    auth_lockout_minutes: int = 15
    # IP-level sliding-window rate limit for anonymous auth endpoints
    # (login / register / demo-login). Protects accounts from brute-force
    # lockout DoS and the registration surface from scripted abuse.
    auth_rate_limit_max: int = 30
    auth_rate_limit_window_seconds: int = 60
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
    # Large media is streamed directly into the local persistent file store. The
    # limit is a policy ceiling, not preallocated disk space. 512 MiB keeps a
    # single upload from exhausting local disk on desktop/self-hosted installs
    # while remaining ample for typical learning materials (PDF/DOCX/PPTX/media).
    max_upload_bytes: int = 512 * 1024 * 1024
    # Per-workspace aggregate storage budget enforced on upload. Set to 0 to
    # disable quota enforcement (not recommended on shared deployments).
    workspace_storage_quota_bytes: int = 10 * 1024 * 1024 * 1024
    max_backup_bytes: int = 1024 * 1024 * 1024
    max_document_parse_bytes: int = 50 * 1024 * 1024
    # Trusted host-side acquisition. Remote bytes are verified and persisted
    # before they are linked into a sandbox session; the sandbox stays offline.
    external_download_timeout_seconds: float = 20.0
    external_download_max_redirects: int = 3
    # Reuse DNS answers that already passed public-address classification within
    # one acquisition (multi-file GitHub snapshots, parallel image batches) so the
    # same host is not re-resolved per file; denied/empty answers are never cached.
    external_download_dns_cache_ttl_seconds: float = 300.0
    external_download_dns_cache_max_entries: int = 128
    # Keep-alive reuse of the pinned (host, IP) HTTPS connection: GitHub snapshots
    # issue one request per file, so reusing one TLS connection avoids a fresh
    # handshake per file. 0 disables reuse entirely (every connection closed).
    external_download_max_idle_connections: int = 4
    external_download_idle_connection_timeout_seconds: float = 60.0
    external_image_download_max_bytes: int = 20 * 1024 * 1024
    external_image_download_max_pixels: int = 40_000_000
    external_image_download_max_parallel: int = 4
    external_github_metadata_max_bytes: int = 8 * 1024 * 1024
    external_github_file_max_bytes: int = 10 * 1024 * 1024
    external_github_total_max_bytes: int = 100 * 1024 * 1024
    external_github_max_files: int = 2_000
    research_poll_seconds: float = 0.25
    research_max_polls: int = 20
    durable_queue_enabled: bool = True
    durable_queue_poll_seconds: float = 0.25
    durable_queue_lease_seconds: int = 300
    durable_queue_max_attempts: int = 3
    # Event-driven Agent turns for bidirectional sub-applications. Default off:
    # enabling it can trigger paid model calls without a frontend consent turn.
    subapp_event_agent_enabled: bool = False
    subapp_event_agent_max_attempts: int = 3
    subapp_event_agent_idle_seconds: int = 120
    subapp_event_agent_poll_reconcile: bool = True
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
    # Event-sourced memory architecture rollout. Architecture flags are deployment
    # controls, never ordinary workspace settings editable by agents.
    memory_write_mode: str = "dual"
    memory_read_mode: str = "events"
    memory_shadow_sample_rate: float = 0.05
    memory_context_builder_v2: bool = True
    memory_task_episode_enabled: bool = True
    memory_file_revision_invalidation_enabled: bool = True
    memory_agent_run_enabled: bool = True
    memory_strategy_enabled: bool = False
    memory_event_master_key: str | None = None
    memory_outbox_worker_enabled: bool = True
    memory_outbox_interval_seconds: int = 5
    memory_outbox_lease_seconds: int = 120
    memory_outbox_max_attempts: int = 8
    memory_outbox_strict_leases: bool = False
    # Docker is the cross-platform hardened baseline: Docker Engine on Linux,
    # Docker Desktop/WSL2 on Windows, and Docker Desktop on macOS.  Enabling
    # the feature by default does not make an unpinned/missing runner image
    # executable; the backend still reports an explicit unavailable state.
    sandbox_enabled: bool = True
    sandbox_backend: str = "docker"
    # Optional immutable runtime image override for CI/offline deployments.
    # When empty, runtime resolution uses the digest persisted by Bootstrap.
    sandbox_image: str | None = None
    # Optional Docker Hub/registry image fetched by Bootstrap instead of locally
    # building the runner. Tags are resolved to an immutable RepoDigest before
    # they can become the runtime image reference.
    sandbox_prebuilt_image: str | None = None
    sandbox_task_ttl_seconds: int = 3_600
    sandbox_container_idle_ttl_seconds: int = 600
    sandbox_container_absolute_ttl_seconds: int = 3_600
    sandbox_workspace_idle_ttl_seconds: int = 1_800
    sandbox_workspace_absolute_ttl_seconds: int = 86_400
    sandbox_workspace_root: str = "./data/sandbox-workspaces"
    # Opt-in Linux deployment hardening. None preserves the host process owner
    # and permissions used by source/desktop development.
    sandbox_workspace_uid: int | None = None
    sandbox_wall_time_seconds: int = 180
    # Host-level timeout for a single Agent tool execution (search/fetch/sandbox
    # command/MCP call). A hanging upstream must not stall the whole generation
    # chain; the tool returns a timeout failure and the chain can continue.
    agent_tool_timeout_seconds: int = 120
    # Hosted 文搜图/图搜图 (Qwen Responses web_search_image / image_search)
    # provider timeout. Image search is slower than plain web search (the
    # upstream runs a real web search and may process an input_image for
    # reverse search), so it gets its own budget instead of the generic 60s
    # QwenResponsesToolProvider default. Must stay below
    # agent_tool_timeout_seconds so the outer tool deadline never preempts it.
    hosted_image_search_timeout_seconds: int = Field(default=90, ge=1, le=300)
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
    # This is a logical, actual-usage ceiling for the bind-mounted workspace;
    # Docker does not preallocate this amount when a sandbox starts.
    sandbox_disk_bytes: int = 2 * 1024 * 1024 * 1024
    sandbox_file_count: int = 20_000
    sandbox_directory_count: int = 5_000
    sandbox_snapshot_reserve_bytes: int = 256 * 1024 * 1024
    # Safety snapshots live beside a user's session directories while a command
    # runs. A crashed backend can strand them, so the cleanup sweep removes only
    # snapshots older than this grace period.
    sandbox_snapshot_cleanup_grace_seconds: int = 600
    # Combined stdout/stderr captured for one command. Generated files use the
    # workspace/artifact path and must not be transported through process output.
    sandbox_output_bytes: int = 20 * 1024 * 1024
    sandbox_active_per_user: int = 2
    sandbox_queued_tasks_per_user: int = 5
    sandbox_retained_workspaces_per_user: int = 10
    sandbox_host_max_active: int = 20
    sandbox_host_max_allocated_memory_ratio: float = 0.70
    sandbox_host_max_allocated_cpu_ratio: float = 0.80
    sandbox_host_minimum_free_disk_bytes: int = 20 * 1024 * 1024 * 1024
    sandbox_agent_enabled: bool = True
    # Multi-file teaching application previews are served only through an
    # independent origin (separate process/port or a real reverse-proxied
    # domain). This env override is a deployment default; an administrator can
    # persist the origin from the frontend settings page. Empty means no
    # configured preview origin (bundle preview fails closed).
    subapp_preview_origin: str | None = None
    # Local dev preview port: when no origin is persisted or set via env, the
    # bundle capability URLs are derived as http://127.0.0.1:<port>. scripts/
    # dev.mjs starts the preview ASGI process on this port automatically.
    subapp_preview_port: int | None = None
    # bootstrap to admins; this env flag sets the initial deployment default.
    sandbox_bootstrap_member_allowed: bool = True
    sandbox_agent_file_bytes: int = 256 * 1024 * 1024
    # Docker get_archive wraps one file in tar metadata, so keep a bounded margin
    # above the per-file limit while rejecting whole-workspace multi-GiB archives.
    sandbox_agent_archive_bytes: int = 320 * 1024 * 1024
    sandbox_agent_command_args_max: int = 32
    sandbox_cleanup_scheduler_enabled: bool = True
    sandbox_cleanup_interval_seconds: int = 60
    # --- Reviewed outbound egress (P2-C) -------------------------------------
    # Sandbox egress is on by default for the local product experience, but it
    # still requires a valid per-workspace reviewed policy file; without one the
    # runtime envelope is ``None`` and the container stays fully offline. Every
    # CONNECT is re-authorized by the egress proxy at connection time, so the
    # switch alone never bypasses the private/loopback/metadata classifier.
    sandbox_egress_enabled: bool = True
    sandbox_egress_policy_dir: str = "./data/egress-policies"
    sandbox_egress_network: str = "learngraph-egress"
    sandbox_egress_proxy_host: str = "127.0.0.1"
    sandbox_egress_proxy_port: int = 8888
    # Sandbox-visible proxy endpoint on the internal egress network.
    sandbox_egress_proxy_url: str = "http://egress-proxy:8888"
    # Generic Agent egress approval channel (D2.1). On by default so users can
    # review and approve Agent egress host requests; the channel itself is
    # inert while `sandbox_egress_enabled` stays off (sandbox stays fully
    # offline). Every decision only adds an exact hostname; the egress proxy
    # still re-classifies every CONNECT, so private/loopback/metadata targets
    # remain denied.
    sandbox_agent_egress_approvals_enabled: bool = True
    # Sandbox-isolated web fetch: when enabled together with sandbox_egress_enabled,
    # page retrieval and untrusted-HTML parsing happen inside a short-lived fixed
    # web_fetch container (never the host). Requires a non-empty workspace
    # web_fetch.policy allowlist; otherwise the explicit remote FetchProvider /
    # Qwen companion path is used. This is the global hard gate: each workspace
    # can additionally toggle its own sandbox fetch switch and channel priority
    # in Provider 管理 -> 网页抓取 (``web_fetch.runtime`` setting). Defaults to
    # on so sandbox-isolated fetch is the secure primary path out of the box.
    sandbox_web_fetch_enabled: bool = True
    # Hard bounds for a single web_fetch container job (independent of the
    # generic sandbox resource limits).
    sandbox_web_fetch_timeout_seconds: float = 30.0
    sandbox_web_fetch_max_bytes: int = 2 * 1024 * 1024
    # Warm web_fetch container pool: containers are created once per
    # workspace/allowlist and reused across fetches, skipping the per-fetch
    # create/delete cost (~1-3s each), and up to ``pool_size`` fetches run
    # concurrently. 0 disables the pool and restores the legacy
    # create-per-fetch behavior. Idle containers are pruned after
    # ``pool_idle_seconds`` of inactivity (lazy + periodic sweep).
    sandbox_web_fetch_pool_size: int = 4
    sandbox_web_fetch_pool_idle_seconds: int = 600
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
    # Explicit allowlist for OAuth dynamic client registration. Empty by
    # default: registration endpoints exist but stay closed until a deployment
    # opts in with the exact issuer URLs it trusts.
    mcp_oauth_trusted_issuers: Annotated[set[str], NoDecode] = Field(default_factory=set)
    external_catalog_timeout_seconds: float = 12.0
    # Progressive disclosure budgets for Agent Skill prompt injection. Cold
    # Skills are metadata-only by default; these limits apply to explicitly
    # activated/context-bound bodies or legacy preload mode.
    skill_prompt_inline_char_limit: int = 4_000
    skill_prompt_total_char_budget: int = 16_000
    skill_prompt_catalog_max_entries: int = 24
    # Strict Agent Skills progressive disclosure: startup context contains only
    # compact metadata. Full SKILL.md bodies are loaded after capability
    # activation (or an explicit lg_skill_read). Context-bound official Skills
    # may still opt in through activated_skill_keys.
    skill_prompt_preload_bodies_enabled: bool = False
    # Progressive disclosure for Agent tools (capability catalog): when enabled,
    # the Agent starts with a small core plus lg_capability_search/activate and
    # loads tool schemas / Skill contracts on demand in later rounds. Per-turn
    # activation never mutates durable grants or server/skill enabled state and
    # never bypasses host authorization. Disabled restores eager definitions.
    agent_progressive_tool_disclosure_enabled: bool = True
    # Agent self-service extension management (lg_skill_install / lg_mcp_register
    # etc.). Installs stay commit-pinned and audited; disable to make skills and
    # MCP servers user-click-only.
    agent_extension_self_service_enabled: bool = True
    # System proxy/VPN clients that answer DNS with synthetic private addresses
    # (Clash-style "fake-ip", e.g. 198.18.0.0/15) make the provider-bridge SSRF
    # guard reject otherwise-public hostnames (Firecrawl, Crawl4AI, SearXNG,
    # cloud search). Default is ON (guard disabled): the product targets a
    # single local user on a trusted host network, so private bridge URLs are
    # allowed and URL scheme/userinfo/port checks still apply. Multi-tenant or
    # internet-exposed deployments MUST explicitly set this to false to keep
    # the private-address SSRF guard closed.
    allow_private_bridge_urls: bool = True

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

    @field_validator("mcp_oauth_trusted_issuers", mode="before")
    @classmethod
    def parse_trusted_issuers(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                decoded = json.loads(stripped)
                if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
                    raise ValueError("MCP OAuth trusted issuers JSON must be an array of strings")
                return set(decoded)
            return {item.strip() for item in value.split(",") if item.strip()}
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

    @property
    def demo_login_enabled(self) -> bool:
        if self.env.casefold() not in {"development", "dev", "test", "local"}:
            return False
        return self.enable_demo_login

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
        if not 1 <= value <= 20 * 1024 * 1024 * 1024:
            raise ValueError("Sandbox Agent file bytes must be between 1 and 21474836480")
        return value

    @field_validator("sandbox_agent_archive_bytes")
    @classmethod
    def validate_sandbox_agent_archive_bytes(cls, value: int) -> int:
        if not 1 <= value <= 1024 * 1024 * 1024:
            raise ValueError("Sandbox Agent archive bytes must be between 1 and 1073741824")
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
