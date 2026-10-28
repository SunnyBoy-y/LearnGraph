"""Serve only the explicitly named disposable acceptance stack for UI review."""
import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path

from updater.agent import handler
from updater.tests.postgresql_roundtrip import FixtureAgent
from updater.tests.docker_isolation import require


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--port", type=int, default=18746)
    args = parser.parse_args()
    root = args.root.resolve()
    evidence = json.loads((root / "verification.json").read_text())
    if not evidence.get("passed") or not evidence["project"].startswith("lg-pg-maintenance-"):
        parser.error("requires a passed disposable PostgreSQL acceptance stack")
    agent = FixtureAgent(root, "LearnGraph/UpdateFixture", evidence["image"], ["compose.json"], timeout=100)
    require(evidence["project"] == agent.verify_isolation()["project"], "fixture project mismatch")
    agent.recover()
    # No background polling: this preview never initiates a future update.
    server = ThreadingHTTPServer(("0.0.0.0", args.port), handler(agent))
    server.daemon_threads = True
    print("Disposable PostgreSQL updater preview ready; automatic polling is off", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
