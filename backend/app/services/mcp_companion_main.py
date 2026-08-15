"""Standalone stdio MCP Host Companion entrypoint (real machine, host side).

Spawns reviewed stdio MCP processes on the physical host and bridges them to
the container through the Host Service Bridge:

    container --bridge /services/mcp-companion/rpc--> host bridge
            --> http://127.0.0.1:34116/rpc --> companion --> stdio MCP process

Start (real machine):

    export LEARNGRAPH_MCP_COMPANION_TOKEN=$(openssl rand -hex 32)
    LEARNGRAPH_MCP_COMPANION_TOKEN=$TOKEN \\
        uv run python -m app.services.mcp_companion_main \\
            --launch-dir ./data/mcp-stdio-launches

Launch files: one ``{id}.json`` per stdio MCP server, e.g. ``filesystem.json``:

    {
      "id": "filesystem",
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/data"],
      "env": {},
      "cwd": null,
      "timeout_seconds": 60
    }

Then register the companion in the Host Service Bridge registry as
``mcp-companion.json`` targeting ``http://127.0.0.1:34116`` so the container
can reach it through the bridge. The companion refuses to start without a
token (fail closed) and listens on loopback by default.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from app.services.mcp_companion import DirectoryStdioRegistry, StdioCompanion


def _env_opt(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-companion",
        description="LearnGraph stdio MCP Host Companion (host-side stdio bridge).",
    )
    parser.add_argument(
        "--host",
        default=_env_opt("LEARNGRAPH_MCP_COMPANION_HOST", "127.0.0.1"),
        help="Bind host (default: LEARNGRAPH_MCP_COMPANION_HOST or 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(_env_opt("LEARNGRAPH_MCP_COMPANION_PORT", "34116")),
        help="Bind port (default: LEARNGRAPH_MCP_COMPANION_PORT or 34116).",
    )
    parser.add_argument(
        "--launch-dir",
        default=_env_opt("LEARNGRAPH_MCP_COMPANION_LAUNCH_DIR", "./data/mcp-stdio-launches"),
        help="Directory of reviewed {id}.json stdio MCP launch files.",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=float,
        default=float(_env_opt("LEARNGRAPH_MCP_COMPANION_REFRESH_SECONDS", "5")),
        help="Launch directory refresh interval (default: 5).",
    )
    parser.add_argument(
        "--token",
        default=_env_opt("LEARNGRAPH_MCP_COMPANION_TOKEN", ""),
        help="Bearer token required on every request (default: LEARNGRAPH_MCP_COMPANION_TOKEN). "
        "The companion refuses to start without one.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = args.token
    if not token:
        print(
            "Stdio MCP Companion refuses to start without "
            "LEARNGRAPH_MCP_COMPANION_TOKEN (fail closed).",
            file=os.sys.stderr,
        )
        return 2
    source = DirectoryStdioRegistry(Path(args.launch_dir).expanduser())
    companion = StdioCompanion(
        token=token,
        source=source,
        refresh_seconds=max(1.0, args.refresh_seconds),
    )
    uvicorn.run(
        companion.app(),
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
