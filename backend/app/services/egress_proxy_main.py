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
    system_resolver,
    utc_now,
    validate_egress_policy,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EgressProxySettings:
    host: str = "0.0.0.0"
    port: int = 8888
    policy_dir: Path = Path("./data/egress-policies")
    audit_log: Path | None = None
    refresh_seconds: float = 5.0
    max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES
    max_idle_seconds: float = DEFAULT_MAX_IDLE_SECONDS
    max_tunnel_bytes: int = DEFAULT_MAX_TUNNEL_BYTES


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
    """

    def __init__(self, policy_dir: str | Path) -> None:
        self.directory = Path(policy_dir)
        self._cache: dict[str, tuple[int, EgressPolicy]] = {}

    def refresh_into(self, registry: dict[str, EgressPolicy]) -> None:
        """Replace ``registry`` contents with the current directory snapshot.

        ``registry`` is mutated in place (``clear`` + ``update``) because the
        running proxy holds a reference to it and the refresh runs on the same
        event loop.
        """
        current: dict[str, EgressPolicy] = {}
        try:
            entries = sorted(self.directory.glob("*.json"))
        except OSError as exc:
            logger.error("Egress policy directory %s is unreadable: %s", self.directory, exc)
            registry.clear()
            return
        now = utc_now()
        for path in entries:
            try:
                stat = path.stat()
            except OSError:
                continue
            cached = self._cache.get(str(path))
            if cached is not None and cached[0] == stat.st_mtime_ns:
                current[str(path)] = cached[1]
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                policy = validate_egress_policy(raw, now=now)
            except (OSError, ValueError, EgressPolicyInvalid) as exc:
                logger.error(
                    "Egress policy file %s is invalid; egress denied for its workspace: %s",
                    path,
                    exc,
                )
                self._cache.pop(str(path), None)
                continue
            self._cache[str(path)] = (stat.st_mtime_ns, policy)
            current[str(path)] = policy

        stale = [key for key in self._cache if key not in current]
        for key in stale:
            self._cache.pop(key, None)

        registry.clear()
        for policy in current.values():
            registry[policy.digest] = policy


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
) -> None:
    while not stop.is_set():
        try:
            source.refresh_into(proxy.policy_registry)  # type: ignore[arg-type]
        except Exception:
            logger.exception("Egress policy refresh failed; previous policies remain in force")
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
        _registry_watchdog(proxy, source, settings.refresh_seconds, stop)
    )
    logger.info(
        "Sandbox egress proxy listening on %s:%s (policies: %s, refresh: %ss)",
        settings.host,
        bound,
        settings.policy_dir,
        settings.refresh_seconds,
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = EgressProxySettings(
        host=args.host,
        port=args.port,
        policy_dir=Path(args.policy_dir).expanduser(),
        audit_log=Path(args.audit_log).expanduser() if args.audit_log else None,
        refresh_seconds=max(1.0, args.refresh_seconds),
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
