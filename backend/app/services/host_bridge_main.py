"""Standalone Host Service Bridge entrypoint for the real machine (host side).

Runs ``HostServiceBridge`` as an independent process so the physical host can
expose its loopback services (Ollama, LM Studio, local HTTP MCP, local APIs)
to the whole-app Docker deployment without opening arbitrary ports. The
backend reaches it through ``host.docker.internal`` (compose already wires
``extra_hosts: host.docker.internal:host-gateway``) at the port below.

Start (real machine):

    export LEARNGRAPH_HOST_BRIDGE_TOKEN=$(openssl rand -hex 32)
    LEARNGRAPH_HOST_BRIDGE_TOKEN=$TOKEN \\
        uv run python -m app.services.host_bridge_main \\
            --registry-dir ./data/host-services \\
            --audit-log ./data/host-bridge-audit.jsonl

Registry files: one ``{id}.json`` per service, e.g. ``ollama.json``:

    {
      "id": "ollama",
      "target": "http://127.0.0.1:11434",
      "kind": "http",
      "enabled": true,
      "allowed_paths": ["/v1", "/api"],
      "headers": {}
    }

The bridge refuses to start without a token (fail closed), serves only
``/services/<id>/...``, targets loopback (or explicitly opted-in private
addresses) and audits every allow/deny decision.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from app.services.host_service_bridge import DirectoryHostServiceRegistry, HostServiceBridge

logger = logging.getLogger(__name__)


def _env_opt(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="host-bridge",
        description="LearnGraph Host Service Bridge (authorized loopback service proxy).",
    )
    parser.add_argument(
        "--host",
        default=_env_opt("LEARNGRAPH_HOST_BRIDGE_HOST", "0.0.0.0"),
        help="Bind host (default: LEARNGRAPH_HOST_BRIDGE_HOST or 0.0.0.0). "
        "Bind a specific adapter (e.g. the WSL vEthernet interface on Windows, "
        "or docker0 on Linux) to keep the bridge off the LAN; the token still "
        "protects every request.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(_env_opt("LEARNGRAPH_HOST_BRIDGE_PORT", "34115")),
        help="Bind port (default: LEARNGRAPH_HOST_BRIDGE_PORT or 34115).",
    )
    parser.add_argument(
        "--registry-dir",
        default=_env_opt("LEARNGRAPH_HOST_BRIDGE_REGISTRY_DIR", "./data/host-services"),
        help="Directory of reviewed {id}.json host service registry files.",
    )
    parser.add_argument(
        "--audit-log",
        default=_env_opt("LEARNGRAPH_HOST_BRIDGE_AUDIT_LOG", ""),
        help="JSONL audit file for every allow/deny decision (default: stdout logs).",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=float,
        default=float(_env_opt("LEARNGRAPH_HOST_BRIDGE_REFRESH_SECONDS", "5")),
        help="Registry directory refresh interval (default: 5).",
    )
    parser.add_argument(
        "--token",
        default=_env_opt("LEARNGRAPH_HOST_BRIDGE_TOKEN", ""),
        help="Bearer token required on every request (default: LEARNGRAPH_HOST_BRIDGE_TOKEN). "
        "The bridge refuses to start without one.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = args.token
    if not token:
        print(
            "Host Service Bridge refuses to start without LEARNGRAPH_HOST_BRIDGE_TOKEN "
            "(fail closed). Generate one with: openssl rand -hex 32",
            file=os.sys.stderr,
        )
        return 2
    source = DirectoryHostServiceRegistry(Path(args.registry_dir).expanduser())
    audit_path = Path(args.audit_log).expanduser() if args.audit_log else None
    from app.services.host_service_bridge import JsonlAuditSink

    audit = JsonlAuditSink(audit_path)
    audit.open()
    bridge = HostServiceBridge(
        token=token,
        source=source,
        on_decision=audit,
        refresh_seconds=max(1.0, args.refresh_seconds),
    )
    uvicorn.run(
        bridge.app(),
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(main())
