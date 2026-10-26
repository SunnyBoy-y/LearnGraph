"""Supervise the API and its separate-origin, read-only preview process."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

API_HEALTH = "http://127.0.0.1:8000/api/v1/livez"
PREVIEW_HEALTH = "http://127.0.0.1:8001/api/v1/livez"


def healthy(url: str, timeout: float = 2) -> bool:
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def start(application: str, port: int, *, read_only: bool = False) -> subprocess.Popen:
    environment = os.environ.copy()
    environment["LEARNGRAPH_SQLITE_READ_ONLY"] = "true" if read_only else "false"
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", application, "--host", "0.0.0.0",
         "--port", str(port), "--proxy-headers", "--forwarded-allow-ips", "*"],
        env=environment,
    )


def stop(children: list[subprocess.Popen], timeout: float = 15) -> None:
    for child in children:
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + timeout
    for child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def serve() -> int:
    stopping = False
    children: list[subprocess.Popen] = []

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        children.append(start("app.main:app", 8000))
        # Initialize schema/routes before preview opens read-only connections.
        deadline = time.monotonic() + float(os.environ.get("LEARNGRAPH_CONTAINER_STARTUP_TIMEOUT", "180"))
        while not stopping:
            if children[0].poll() is not None:
                print("API exited before preview startup", file=sys.stderr, flush=True)
                return 1
            if healthy(API_HEALTH):
                break
            if time.monotonic() >= deadline:
                print("API startup timed out", file=sys.stderr, flush=True)
                return 1
            time.sleep(0.2)
        if stopping:
            return 0
        children.append(start("app.preview:preview_app", 8001, read_only=True))
        while not stopping:
            for child in children:
                if child.poll() is not None:
                    print(f"Application child {child.pid} exited; stopping its companion", file=sys.stderr, flush=True)
                    return 1
            time.sleep(0.2)
        return 0
    finally:
        stop(children)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> int:
    if sys.argv[1:] == ["health"]:
        return 0 if all(healthy(url) for url in (API_HEALTH, PREVIEW_HEALTH)) else 1
    if sys.argv[1:]:
        raise SystemExit("Usage: python -m app.container_runtime [health]")
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
