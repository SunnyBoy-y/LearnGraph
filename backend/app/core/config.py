from __future__ import annotations

import json
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEPLOYMENT_PROFILES = frozenset({"personal_desktop", "self_hosted_team", "cloud_saas"})


class HostAccessMode(str, Enum):
    """Host-service access strategy for the whole-app container deployment.

    ``127.0.0.1`` inside a container is the container itself, so provider/MCP
    loopback URLs must be rewritten to reach real-machine services. This enum
    selects the strategy:

    * ``auto`` (default): containerized profiles (``self_hosted_team`` /
      ``cloud_saas``) rewrite through the Host Service Bridge
      (``host.docker.internal:34115``); source installs keep direct loopback.
    * ``bridge``: force bridge rewriting (same rules as ``auto``, explicit).
    * ``direct``: trusted-desktop direct host access — loopback URLs are
      rewritten to ``host.docker.internal:<same-port>`` (Docker Desktop
      forwards that alias to the real machine's loopback). No service
      registry, token or audit; single-user trusted machines only. Sandbox
      containers never inherit this.
    * ``off``: disable all host-service rewriting (loopback URLs stay literal).
    """

    auto = "auto"
    bridge = "bridge"
    direct = "direct"
    off = "off"


def running_in_container() -> bool:
    """True when this process runs inside a container (compose deployment).

    Source installs (``npm run dev``) run directly on the real machine and
    have no ``/.dockerenv``, so they keep the loopback default. Inside the
    app container the loopback is the container itself, so the sandboxd URL
    must go through the compose ``extra_hosts`` gateway alias instead.
    """
    try:
        return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    except OSError:  # pragma: no cover - defensive
        return False


def default_sandboxd_url() -> str:
    """Resolve the sandboxd control-plane URL when no explicit one is set.

    Mirrors ``Settings.effective_host_bridge_url`` (the Host Service Bridge
    auto-derive): an explicit ``LEARNGRAPH_SANDBOXD_URL`` wins; otherwise a
    containerized backend reaches a sandboxd that runs on the real machine
    (Windows/Docker Desktop dev, host-launched daemon) through the
    ``host.docker.internal:host-gateway`` alias already wired by
    ``docker-compose.yml`` — the container→host interconnect that keeps
    「沙箱一键初始化」working under a containerized LearnGraph. A source
    install talks to its local daemon on loopback. The compose stack pins
    ``http://sandboxd:8090`` for its in-stack sandboxd service (the hardening
    override keeps the same URL).
    """
    if running_in_container():
        return "http://host.docker.internal:8090"
    return "http://127.0.0.1:8090"

