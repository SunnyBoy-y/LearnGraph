"""Host Service Bridge unit tests (registry, validation, deny-by-default, proxy).

背景：整体 Docker 部署后 ``app`` 容器内的 ``127.0.0.1`` 指向容器自己，宿主
机 Ollama/MCP 等 loopback 服务不可达。Host Service Bridge 是宿主侧常驻进程，
只对 ``/services/<id>/...`` 做授权转发（默认 DENY + token + JSONL 审计），
配合 backend 的 Host Service Resolver 把逻辑地址改写为 bridge 地址。

本测试锁定：
- 注册表文档校验（id 必须 path-safe、target 必须是绝对 http(s) URL）；
- 目标地址分类（loopback 放行 / private 需显式 opt-in / 公网拒绝）；
- 注册表目录刷新（mtime 缓存、坏文件跳过、id 与文件名不一致拒绝）；
- bridge 鉴权（无 token 拒绝、错误 token 拒绝、缺 Authorization 拒绝）；
- 未知/禁用服务与路径前缀白名单的 403；
- 真实 HTTP 转发（含 /v1 前缀转发到目标 /v1 路径）与审计记录。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.services.host_service_bridge import (
    DirectoryHostServiceRegistry,
    HostServiceBridge,
    HostServiceInvalid,
    _target_address_is_allowed,
    validate_host_service,
)

TOKEN = "test-bridge-token-0123456789abcdef"


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


class TestValidateHostService:
    def test_valid_minimal(self) -> None:
        svc = validate_host_service(
            {"id": "ollama", "target": "http://127.0.0.1:11434"}
        )
        assert svc.id == "ollama"
        assert svc.target == "http://127.0.0.1:11434"
        assert svc.enabled is True
        assert svc.allowed_paths == ()
        assert svc.headers == {}

    def test_id_must_be_path_safe(self) -> None:
        for bad in ["a/b", "a\\b", "", ".", "..", "a b"]:
            with pytest.raises(HostServiceInvalid):
                validate_host_service({"id": bad, "target": "http://127.0.0.1:1"})

    def test_target_must_be_absolute_http(self) -> None:
        for bad in ["127.0.0.1:11434", "ftp://127.0.0.1", "", "http://user:pw@h:1", "http://h:1/#frag"]:
            with pytest.raises(HostServiceInvalid):
                validate_host_service({"id": "x", "target": bad})

    def test_allowed_paths_and_headers(self) -> None:
        svc = validate_host_service(
            {
                "id": "mcp",
                "target": "http://127.0.0.1:3001",
                "allowed_paths": ["/mcp", "/health"],
                "headers": {"X-Token": "abc"},
            }
        )
        assert svc.match_path("mcp") is True
        assert svc.match_path("mcp/tools/list") is True
        assert svc.match_path("other") is False
        assert svc.headers == {"X-Token": "abc"}


class TestTargetAddressAllowed:
    def test_loopback_allowed(self) -> None:
        allowed, reason = _target_address_is_allowed("http://127.0.0.1:11434", allow_private=False)
        assert allowed and reason is None

    def test_private_requires_opt_in(self) -> None:
        # 172.16-31.x is a private range; default must refuse it.
        allowed, _ = _target_address_is_allowed("http://172.17.0.1:11434", allow_private=False)
        assert allowed is False
        allowed, reason = _target_address_is_allowed("http://172.17.0.1:11434", allow_private=True)
        assert allowed and reason is None

    def test_public_internet_refused(self) -> None:
        # IP literal so the assertion never depends on ambient DNS (fake-ip
        # proxies resolve real domains to synthetic loopback/private addresses).
        allowed, _ = _target_address_is_allowed("https://8.8.8.8:443", allow_private=True)
        assert allowed is False


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


class TestDirectoryRegistry:
    def test_loads_valid_skips_invalid(self, tmp_path: Path) -> None:
        (tmp_path / "ollama.json").write_text(
            json.dumps({"id": "ollama", "target": "http://127.0.0.1:11434"}),
            encoding="utf-8",
        )
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "mismatch.json").write_text(
            json.dumps({"id": "other", "target": "http://127.0.0.1:1"}),
            encoding="utf-8",
        )
        registry: dict = {}
        DirectoryHostServiceRegistry(tmp_path).refresh_into(registry)
        assert set(registry) == {"ollama"}

    def test_mtime_cache_skips_reread(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"id": "s", "target": "http://127.0.0.1:1"}), encoding="utf-8")
        source = DirectoryHostServiceRegistry(tmp_path)
        registry: dict = {}
        source.refresh_into(registry)
        assert "s" in registry
        # Touch without content change; mtime cache still refreshes on second
        # pass because stat mtime changed. A third pass with unchanged stat is
        # served from cache (we assert the value survives both paths).
        time.sleep(0.02)
        path.write_text(json.dumps({"id": "s", "target": "http://127.0.0.1:1", "enabled": False}), encoding="utf-8")
        source.refresh_into(registry)
        assert registry["s"].enabled is False
        source.refresh_into(registry)
        assert registry["s"].enabled is False


# ---------------------------------------------------------------------------
# bridge app
# ---------------------------------------------------------------------------


class _FakeTarget:
    """Minimal loopback HTTP target the bridge forwards to."""

    def __init__(self) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                self.server.requests.append(  # type: ignore[attr-defined]
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {k: v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                payload = json.dumps({"path": self.path}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _handle
            do_POST = _handle
            do_PUT = _handle
            do_DELETE = _handle

            def log_message(self, *args: object) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.requests = self.requests  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def target():
    server = _FakeTarget()
    yield server
    server.close()


def _client_for(token: str = TOKEN) -> TestClient:
    bridge = HostServiceBridge(token=token)
    return TestClient(bridge.app())


class TestBridgeAuth:
    def test_missing_token_refused_at_construction(self) -> None:
        with pytest.raises(ValueError):
            HostServiceBridge(token="")

    def test_healthz_requires_token(self) -> None:
        client = _client_for()
        resp = client.get("/healthz")
        assert resp.status_code == 403

    def test_wrong_token_denied(self) -> None:
        client = _client_for()
        resp = client.get("/healthz", headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 403

    def test_healthz_ok_with_token(self) -> None:
        client = _client_for()
        resp = client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_x_learngraph_token_header_accepted(self) -> None:
        """Backend authenticates the bridge hop with X-LearnGraph-Host-Bridge-Token
        (its provider/MCP Authorization carries the target service credential)."""
        client = _client_for()
        resp = client.get(
            "/healthz",
            headers={"X-LearnGraph-Host-Bridge-Token": TOKEN},
        )
        assert resp.status_code == 200

    def test_x_learngraph_token_header_wrong_rejected(self) -> None:
        client = _client_for()
        resp = client.get(
            "/healthz",
            headers={"X-LearnGraph-Host-Bridge-Token": "nope"},
        )
        assert resp.status_code == 403


class TestBridgeForwarding:
    def _client_with_registry(self, services: dict, token: str = TOKEN):
        bridge = HostServiceBridge(token=token, registry=services)
        return TestClient(bridge.app())

    def test_unknown_service_denied(self, target) -> None:
        client = self._client_with_registry({})
        resp = client.get(
            f"/services/nope/x", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert resp.status_code == 403

    def test_disabled_service_denied(self, target) -> None:
        svc = validate_host_service(
            {"id": "ollama", "target": f"http://127.0.0.1:{target.port}", "enabled": False}
        )
        client = self._client_with_registry({"ollama": svc})
        resp = client.get(
            f"/services/ollama/v1/models", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert resp.status_code == 403

    def test_path_prefix_allowlist(self, target) -> None:
        svc = validate_host_service(
            {
                "id": "ollama",
                "target": f"http://127.0.0.1:{target.port}",
                "allowed_paths": ["/v1", "/api"],
            }
        )
        client = self._client_with_registry({"ollama": svc})
        ok = client.get("/services/ollama/v1/models", headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200
        denied = client.get("/services/ollama/admin", headers={"Authorization": f"Bearer {TOKEN}"})
        assert denied.status_code == 403

    def test_forwards_path_and_headers(self, target) -> None:
        svc = validate_host_service(
            {
                "id": "ollama",
                "target": f"http://127.0.0.1:{target.port}",
                "headers": {"X-Service": "ollama"},
            }
        )
        client = self._client_with_registry({"ollama": svc})
        resp = client.post(
            "/services/ollama/v1/chat/completions?stream=false",
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            json={"model": "qwen3"},
        )
        assert resp.status_code == 200
        # The fake target echoes self.path (query included).
        assert resp.json()["path"] == "/v1/chat/completions?stream=false"
        assert len(target.requests) == 1
        req = target.requests[0]
        assert req["method"] == "POST"
        assert req["path"] == "/v1/chat/completions?stream=false"
        # bridge token must never leak to the target
        assert "authorization" not in {k.casefold() for k in req["headers"]}
        assert req["headers"].get("X-Service") == "ollama"
        assert json.loads(req["body"]) == {"model": "qwen3"}

    def test_audit_allow_and_deny(self, target) -> None:
        events: list[dict] = []
        svc = validate_host_service(
            {"id": "ollama", "target": f"http://127.0.0.1:{target.port}"}
        )
        bridge = HostServiceBridge(
            token=TOKEN, registry={"ollama": svc}, on_decision=events.append
        )
        client = TestClient(bridge.app())
        client.get("/services/ollama/v1/models", headers={"Authorization": f"Bearer {TOKEN}"})
        client.get("/services/ghost/x", headers={"Authorization": f"Bearer {TOKEN}"})
        decisions = [e["decision"] for e in events]
        assert decisions == ["allow", "deny"]
        allow = events[0]
        assert allow["service_id"] == "ollama"
        assert allow["path"] == "/services/ollama/v1/models"
        deny = events[1]
        assert deny["reason"] == "unknown_service"
