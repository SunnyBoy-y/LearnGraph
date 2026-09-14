"""Standalone egress proxy entrypoint for reviewed sandbox outbound network.

Runs ``SandboxEgressProxy`` as an independent process so a deployment can give
the proxy the only internet egress and keep every dynamic sandbox offline
behind it. Policies are loaded from the shared egress-policy directory the API
writes (``LEARNGRAPH_SANDBOX_EGRESS_POLICY_DIR``); the registry refreshes on a
short interval, so approved hosts, allow_all toggles and expirations take
effect without a proxy restart. Every allow/deny decision is emitted as a
JSONL audit record (``LEARNGRAPH_EGRESS_PROXY_AUDIT_LOG``, stdout when unset).

Compose (self-hosted): the image runs this module as its entrypoint, attached
to the internal ``learngraph-egress`` network plus the outbound network.

Local development (same posture as the compose stack):

    docker network create learngraph-egress
    LEARNGRAPH_SANDBOX_EGRESS_PROXY_URL=http://host.docker.internal:8888 \\
        uv run python -m app.services.egress_proxy_main

The proxy itself never resolves or connects to private/loopback/metadata
targets; every CONNECT is authorized against the workspace policy digest the
sandbox carries (see ``app.services.sandbox_network_policy``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.sandbox_egress_proxy import (
    DEFAULT_MAX_HEADER_BYTES,
    DEFAULT_MAX_IDLE_SECONDS,
    DEFAULT_MAX_TUNNEL_BYTES,
    SandboxEgressProxy,
)
from app.services.sandbox_network_policy import (
    EgressPolicy,
    EgressPolicyInvalid,
    WEB_FETCH_POLICY_FILE_SUFFIX,
    system_resolver,
    utc_now,
    validate_egress_policy,
)

logger = logging.getLogger(__name__)

# Policy snapshots expire by design (a reviewed egress grant is time-boxed), so
# an expired file on disk is a *steady state*, not an incident. It is reported
# once per revision (see ``DirectoryPolicyRegistry``); this interval is only the
# low-frequency digest that keeps operators aware without per-refresh spam.
DEFAULT_SUMMARY_SECONDS = 3600.0


def _policy_workspace_id(path: Path) -> str:
    """Best-effort workspace identity from a policy filename (no file read).

    ``{workspace_id}.json`` (generic Agent egress) and
    ``{workspace_id}.web_fetch.json`` (fetch egress) are the only two shapes the
    API writes; anything else is reported by its full name.
    """
    name = path.name
    if name.endswith(WEB_FETCH_POLICY_FILE_SUFFIX):
        return name[: -len(WEB_FETCH_POLICY_FILE_SUFFIX)]
    if name.endswith(".json"):
        return name[: -len(".json")]
    return name


@dataclass(frozen=True, slots=True)
class EgressProxySettings:
    host: str = "0.0.0.0"
    port: int = 8888
    policy_dir: Path = Path("./data/egress-policies")
    audit_log: Path | None = None
    refresh_seconds: float = 5.0
    summary_seconds: float = DEFAULT_SUMMARY_SECONDS
    max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES
    max_idle_seconds: float = DEFAULT_MAX_IDLE_SECONDS
    max_tunnel_bytes: int = DEFAULT_MAX_TUNNEL_BYTES


@dataclass(frozen=True, slots=True)
class PolicyFileOutcome:
    """Last reported validation outcome for one policy file.

    Used only for log de-duplication: the registry still re-validates (or
    serves from the mtime cache) exactly as before, and authorization never
    consults this record.
    """

    mtime_ns: int
    outcome: str  # "ok" | "expired" | "invalid"
    reason: str


class DirectoryPolicyRegistry:
    """Load every reviewed egress policy file under one directory.

    The API persists one file per policy revision: ``{workspace_id}.json`` for
    generic Agent egress and ``{workspace_id}.web_fetch.json`` for derived
    fetch egress. Each file is validated on every refresh (files are small and
    few, and policy churn is an explicit review event), and the resulting
    registry is keyed by the *content digest* — the exact identity a sandbox
    carries in ``LEARNGRAPH_EGRESS_POLICY_DIGEST``. Missing, malformed, or
    expired files are skipped and logged: absent a valid policy, egress is
    denied (fail closed). An mtime cache avoids re-reading unchanged files;
    expiry is still re-checked by ``authorize_connect`` at CONNECT time.

    Logging is *state driven*, not event driven: refresh runs every few seconds
    and an expired snapshot stays expired for as long as the workspace is idle
    (it is only rewritten when a sandbox container is created), so a
    per-refresh message would repeat forever without carrying new information.
    Each file therefore reports at most one entry per revision (mtime) and
    per state transition, and ``summary`` offers a low-frequency digest.
    """

    def __init__(self, policy_dir: str | Path) -> None:
        self.directory = Path(policy_dir)
        self._cache: dict[str, tuple[int, EgressPolicy]] = {}
        self._reported: dict[str, PolicyFileOutcome] = {}
        self._expired_files: dict[str, str] = {}
        self._invalid_files: dict[str, str] = {}
        self._scanned = 0

    def refresh_into(self, registry: dict[str, EgressPolicy]) -> None:
        """Replace ``registry`` contents with the current directory snapshot.

        ``registry`` is mutated in place (``clear`` + ``update``) because the
        running proxy holds a reference to it and the refresh runs on the same
        event loop. Authorization behaviour is unchanged from before the
        state-driven logging: valid files register their digest, invalid and
        expired files are skipped (fail closed).
        """
        current: dict[str, EgressPolicy] = {}
        expired_files: dict[str, str] = {}
        invalid_files: dict[str, str] = {}
        try:
            entries = sorted(self.directory.glob("*.json"))
        except OSError as exc:
            logger.error("Egress policy directory %s is unreadable: %s", self.directory, exc)
            registry.clear()
            return
        now = utc_now()
        seen: set[str] = set()
        for path in entries:
            key = str(path)
            seen.add(key)
            try:
                stat = path.stat()
            except OSError:
                continue
            cached = self._cache.get(key)
            if cached is not None and cached[0] == stat.st_mtime_ns:
                policy = cached[1]
                current[key] = policy
                self._report(key, stat.st_mtime_ns, "ok", "", workspace=policy.workspace_id)
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                policy = validate_egress_policy(raw, now=now)
            except (OSError, ValueError, EgressPolicyInvalid) as exc:
                # ``str(EgressPolicyInvalid)`` is its reason, so the message text
                # of an unreadable/expired policy is unchanged from before.
                reason = (
                    exc.reason
                    if isinstance(exc, EgressPolicyInvalid)
                    else f"policy_unreadable: {exc}"
                )
                outcome = "expired" if reason == "policy_expired" else "invalid"
                workspace = _policy_workspace_id(path)
                self._report(key, stat.st_mtime_ns, outcome, reason, workspace=workspace)
                self._cache.pop(key, None)
                if outcome == "expired":
                    expired_files[key] = workspace
                else:
                    invalid_files[key] = workspace
                continue
            self._cache[key] = (stat.st_mtime_ns, policy)
            current[key] = policy
            self._report(key, stat.st_mtime_ns, "ok", "", workspace=policy.workspace_id)

        stale = [key for key in self._cache if key not in current]
        for key in stale:
            self._cache.pop(key, None)
        for key in [key for key in self._reported if key not in seen]:
            self._reported.pop(key, None)

        registry.clear()
        for policy in current.values():
            registry[policy.digest] = policy

        self._expired_files = expired_files
        self._invalid_files = invalid_files
        self._scanned = len(entries)

    def _report(
        self,
        key: str,
        mtime_ns: int,
        outcome: str,
        reason: str,
        *,
        workspace: str = "",
    ) -> None:
        """Log one policy file only when its revision or state actually changed."""
        state = PolicyFileOutcome(mtime_ns=mtime_ns, outcome=outcome, reason=reason)
        previous = self._reported.get(key)
        if previous == state:
            return
        self._reported[key] = state
        if outcome == "ok":
            if previous is not None and previous.outcome != "ok":
                logger.info(
                    "Egress policy file %s is valid again; egress re-enabled for its workspace (%s)",
                    key,
                    workspace or "unknown",
                )
            return
        if outcome == "expired":
            # Expected steady state (time-boxed grant), reported once per
            # revision: the file is rewritten the next time a sandbox container
            # is created, and until then the workspace's egress stays denied.
            logger.warning(
                "Egress policy file %s is invalid; egress denied for its workspace: %s "
                "(snapshot expired; a new revision is written on the next sandbox container "
                "creation; reported once per revision)",
                key,
                reason,
            )
            return
        logger.error(
            "Egress policy file %s is invalid; egress denied for its workspace: %s",
            key,
            reason,
        )

    def summary(self, registry: dict[str, EgressPolicy] | None = None) -> str | None:
        """One-line digest of the snapshot state, or ``None`` when all is well.

        ``registry_expired`` covers a file that was still valid when this
        process cached it and expired afterwards: it stays registered (so the
        deny reason is ``policy_expired`` at CONNECT time, unchanged behaviour)
        and would otherwise never be visible in the logs.
        """
        now = utc_now()
        registry_expired = sorted(
            {
                policy.workspace_id
                for policy in (registry or {}).values()
                if policy.is_expired(now=now)
            }
        )
        expired = sorted(set(self._expired_files.values()))
        invalid = sorted(set(self._invalid_files.values()))
        if not expired and not invalid and not registry_expired:
            return None
        return (
            f"policy_files={self._scanned}"
            f" expired_on_disk={len(self._expired_files)}"
            f" invalid={len(self._invalid_files)}"
            f" registered_expired={len(registry_expired)}"
            f" expired_workspaces={','.join(expired) or '-'}"
            f" invalid_workspaces={','.join(invalid) or '-'}"
            f" registered_expired_workspaces={','.join(registry_expired) or '-'}"
        )


class JsonlAuditSink:
    """Append-only JSONL sink for proxy allow/deny decisions.

    Every record carries a UTC timestamp in addition to the decision payload,
    so a deployment can correlate the audit trail with policy revisions and
    sandbox sessions without parsing timestamps out of log lines.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._stream: Any = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def open(self) -> None:
        if self._path is not None:
            self._stream = self._path.open("a", encoding="utf-8")

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def __call__(self, event: dict[str, Any]) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if self._stream is not None:
            self._stream.write(line + "\n")
            self._stream.flush()
        else:
            logger.info("egress decision: %s", line)