# Release-default prebuilt sandbox runner image. Bump this constant with every
# release: deployments that do NOT set LEARNGRAPH_SANDBOX_PREBUILT_IMAGE pick up
# the new runner automatically when they upgrade the code, so open-source users
# never need to edit their .env to follow a newer runner build. Setting the env
# var still pins an explicit reference (admin lock).
DEFAULT_SANDBOX_PREBUILT_IMAGE = (
    "crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph:v0.4"
)


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

    # Host-service access strategy for whole-app Docker (see HostAccessMode).
    # auto = compose default (bridge); direct = trusted desktop direct host
    # access without the Host Service Bridge; off = no rewriting at all.
    host_access_mode: HostAccessMode = HostAccessMode.auto

    # Host Service Bridge endpoint (host-side daemon, see
    # doc/host-service-bridge.md). When set on a containerized deployment
    # (self_hosted_team/cloud_saas), the Host Service Resolver rewrites
    # loopback provider/MCP URLs to {host_bridge_url}/services/<id>/...
    # so real-machine services (Ollama, LM Studio, local MCP, local APIs)
    # stay reachable from inside Docker. Leave unset on source installs:
    # loopback URLs then resolve directly.
    host_bridge_url: str | None = None
    # When true (default), containerized deployments auto-derive the bridge
    # endpoint as http://host.docker.internal:34115 — compose already wires
    # extra_hosts host.docker.internal:host-gateway, so no per-user config is
    # needed for the standard self-hosted shape. Set LEARNGRAPH_HOST_BRIDGE_URL
    # explicitly to override; set this to false to disable bridging entirely
    # (e.g. a cloud SaaS deployment with no local bridge).
    host_bridge_auto: bool = True
    # Path to the bridge bearer token file (written by scripts/host-bridge.mjs
    # on the real machine as data/host-bridge/token). Compose mounts it into
    # the app/preview containers so the backend can authenticate outbound
    # bridge calls (X-LearnGraph-Host-Bridge-Token). Empty means the backend
    # calls the bridge without a token (denied unless the bridge is configured
    # to skip auth).
    host_bridge_token_file: Path | None = None

    def _raw_host_access_mode(self) -> HostAccessMode:
        mode = self.host_access_mode
        if not isinstance(mode, HostAccessMode):
            mode = HostAccessMode(mode)
        return mode

    @property
    def effective_host_bridge_url(self) -> str | None:
        """Resolve the bridge endpoint for the active deployment shape.

        Priority: explicit ``host_bridge_url`` > auto-derived
        ``http://host.docker.internal:34115`` on containerized profiles (when
        ``host_bridge_auto``) > None (source installs keep direct loopback).
        ``direct``/``off`` modes short-circuit to None — no bridge in play.
        """
        if self._raw_host_access_mode() in (HostAccessMode.direct, HostAccessMode.off):
            return None
        if self.host_bridge_url:
            return self.host_bridge_url
        if self.host_bridge_auto and self.deployment_profile != "personal_desktop":
            return "http://host.docker.internal:34115"
        return None

    @property
    def effective_host_access_mode(self) -> str:
        """Resolve the effective host-access strategy.

        Returns one of ``"direct"`` | ``"bridge"`` | ``"off"``. ``direct``
        only applies inside a container — on a source install the local
        loopback is already the real machine, so rewriting would break it.
        """
        raw = self._raw_host_access_mode()
        if raw is HostAccessMode.direct:
            return "direct" if running_in_container() else "off"
        if raw is HostAccessMode.off:
            return "off"
        # auto | bridge
        return "bridge" if self.effective_host_bridge_url else "off"

    env: str = "development"
    database_url: str = "sqlite:///./data/learngraph.db"
    # SQLite write-lock busy wait (milliseconds). Feeds both the pysqlite
    # ``timeout`` and the per-connection PRAGMA busy_timeout. 2s is a fast-fail
    # budget: normal multi-stream contention resolves in well under a second
    # (WAL single-writer windows are bounded by the 1s stream flush cadence),
    # so a 10s wait only ever amplified pathological long-window stalls into
    # multi-second token freezes. When a lock is genuinely held longer (a
    # background sweep that kept a dirty session across a model call), the
    # retry helpers fail fast and retry with backoff instead of blocking 10s.
    sqlite_busy_timeout_ms: int = 2_000
    # SQLite connection-pool tuning. Each active agent stream holds one
    # connection for the whole generation; the SQLAlchemy default (5 + 10
    # overflow) could be exhausted by 5 concurrent streams plus scheduler
    # sweeps, blocking pool checkout for up to 30s. Raise the ceiling and
    # shorten the checkout wait so streams never stall on pool checkout.
    sqlite_pool_size: int = 10
    sqlite_pool_max_overflow: int = 20
    sqlite_pool_timeout_seconds: int = 10
    # Period (seconds) of the background SQLite WAL checkpoint maintenance
    # loop. A multi-MB WAL makes the next autocheckpoint write many MB back
    # into the main file while holding the single SQLite write lock, which can
    # starve concurrent chat streams with ``database is locked``. Keeping the
    # WAL small (TRUNCATE checkpoint every interval, skipped when busy) keeps
    # autocheckpoint cheap. 60s halves the checkpoint frequency vs 30s so the
    # maintenance writer collides less often with active streams. 0 disables
    # the loop.
    wal_checkpoint_interval_seconds: int = 60
    # P3-S1: while any agent SSE stream is generating, non-urgent scheduler
    # sweeps defer to the next tick so the single SQLite writer serves
    # interactive traffic first. This caps how many consecutive ticks a sweep
    # may skip before it runs anyway (a long multi-session study session must
    # not starve mastery/retention/cleanup work forever).
    sqlite_sweep_defer_max_skips: int = 3
    # P4-L1: stream-event retention. ``message_stream_events`` is an
    # append-only transport/replay projection; terminal messages older than
    # ``stream_event_retention_days`` get their events purged by a periodic
    # maintenance sweep (the message/part rows remain the fact source). 0
    # disables purging. The sweep also defers while agent streams are active.
    stream_event_retention_enabled: bool = True
    stream_event_retention_days: int = 90
    stream_event_retention_interval_seconds: int = 3600
    storage_root: Path = Path("./data/storage")
    memory_root: Path = Path("./data/memory")
    # Production SPA directory served by the API process. Empty keeps the
    # API-only layout used by `npm run dev` (Vite owns the frontend origin).
    frontend_dist: str | None = None
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
    # The sandboxd control plane is the runtime of record since the execution
    # pool migration; the legacy in-process Docker backend ("docker") is kept
    # only as a migration shim for deployments that have not switched yet.
    sandbox_backend: str = "sandboxd"
    # sandboxd control-plane connection (required when sandbox_backend=sandboxd).
    # The daemon owns Docker Engine; LearnGraph only consumes its authenticated
    # Sandbox API. The deployment id MUST match the daemon's SANDBOXD_DEPLOYMENT_ID.
    sandboxd_url: str | None = None
    sandboxd_token_file: str | None = None
    # Optional separate credential for the daemon's bootstrap/admin control
    # plane (pull + digest + smoke). When empty, bootstrap reports an explicit
    # "admin control plane not configured" state instead of attempting a job.
    sandboxd_admin_token_file: str | None = None
    sandboxd_deployment_id: str = "default"
    sandboxd_connect_timeout_seconds: float = 3.0
    sandboxd_request_timeout_seconds: float = 190.0
    sandboxd_protocol_min: str = "1.1"
    sandboxd_protocol_max: str = "1.1"
    # Optional immutable runtime image override for CI/offline deployments.
    # When empty, runtime resolution uses the digest persisted by Bootstrap.
    sandbox_image: str | None = None
    # Optional Docker Hub/registry image fetched by Bootstrap instead of locally
    # building the runner. Tags are resolved to an immutable RepoDigest before
    # they can become the runtime image reference.
    # When unset, effective_sandbox_prebuilt_image falls back to the code
    # release default (DEFAULT_SANDBOX_PREBUILT_IMAGE), so upgrading the code
    # carries the new runner version without touching operator .env files.
    sandbox_prebuilt_image: str | None = None

    @property
    def effective_sandbox_prebuilt_image(self) -> str | None:
        """Resolve the prebuilt runner image reference for this deployment.

        Explicit LEARNGRAPH_SANDBOX_PREBUILT_IMAGE wins; otherwise the code
        release default follows code upgrades (open-source users never edit
        their .env to get a newer runner).
        """
        return (self.sandbox_prebuilt_image or "").strip() or DEFAULT_SANDBOX_PREBUILT_IMAGE
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
    # ── Parallel Agent tool execution ─────────────────────────────────
    # Default OFF. When enabled, a provider round whose tools are ALL in the
    # audited parallel-safe allowlist (get_current_time / search_web /
    # search_images) runs them concurrently on a bounded process-wide pool.
    # Each parallel tool executes on its own SQLAlchemy Session; every other
    # tool keeps the legacy serial single-worker path (exact prior behavior).
    # agent_parallel_tools_max_workers caps the whole process; the per-batch
    # cap is min(len(calls), max_workers).
    agent_parallel_tools_enabled: bool = False
    agent_parallel_tools_max_workers: int = 4
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
    # Destructive-delete authorization for the session work/ tree. The sandbox
    # is Docker-isolated and work/ deletes never touch host files, so the
    # product default is approval-free ("off"): the Agent can delete files
    # under work/ directly, every delete stays audited, and any path outside
    # the work/ tree remains hard-blocked. Set to "on" to restore the
    # single-use grant dialog flow (SandboxDestructiveGrant).
    sandbox_delete_approval_mode: str = "off"
    # ── Execution pool / unified scheduler ─────────────────────────────
    # Platform hard caps (never exceeded, even by admin overrides).
    sandbox_hard_max_instances_per_user: int = 8
    sandbox_hard_max_parallel_execs_per_instance: int = 8
    # Deployment defaults (admin/user overrides may lower but not raise past hard caps).
    sandbox_default_max_instances_per_user: int = 2
    sandbox_default_max_parallel_execs_per_instance: int = 4
    sandbox_default_queue_depth_per_user: int = 50
    sandbox_queue_deadline_seconds: int = 1_800
    sandbox_job_wall_time_seconds: int = 600
    sandbox_execution_lease_seconds: int = 300
    sandbox_reservation_ttl_seconds: int = 120
    sandbox_scheduler_interval_seconds: int = 5
    sandbox_scheduler_workers: int = 4
    # Execution-pool instance reuse: when enabled, a user's chat workspaces
    # share the user's warm sandboxd instance (one physical container per
    # instance, chat workspaces isolated by directory prefix). The legacy
    # per-chat container path remains for the docker backend.
    sandbox_instance_pooling_enabled: bool = True
    sandbox_probe_high_watermark: float = 0.80
    sandbox_probe_low_watermark: float = 0.60
    sandbox_probe_low_recovery_rounds: int = 6
    # Workload-class resource hints (server-side authoritative; Agents never
    # supply raw resource numbers). Keys: read_only / python / build / browser.
    sandbox_workload_classes: dict[str, dict[str, Any]] = {
        "read_only": {"cpu": 0.1, "memory_bytes": 64 * 1024 * 1024, "pids": 8},
        "python": {"cpu": 0.5, "memory_bytes": 512 * 1024 * 1024, "pids": 64},
        "build": {"cpu": 1.5, "memory_bytes": 1536 * 1024 * 1024, "pids": 256},
        "browser": {"cpu": 1.0, "memory_bytes": 2048 * 1024 * 1024, "pids": 256},
    }
    # ── End execution pool ─────────────────────────────────────────────
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
    # ── Sandbox toolkit (bash / git / patch / notebook / subagent / network) ──
    sandbox_bash_enabled: bool = True
    sandbox_bash_max_chars: int = 16_384
    sandbox_git_enabled: bool = True
    sandbox_patch_max_bytes: int = 2_000_000
    sandbox_notebook_enabled: bool = True
    sandbox_subagent_enabled: bool = True
    sandbox_subagent_max_rounds: int = 6
    sandbox_subagent_max_seconds: int = 300
    # Host-side network tools for the sandbox (search/fetch). The sandbox
    # container itself stays offline; requests go through the reviewed
    # authorization pipeline on the host.
    sandbox_network_tools_enabled: bool = True
    sandbox_cleanup_scheduler_enabled: bool = True
    sandbox_cleanup_interval_seconds: int = 60
    sandbox_execution_scheduler_enabled: bool = True
    sandbox_execution_scheduler_interval_seconds: int = 5
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
    # --- Frontend-sandbox networking (browser MagicCard / HTML preview) -------
    # Approval-free by product decision (only backend sandboxes require egress
    # approval). When enabled, JS-initiated network calls inside the browser
    # sandbox are relayed by the host bridge to POST /api/v1/sandbox-net/proxy;
    # the gateway still hard-guards every relay (public-only resolved addresses,
    # no cookies/credentials, size/timeout caps, audit trail).
    sandbox_net_enabled: bool = True
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

    @property
    def resolved_frontend_dist(self) -> Path | None:
        """Return the SPA directory when it contains a production index.html."""

        raw = (self.frontend_dist or "").strip()
        if not raw:
            return None
        configured = Path(raw).expanduser()
        if not configured.is_absolute():
            backend_root = Path(__file__).resolve().parents[2]
            configured = backend_root / configured
        try:
            configured = configured.resolve()
        except OSError:
            return None
        if configured.is_dir() and (configured / "index.html").is_file():
            return configured
        return None

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
        if normalized not in {"docker", "sandboxd"}:
            raise ValueError("sandbox_backend must be 'docker' or 'sandboxd'")
        return normalized

    @model_validator(mode="after")
    def validate_sandboxd_connection(self) -> "Settings":
        # sandbox_backend=sandboxd without a configured daemon connection is
        # valid: the app starts and every sandbox probe reports an explicit
        # "backend unavailable" (fail closed). This keeps the default flipped to
        # sandboxd without breaking bare source checkouts that have not wired a
        # daemon yet.
        if self.sandbox_backend == "sandboxd":
            if not (self.sandboxd_url or "").strip():
                self.sandboxd_url = default_sandboxd_url()
            if not (self.sandboxd_token_file or "").strip():
                self.sandboxd_token_file = "./data/.sandboxd/sandboxd-token"
        return self

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

    @field_validator("sandbox_delete_approval_mode")
    @classmethod
    def validate_sandbox_delete_approval_mode(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"off", "on"}:
            raise ValueError("sandbox_delete_approval_mode must be 'off' or 'on'")
        return normalized

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
            if self.sandbox_enabled and self.sandbox_backend == "docker":
                warnings.append(
                    f"{self.deployment_profile}: docker backend 已弃用（drain-only），"
                    "生产部署请使用 sandboxd 控制面（docker-compose.sandbox.yml / docker/wrapper）"
                )
        return warnings


# `from __future__ import annotations` defers field annotations to strings;
# pydantic 2.13+ needs an explicit rebuild so forward references (e.g. `Any`
# inside generic dict annotations) resolve before validation runs.
Settings.model_rebuild()


@lru_cache
def get_settings() -> Settings:
    return Settings()
