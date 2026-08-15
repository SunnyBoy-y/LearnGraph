"""Local end-to-end smoke of the standalone egress proxy (not part of pytest).

Starts the proxy on a random port with a temp policy directory, then exercises
the CONNECT authorization path: allowed host -> 200, wrong digest -> 403,
unapproved host -> 403, invalid authority -> 400.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
PYTHON = str(BACKEND_DIR / ".venv" / "Scripts" / "python.exe")

POLICY = {
    "workspace_id": "ws-smoke",
    "approval_id": "smoke-approval",
    "issuer": "agent_egress_authorization",
    "issued_at": datetime.now(timezone.utc).isoformat(),
    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    "hosts": [{"host": "example.com", "ports": [443], "protocols": ["https"]}],
}


def send_connect(port: int, authority: str, digest: str | None) -> tuple[int, str]:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    headers = [f"CONNECT {authority} HTTP/1.1", f"Host: {authority}"]
    if digest:
        headers.append(f"X-LearnGraph-Policy-Digest: {digest}")
    request = "\r\n".join(headers) + "\r\n\r\n"
    sock.sendall(request.encode("latin-1"))
    response = sock.recv(1024).decode("latin-1", errors="replace")
    status_line = response.split("\r\n", 1)[0]
    sock.close()
    return int(status_line.split()[1]), status_line


async def main() -> int:
    from app.services.sandbox_network_policy import validate_egress_policy

    with tempfile.TemporaryDirectory() as tmp:
        policy_dir = Path(tmp) / "policies"
        policy_dir.mkdir()
        (policy_dir / "ws-smoke.json").write_text(
            json.dumps(POLICY, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        digest = validate_egress_policy(POLICY).digest

        port = 18999
        env = {
            **os.environ,
            "PYTHONPATH": str(BACKEND_DIR),
        }
        proc = subprocess.Popen(
            [
                PYTHON,
                "-m",
                "app.services.egress_proxy_main",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--policy-dir",
                str(policy_dir),
            ],
            env=env,
            cwd=BACKEND_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=1).close()
                    break
                except OSError:
                    await asyncio.sleep(0.2)
            else:
                print("FAIL: proxy did not start")
                return 1

            code, line = send_connect(port, "example.com:443", digest)
            print(f"allowed host + digest: {line} -> {'PASS' if code == 200 else 'FAIL'}")
            assert code == 200

            code, line = send_connect(port, "example.com:443", "deadbeef")
            print(f"wrong digest: {line} -> {'PASS' if code == 403 else 'FAIL'}")
            assert code == 403

            code, line = send_connect(port, "evil.example.org:443", digest)
            print(f"unapproved host: {line} -> {'PASS' if code == 403 else 'FAIL'}")
            assert code == 403

            code, line = send_connect(port, "not-an-authority", digest)
            print(f"invalid authority: {line} -> {'PASS' if code == 400 else 'FAIL'}")
            assert code == 400

            print("ALL EGRESS SMOKE CHECKS PASSED")
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.path.insert(0, str(BACKEND_DIR))
    raise SystemExit(asyncio.run(main()))