async def _registry_watchdog(
    proxy: SandboxEgressProxy,
    source: DirectoryPolicyRegistry,
    interval_seconds: float,
    stop: asyncio.Event,
    summary_seconds: float = DEFAULT_SUMMARY_SECONDS,
) -> None:
    loop = asyncio.get_running_loop()
    next_summary_at = loop.time() + max(1.0, summary_seconds)
    while not stop.is_set():
        try:
            source.refresh_into(proxy.policy_registry)  # type: ignore[arg-type]
        except Exception:
            logger.exception("Egress policy refresh failed; previous policies remain in force")
        if summary_seconds > 0 and loop.time() >= next_summary_at:
            try:
                summary = source.summary(proxy.policy_registry)  # type: ignore[arg-type]
                if summary is not None:
                    logger.info("Egress policy summary: %s", summary)
            except Exception:
                logger.exception("Egress policy summary failed")
            next_summary_at = loop.time() + summary_seconds
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue


async def _serve(settings: EgressProxySettings) -> None:
    audit = JsonlAuditSink(settings.audit_log)
    audit.open()
    proxy = SandboxEgressProxy(
        policy_registry={},
        resolver=system_resolver,
        on_decision=audit,
        max_header_bytes=settings.max_header_bytes,
        max_idle_seconds=settings.max_idle_seconds,
        max_tunnel_bytes=settings.max_tunnel_bytes,
    )
    bound = await proxy.start(settings.host, settings.port)
    source = DirectoryPolicyRegistry(settings.policy_dir)
    source.refresh_into(proxy.policy_registry)  # type: ignore[arg-type]

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            # Windows event loops do not support add_signal_handler; Ctrl+C /
            # termination is still handled by asyncio.run / the container.
            break

    watchdog = asyncio.create_task(
        _registry_watchdog(
            proxy,
            source,
            settings.refresh_seconds,
            stop,
            settings.summary_seconds,
        )
    )
    logger.info(
        "Sandbox egress proxy listening on %s:%s (policies: %s, refresh: %ss, summary: %ss)",
        settings.host,
        bound,
        settings.policy_dir,
        settings.refresh_seconds,
        settings.summary_seconds,
    )
    try:
        await stop.wait()
    finally:
        watchdog.cancel()
        try:
            await watchdog
        except asyncio.CancelledError:
            pass
        await proxy.close()
        audit.close()
        logger.info("Sandbox egress proxy stopped")


