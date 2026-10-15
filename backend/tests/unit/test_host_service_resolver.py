"""Host Service Resolver unit tests.

背景：整体 Docker 部署后容器内 127.0.0.1 指向容器自己，宿主机 Ollama/MCP
不可达。Resolver 在 Model Gateway 装配点（factory）与探测路径（_discover）
把"逻辑 loopback 地址"按宿主访问策略改写：安全桥接（Host Service Bridge，
默认）或可信桌面直连（host.docker.internal:同端口，direct 模式），业务层与
用户配置永远不感知部署形态。

锁定规则：
- host_access_mode="off" -> 原样返回；
- host_access_mode="direct" -> loopback 改写为 host.docker.internal:同端口
  （保留 path/query），非 loopback 永不改写；
- 未配置 host_bridge_url -> 原样返回（源码直连）；
- personal_desktop -> 原样返回（本机就是物理机）；
- 非 loopback 地址（远端 API）-> 永不改写；
- 容器化 + loopback + bridge -> 改写为 {bridge}/services/{id}/{path}，保留 query；
- ollama 与 ollama_embedding 映射到同一注册 id "ollama"。
"""
from __future__ import annotations

import pytest

from app.providers.host_service_resolver import (
    is_loopback_url,
    resolve_host_service_url,
    resolve_loopback_url,
    sanitize_service_id,
    service_id_for_provider,
)

BRIDGE = "http://host.docker.internal:34115"


class TestIsLoopbackUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:11434/v1",
            "http://localhost:11434",
            "http://[::1]:11434/v1",
        ],
    )
    def test_loopback(self, url: str) -> None:
        assert is_loopback_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.deepseek.com/v1",
            "https://ollama.com/v1",
            "http://host.docker.internal:34115/services/ollama/v1",
            "",
            "not a url",
        ],
    )
    def test_not_loopback(self, url: str) -> None:
        assert is_loopback_url(url) is False


class TestServiceIdForProvider:
    def test_ollama_family(self) -> None:
        assert service_id_for_provider("ollama") == "ollama"
        assert service_id_for_provider("ollama_embedding") == "ollama"

    def test_other_types_keep_name(self) -> None:
        assert service_id_for_provider("openai_chat") == "openai_chat"
        assert service_id_for_provider("") == "unknown"


class TestResolveHostServiceUrl:
    def test_no_bridge_unchanged(self) -> None:
        url = "http://127.0.0.1:11434/v1"
        assert (
            resolve_host_service_url(
                provider_type="ollama",
                base_url=url,
                host_bridge_url=None,
                deployment_profile="self_hosted_team",
            )
            == url
        )

    def test_personal_desktop_unchanged_even_with_bridge(self) -> None:
        url = "http://127.0.0.1:11434/v1"
        assert (
            resolve_host_service_url(
                provider_type="ollama",
                base_url=url,
                host_bridge_url=BRIDGE,
                deployment_profile="personal_desktop",
            )
            == url
        )

    def test_remote_url_never_rewritten(self) -> None:
        url = "https://ollama.com/v1"
        assert (
            resolve_host_service_url(
                provider_type="ollama",
                base_url=url,
                host_bridge_url=BRIDGE,
                deployment_profile="self_hosted_team",
            )
            == url
        )

    def test_docker_rewrite_with_query(self) -> None:
        result = resolve_host_service_url(
            provider_type="ollama",
            base_url="http://127.0.0.1:11434/v1",
            host_bridge_url=BRIDGE,
            deployment_profile="self_hosted_team",
        )
        assert result == f"{BRIDGE}/services/ollama/v1"

        with_query = resolve_host_service_url(
            provider_type="ollama",
            base_url="http://127.0.0.1:11434/api/tags?limit=5",
            host_bridge_url=BRIDGE,
            deployment_profile="cloud_saas",
        )
        assert with_query == f"{BRIDGE}/services/ollama/api/tags?limit=5"

    def test_ollama_embedding_uses_same_service_id(self) -> None:
        result = resolve_host_service_url(
            provider_type="ollama_embedding",
            base_url="http://127.0.0.1:11434/v1",
            host_bridge_url=BRIDGE,
            deployment_profile="self_hosted_team",
        )
        assert result.startswith(f"{BRIDGE}/services/ollama/")

    def test_custom_provider_type_keeps_own_id(self) -> None:
        result = resolve_host_service_url(
            provider_type="my_local_api",
            base_url="http://localhost:8001",
            host_bridge_url=BRIDGE,
            deployment_profile="self_hosted_team",
        )
        assert result == f"{BRIDGE}/services/my_local_api/"

    def test_openai_compatible_local_gateway_direct(self) -> None:
        """Sub2API 类 OpenAI 兼容网关：direct 模式下 loopback 必须改写。"""
        result = resolve_host_service_url(
            provider_type="openai_compatible_chat",
            base_url="http://127.0.0.1:8080/v1",
            host_bridge_url=None,
            deployment_profile="self_hosted_team",
            host_access_mode="direct",
        )
        assert result == "http://host.docker.internal:8080/v1"

    def test_openai_compatible_local_gateway_bridge(self) -> None:
        result = resolve_host_service_url(
            provider_type="openai_compatible_chat",
            base_url="http://127.0.0.1:8080/v1",
            host_bridge_url=BRIDGE,
            deployment_profile="self_hosted_team",
            host_access_mode="bridge",
        )
        assert result == f"{BRIDGE}/services/openai_compatible_chat/v1"


