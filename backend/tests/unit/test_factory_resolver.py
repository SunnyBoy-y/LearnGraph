"""Provider 全接口直连/桥接改写覆盖 + 源码模式零影响锁定测试。

背景：可信桌面直连（LEARNGRAPH_HOST_ACCESS_MODE=direct）与 Host Service
Bridge 必须覆盖 Provider 管理的**所有**出站接口（model/embedding/image/
transcription/search/fetch/deep-research/memory/MCP），而不只是 Ollama；
同时源码启动（npm run dev / personal_desktop / 非容器）必须零影响——任何
改写路径在源码模式下原样返回。

锁定规则：
- personal_desktop 或非容器（running_in_container=False）-> 永不改写；
- 非 loopback 远端 URL -> 永不改写；
- direct + 容器内 + loopback -> host.docker.internal:同端口；
- bridge + 容器内 + loopback -> {bridge}/services/{type}/{path}（非 ollama
  类型 service_id 保持 provider_type）；
- MCP HTTP 适配器 direct 模式下改写 endpoint 并把 host.docker.internal 加入
  明文 HTTP 白名单。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.providers.factory import _resolve_provider_base_url
from app.providers.host_service_resolver import is_loopback_url, resolve_loopback_url

BRIDGE = "http://host.docker.internal:34115"


class TestSourceInstallZeroImpact:
    """源码模式（personal_desktop / 非容器 / 远端 URL）必须原样返回。"""

    def test_personal_desktop_never_rewrites(self) -> None:
        settings = Settings(deployment_profile="personal_desktop")
        for url in (
            "http://127.0.0.1:8080/v1",
            "http://localhost:8888",
            "https://api.deepseek.com/v1",
        ):
            assert _resolve_provider_base_url(settings, "openai_compatible_chat", url) == url

    def test_direct_outside_container_falls_back_to_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.core.config.running_in_container", lambda: False)
        settings = Settings(deployment_profile="self_hosted_team", host_access_mode="direct")
        url = "http://127.0.0.1:8888"
        assert _resolve_provider_base_url(settings, "searxng", url) == url

    def test_remote_urls_never_rewritten_in_any_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.core.config.running_in_container", lambda: True)
        settings = Settings(deployment_profile="self_hosted_team", host_access_mode="direct")
        for url in (
            "https://api.deepseek.com/v1",
            "https://api.tavily.com/search",
            "https://mem0.ai/v1",
        ):
            assert _resolve_provider_base_url(settings, "deepseek_chat", url) == url

    def test_off_mode_never_rewrites(self) -> None:
        settings = Settings(
            deployment_profile="self_hosted_team", host_access_mode="off"
        )
        url = "http://127.0.0.1:8080/v1"
        assert _resolve_provider_base_url(settings, "openai_compatible_chat", url) == url


class TestDirectCoversAllProviderTypes:
    """direct + 容器内：任意类型 loopback 一律改写到宿主同端口。"""

    @pytest.fixture(autouse=True)
    def _in_container(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.core.config.running_in_container", lambda: True)

    def _settings(self) -> Settings:
        return Settings(deployment_profile="self_hosted_team", host_access_mode="direct")

    @pytest.mark.parametrize(
        ("provider_type", "base_url", "expected"),
        [
            ("openai_compatible_chat", "http://127.0.0.1:8080/v1", "http://host.docker.internal:8080/v1"),
            ("searxng", "http://127.0.0.1:8888", "http://host.docker.internal:8888"),
            ("crawl4ai_http", "http://127.0.0.1:11235", "http://host.docker.internal:11235"),
            ("openai_compatible_embedding", "http://127.0.0.1:11434/v1", "http://host.docker.internal:11434/v1"),
            ("openai_compatible_vision", "http://127.0.0.1:1234/v1", "http://host.docker.internal:1234/v1"),
            ("openai_compatible_transcription", "http://127.0.0.1:9000/v1", "http://host.docker.internal:9000/v1"),
            ("http_deep_research", "http://127.0.0.1:9090", "http://host.docker.internal:9090"),
        ],
    )
    def test_rewrites_loopback_preserving_port(self, provider_type: str, base_url: str, expected: str) -> None:
        assert _resolve_provider_base_url(self._settings(), provider_type, base_url) == expected


class TestBridgeCoversAllProviderTypes:
    """bridge + 容器内：任意类型 loopback 改写到 /services/{type}/...。"""

    @pytest.fixture(autouse=True)
    def _in_container(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.core.config.running_in_container", lambda: True)

    def _settings(self) -> Settings:
        return Settings(deployment_profile="self_hosted_team", host_access_mode="bridge")

    def test_searxng_keeps_own_service_id(self) -> None:
        assert (
            _resolve_provider_base_url(self._settings(), "searxng", "http://127.0.0.1:8888")
            == f"{BRIDGE}/services/searxng/"
        )

    def test_openai_compatible_keeps_own_service_id(self) -> None:
        assert (
            _resolve_provider_base_url(
                self._settings(), "openai_compatible_chat", "http://127.0.0.1:8080/v1"
            )
            == f"{BRIDGE}/services/openai_compatible_chat/v1"
        )


class TestMCPAdapterDirect:
    """MCP HTTP 适配器在 direct 模式下必须改写 endpoint 并放行明文 HTTP。"""

    def _service(self) -> SimpleNamespace:
        from app.services.mcp_skills import MCPAndSkillService

        service = MCPAndSkillService.__new__(MCPAndSkillService)
        service.settings = SimpleNamespace(
            effective_host_bridge_url=None,
            effective_host_access_mode="direct",
            deployment_profile="self_hosted_team",
            host_bridge_token_file=None,
            mcp_stdio_runner_enabled=False,
        )
        return service

    def test_http_mcp_endpoint_rewritten_to_host_docker(self, docker_host_dns) -> None:
        service = self._service()
        server = SimpleNamespace(
            id="srv-test",
            server_key="local-mcp",
            auth_reference=None,
            endpoint_url="http://127.0.0.1:34871/mcp",
            transport="streamable_http",
            timeout_ms=5000,
            max_result_bytes=65536,
            max_concurrency=1,
        )
        adapter = service._adapter(server)
        assert adapter.endpoint_url == "http://host.docker.internal:34871/mcp"

    def test_rewrite_loopback_url_direct(self) -> None:
        assert (
            resolve_loopback_url(
                service_id="mcp-local-mcp",
                base_url="http://127.0.0.1:34871/mcp",
                host_bridge_url=None,
                deployment_profile="self_hosted_team",
                host_access_mode="direct",
            )
            == "http://host.docker.internal:34871/mcp"
        )

    def test_loopback_detection(self) -> None:
        assert is_loopback_url("http://127.0.0.1:34871/mcp") is True
        assert is_loopback_url("http://host.docker.internal:34871/mcp") is False
