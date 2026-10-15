"""End-to-end: Host Service Bridge -> stdio MCP Companion -> fake stdio MCP.

验证完整链路：容器经 bridge 的 /services/mcp-companion/rpc 调用，bridge 注入
per-service Authorization（companion token，bridge 自身的 token 不转发），
companion 鉴权后 spawn stdio MCP 进程并桥接 JSON-RPC。
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.services.host_service_bridge import HostServiceBridge, validate_host_service
from app.services.mcp_companion import StdioCompanion, StdioLaunch

BRIDGE_TOKEN = "bridge-e2e-token-0123456789"
COMPANION_TOKEN = "companion-e2e-token-0123456789"

FAKE_MCP_SERVER = r"""
import sys, json
for line in sys.stdin.buffer:
    try:
        msg = json.loads(line.decode("utf-8"))
    except Exception:
        continue
    if isinstance(msg, dict) and "id" in msg:
        out = json.dumps({"id": msg["id"], "result": {"echo": msg.get("params")}}).encode("utf-8") + b"\n"
        sys.stdout.buffer.write(out)
        sys.stdout.buffer.flush()
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def companion_server():
    launch = StdioLaunch(
        id="fake",
        command=(sys.executable, "-u", "-c", FAKE_MCP_SERVER),
        env={},
        cwd=None,
        timeout_seconds=15.0,
    )
    companion = StdioCompanion(token=COMPANION_TOKEN, registry={"fake": launch})
    port = _free_port()
    config = uvicorn.Config(companion.app(), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "companion uvicorn did not start"
    yield {"port": port, "server": server, "companion": companion}
    server.should_exit = True
    thread.join(timeout=10)


class TestBridgeToCompanionE2E:
    def test_full_chain(self, companion_server) -> None:
        port = companion_server["port"]
        svc = validate_host_service(
            {
                "id": "mcp-companion",
                "target": f"http://127.0.0.1:{port}",
                "headers": {"Authorization": f"Bearer {COMPANION_TOKEN}"},
            }
        )
        bridge = HostServiceBridge(token=BRIDGE_TOKEN, registry={"mcp-companion": svc})
        client = TestClient(bridge.app())

        resp = client.post(
            "/services/mcp-companion/rpc",
            headers={"Authorization": f"Bearer {BRIDGE_TOKEN}"},
            json={
                "launch_id": "fake",
                "message": {"id": 99, "method": "tools/call", "params": {"tool": "echo", "ok": True}},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == 99
        assert body["result"]["echo"] == {"tool": "echo", "ok": True}

    def test_bridge_token_not_forwarded(self, companion_server) -> None:
        # The companion only knows its own token; if the bridge leaked its
        # token, the companion would reject the call with 403. A 200 here
        # proves per-service header injection worked.
        port = companion_server["port"]
        svc = validate_host_service(
            {
                "id": "mcp-companion",
                "target": f"http://127.0.0.1:{port}",
                "headers": {"Authorization": f"Bearer {COMPANION_TOKEN}"},
            }
        )
        bridge = HostServiceBridge(token=BRIDGE_TOKEN, registry={"mcp-companion": svc})
        client = TestClient(bridge.app())
        resp = client.post(
            "/services/mcp-companion/rpc",
            headers={"Authorization": f"Bearer {BRIDGE_TOKEN}"},
            json={"launch_id": "fake", "message": {"id": 1, "method": "tools/call", "params": {}}},
        )
        assert resp.status_code == 200