class TestEffectiveBridgeUrl:
    """Settings.effective_host_bridge_url auto-derivation (zero-config Docker)."""

    def test_source_profile_returns_none(self) -> None:
        from app.core.config import Settings

        settings = Settings(deployment_profile="personal_desktop")
        assert settings.effective_host_bridge_url is None

    def test_container_profile_auto_derives(self) -> None:
        from app.core.config import Settings

        settings = Settings(deployment_profile="self_hosted_team")
        assert settings.effective_host_bridge_url == "http://host.docker.internal:34115"

    def test_explicit_url_wins(self) -> None:
        from app.core.config import Settings

        settings = Settings(
            deployment_profile="self_hosted_team",
            host_bridge_url="http://192.168.1.5:34115",
        )
        assert settings.effective_host_bridge_url == "http://192.168.1.5:34115"

    def test_auto_disabled(self) -> None:
        from app.core.config import Settings

        settings = Settings(
            deployment_profile="self_hosted_team",
            host_bridge_auto=False,
        )
        assert settings.effective_host_bridge_url is None

    def test_resolver_uses_derived_bridge(self) -> None:
        from app.core.config import Settings

        settings = Settings(deployment_profile="self_hosted_team")
        result = resolve_host_service_url(
            provider_type="ollama",
            base_url="http://127.0.0.1:11434/v1",
            host_bridge_url=settings.effective_host_bridge_url,
            deployment_profile=settings.deployment_profile,
        )
        assert result == "http://host.docker.internal:34115/services/ollama/v1"


class TestDirectHostAccess:
    """host_access_mode="direct"：loopback -> host.docker.internal:同端口。"""

    def test_direct_rewrites_loopback_preserving_port(self) -> None:
        result = resolve_host_service_url(
            provider_type="ollama",
            base_url="http://127.0.0.1:11434/v1",
            host_bridge_url=None,
            deployment_profile="self_hosted_team",
            host_access_mode="direct",
        )
        assert result == "http://host.docker.internal:11434/v1"

    def test_direct_keeps_path_and_query(self) -> None:
        result = resolve_loopback_url(
            service_id="mcp-x",
            base_url="http://localhost:3001/mcp?token=abc",
            host_bridge_url=None,
            deployment_profile="self_hosted_team",
            host_access_mode="direct",
        )
        assert result == "http://host.docker.internal:3001/mcp?token=abc"

    def test_direct_adds_default_port_when_absent(self) -> None:
        result = resolve_loopback_url(
            service_id="mcp-x",
            base_url="http://localhost/mcp",
            host_bridge_url=None,
            deployment_profile="self_hosted_team",
            host_access_mode="direct",
        )
        assert result == "http://host.docker.internal:80/mcp"

    def test_direct_never_touches_remote_urls(self) -> None:
        url = "https://api.deepseek.com/v1"
        assert (
            resolve_host_service_url(
                provider_type="openai_chat",
                base_url=url,
                host_bridge_url=None,
                deployment_profile="self_hosted_team",
                host_access_mode="direct",
            )
            == url
        )

    def test_off_returns_unchanged(self) -> None:
        url = "http://127.0.0.1:11434/v1"
        assert (
            resolve_host_service_url(
                provider_type="ollama",
                base_url=url,
                host_bridge_url=None,
                deployment_profile="self_hosted_team",
                host_access_mode="off",
            )
            == url
        )

    def test_bridge_mode_matches_legacy_behavior(self) -> None:
        result = resolve_host_service_url(
            provider_type="ollama",
            base_url="http://127.0.0.1:11434/v1",
            host_bridge_url=BRIDGE,
            deployment_profile="self_hosted_team",
            host_access_mode="bridge",
        )
        assert result == f"{BRIDGE}/services/ollama/v1"


