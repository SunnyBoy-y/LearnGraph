"""Host Bridge status guidance tests (frontend hint backend).

锁定 status 端点的纯函数：_tcp_reachable 探活与 _host_bridge_guidance 文案
分支（未运行 / 运行但 token 未挂载 / 就绪 / 源码模式不提示）。
"""
from __future__ import annotations

import pytest

from app.services.management import _host_bridge_guidance, _tcp_reachable

BRIDGE = "http://host.docker.internal:34115"


class TestTcpReachable:
    def test_unreachable_host_false(self) -> None:
        # 无监听端口（大概率无服务）→ False；短超时避免拖慢测试。
        assert _tcp_reachable("http://127.0.0.1:1", timeout_seconds=0.5) is False

    def test_malformed_url_false(self) -> None:
        assert _tcp_reachable("not a url", timeout_seconds=0.5) is False
        assert _tcp_reachable("", timeout_seconds=0.5) is False

    def test_reachable_true(self) -> None:
        # 用 TCP 服务端自证可达（127.0.0.1 回环必然可达）。
        import socket
        import threading

        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def _accept() -> None:
            try:
                conn, _ = server.accept()
                conn.close()
            except OSError:
                pass

        threading.Thread(target=_accept, daemon=True).start()
        try:
            assert _tcp_reachable(f"http://127.0.0.1:{port}", timeout_seconds=2.0) is True
        finally:
            server.close()


class TestHostBridgeGuidance:
    def test_source_mode_empty(self) -> None:
        assert (
            _host_bridge_guidance(
                host_access_mode="off",
                effective=None,
                reachable=None,
                token_ready=False,
                host_gateway_reachable=None,
            )
            == ""
        )

    def test_bridge_not_running(self) -> None:
        text = _host_bridge_guidance(
            host_access_mode="bridge",
            effective=BRIDGE,
            reachable=False,
            token_ready=False,
            host_gateway_reachable=None,
        )
        assert "node scripts/host-bridge.mjs" in text
        assert "docker compose restart app preview" in text

    def test_bridge_running_token_missing(self) -> None:
        text = _host_bridge_guidance(
            host_access_mode="bridge",
            effective=BRIDGE,
            reachable=True,
            token_ready=False,
            host_gateway_reachable=None,
        )
        assert "已运行" in text
        assert "token" in text
        assert "node scripts/host-bridge.mjs" in text

    def test_all_ready(self) -> None:
        text = _host_bridge_guidance(
            host_access_mode="bridge",
            effective=BRIDGE,
            reachable=True,
            token_ready=True,
            host_gateway_reachable=None,
        )
        assert "已就绪" in text

    def test_direct_mode_gateway_ok(self) -> None:
        text = _host_bridge_guidance(
            host_access_mode="direct",
            effective=None,
            reachable=None,
            token_ready=False,
            host_gateway_reachable=True,
        )
        assert "可信桌面直连已启用" in text
        assert "host.docker.internal" in text
        assert "无需 Host Service Bridge" in text

    def test_direct_mode_gateway_down(self) -> None:
        text = _host_bridge_guidance(
            host_access_mode="direct",
            effective=None,
            reachable=None,
            token_ready=False,
            host_gateway_reachable=False,
        )
        assert "无法解析 host.docker.internal" in text
        assert "extra_hosts" in text