def _env_opt(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="egress-proxy",
        description="LearnGraph sandbox egress proxy (reviewed outbound CONNECT).",
    )
    parser.add_argument(
        "--host",
        default=_env_opt("LEARNGRAPH_SANDBOX_EGRESS_PROXY_HOST", "0.0.0.0"),
        help="Bind host (default: LEARNGRAPH_SANDBOX_EGRESS_PROXY_HOST or 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(_env_opt("LEARNGRAPH_SANDBOX_EGRESS_PROXY_PORT", "8888")),
        help="Bind port (default: LEARNGRAPH_SANDBOX_EGRESS_PROXY_PORT or 8888).",
    )
    parser.add_argument(
        "--policy-dir",
        default=_env_opt("LEARNGRAPH_SANDBOX_EGRESS_POLICY_DIR", "./data/egress-policies"),
        help="Directory of reviewed {workspace_id}.json policy files.",
    )
    parser.add_argument(
        "--audit-log",
        default=_env_opt("LEARNGRAPH_EGRESS_PROXY_AUDIT_LOG", ""),
        help="JSONL audit file for every allow/deny decision (default: stdout logs).",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=float,
        default=float(_env_opt("LEARNGRAPH_EGRESS_PROXY_REFRESH_SECONDS", "5")),
        help="Policy directory refresh interval (default: 5).",
    )
    parser.add_argument(
        "--summary-seconds",
        type=float,
        default=float(_env_opt("LEARNGRAPH_EGRESS_PROXY_SUMMARY_SECONDS", "3600")),
        help=(
            "Interval for the degraded-state policy summary line; 0 disables it "
            "(default: 3600). Per-file messages are always reported once per revision."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = EgressProxySettings(
        host=args.host,
        port=args.port,
        policy_dir=Path(args.policy_dir).expanduser(),
        audit_log=Path(args.audit_log).expanduser() if args.audit_log else None,
        refresh_seconds=max(1.0, args.refresh_seconds),
        summary_seconds=max(0.0, args.summary_seconds),
    )
    try:
        asyncio.run(_serve(settings))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(main())