class TestEffectiveHostAccessMode:
    """Settings.effective_host_access_mode / effective_host_bridge_url resolution."""

    def test_auto_container_profile_is_bridge(self) -> None:
        from app.core.config import Settings

        settings = Settings(deployment_profile="self_hosted_team")
        assert settings.effective_host_access_mode == "bridge"
        assert settings.effective_host_bridge_url == "http://host.docker.internal:34115"

    def test_auto_source_profile_is_off(self) -> None:
        from app.core.config import Settings

        settings = Settings(deployment_profile="personal_desktop")
        assert settings.effective_host_access_mode == "off"
        assert settings.effective_host_bridge_url is None

    def test_direct_in_container(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.config import Settings, running_in_container

        monkeypatch.setattr("app.core.config.running_in_container", lambda: True)
        settings = Settings(deployment_profile="self_hosted_team", host_access_mode="direct")
        assert settings.effective_host_access_mode == "direct"
        # direct 模式下 bridge 地址不生效（无 token/注册需求）。
        assert settings.effective_host_bridge_url is None

    def test_direct_outside_container_falls_back_to_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import Settings, running_in_container

        monkeypatch.setattr("app.core.config.running_in_container", lambda: False)
        settings = Settings(deployment_profile="self_hosted_team", host_access_mode="direct")
        assert settings.effective_host_access_mode == "off"

    def test_off_mode(self) -> None:
        from app.core.config import Settings

        settings = Settings(
            deployment_profile="self_hosted_team",
            host_access_mode="off",
            host_bridge_url="http://192.168.1.5:34115",
        )
        assert settings.effective_host_access_mode == "off"
        assert settings.effective_host_bridge_url is None

    def test_explicit_bridge_mode_uses_derived_url(self) -> None:
        from app.core.config import Settings

        settings = Settings(deployment_profile="self_hosted_team", host_access_mode="bridge")
        assert settings.effective_host_access_mode == "bridge"
        assert settings.effective_host_bridge_url == "http://host.docker.internal:34115"


class TestSanitizeServiceId:
    def test_path_safe(self) -> None:
        assert sanitize_service_id("GitHub MCP/2") == "github-mcp-2"
        assert sanitize_service_id("ok_id.v1") == "ok_id.v1"
        assert sanitize_service_id("") == "unknown"
        assert sanitize_service_id("  ") == "unknown"


class TestReadBridgeToken:
    def test_reads_token_file(self, tmp_path: Path) -> None:
        token_file = tmp_path / "token"
        token_file.write_text("  abc123\n", encoding="utf-8")
        from app.providers.host_service_resolver import read_bridge_token

        assert read_bridge_token(token_file) == "abc123"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        from app.providers.host_service_resolver import read_bridge_token

        assert read_bridge_token(tmp_path / "nope") is None
        assert read_bridge_token(None) is None
        assert read_bridge_token("") is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        from app.providers.host_service_resolver import read_bridge_token

        token_file = tmp_path / "token"
        token_file.write_text("   \n", encoding="utf-8")
        assert read_bridge_token(token_file) is None


class TestResolveLoopbackUrl:
    def test_mcp_style_rewrite(self) -> None:
        result = resolve_loopback_url(
            service_id="mcp-github",
            base_url="http://127.0.0.1:3001/mcp",
            host_bridge_url=BRIDGE,
            deployment_profile="self_hosted_team",
        )
        assert result == f"{BRIDGE}/services/mcp-github/mcp"

    def test_unchanged_without_bridge(self) -> None:
        url = "http://127.0.0.1:3001/mcp"
        assert (
            resolve_loopback_url(
                service_id="mcp-github",
                base_url=url,
                host_bridge_url=None,
                deployment_profile="self_hosted_team",
            )
            == url
        )
