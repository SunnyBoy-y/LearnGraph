import urllib.parse

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.services.mcp_market import McpMarketService, public_url


@pytest.fixture
def market():
    return McpMarketService(None, "test-ws", "test-user", Settings(_env_file=None))


def official(*, remote=False):
    return {"server": {"name": "io.example/filesystem", "version": "1.2.3", "description": "Read files",
                       "packages": [{"registryType": "npm", "identifier": "@example/filesystem", "version": "1.2.3",
                                     "environmentVariables": [{"name": "ROOT", "description": "Root directory", "isRequired": True}]}],
                       "remotes": [{"type": "streamable-http", "url": "https://example.com/mcp"}] if remote else []},
            "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active", "publishedAt": "2026-10-15T00:00:00Z"}}}


def test_official_browse_preserves_query_and_cursor(market, monkeypatch):
    urls = []
    def fetch(url):
        urls.append(url)
        return {"servers": [official()], "metadata": {"nextCursor": "x" * 400}}
    monkeypatch.setattr(market, "_get_json", fetch)
    result = market.browse(query="x", cursor="opaque:page", limit=2)
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query) == {
        "limit": ["2"], "version": ["latest"], "search": ["x"], "cursor": ["opaque:page"]}
    assert result.next_cursor == "x" * 400
    assert result.items[0].catalog_url.endswith("io.example%2Ffilesystem")


def test_local_package_details_are_available_without_connecting(market, monkeypatch):
    urls = []
    def fetch(url):
        urls.append(url)
        return official()
    monkeypatch.setattr(market, "_get_json", fetch)
    result = market.detail("io.example/filesystem", version="1.2.3")
    assert urls == ["https://registry.modelcontextprotocol.io/v0.1/servers/io.example%2Ffilesystem/versions/1.2.3"]
    assert not result.item.supported
    assert result.package_details[0]["identifier"] == "@example/filesystem"
    assert result.configuration[0]["name"] == "ROOT" and result.configuration[0]["isRequired"]
    assert result.tools == [] and result.raw == official()


def test_smithery_search_and_pagination(market, monkeypatch):
    urls = []
    def fetch(url):
        urls.append(url)
        return {"servers": [{"qualifiedName": "brave", "displayName": "Brave Search"}],
                "pagination": {"currentPage": 2, "totalPages": 4}}
    monkeypatch.setattr(market, "_get_json", fetch)
    result = market.browse(catalog="smithery", query="web search", cursor="2", limit=8)
    assert result.next_cursor == "3" and result.items[0].catalog == "smithery"
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(urls[0]).query)["q"] == ["web search"]


@pytest.mark.parametrize("required,supported", [(True, False), (False, True)])
def test_smithery_details_expose_tools_and_configuration(market, monkeypatch, required, supported):
    raw = {"qualifiedName": "brave", "description": "Web search", "tools": [{"name": "search", "inputSchema": {"type": "object"}}],
           "connections": [{"type": "http", "deploymentUrl": "https://brave.run.tools",
                            "configSchema": {"required": ["apiKey"] if required else [], "properties": {"apiKey": {"description": "API key"}}}}]}
    monkeypatch.setattr(market, "_get_json", lambda _: raw)
    result = market.detail("brave", catalog="smithery")
    assert result.item.supported is supported and result.item.endpoint_url == "https://brave.run.tools"
    assert result.tools[0]["name"] == "search" and result.configuration[0]["isRequired"] is required


@pytest.mark.parametrize("name", ["../bad", "a/../b", "https://example.com", "a//b"])
def test_reject_invalid_detail_names(market, monkeypatch, name):
    monkeypatch.setattr(market, "_get_json", lambda _: pytest.fail("must not fetch invalid names"))
    with pytest.raises(AppError):
        market.detail(name)


def test_disabled_sources_and_wrong_details_are_explicit(market, monkeypatch):
    market.settings.mcp_smithery_enabled = False
    with pytest.raises(AppError) as error:
        market.browse(catalog="smithery")
    assert error.value.code == "catalog_disabled"
    monkeypatch.setattr(market, "_get_json", lambda _: official())
    with pytest.raises(AppError) as error:
        market.detail("other/server")
    assert error.value.code == "catalog_invalid_response"


def test_reject_unsafe_source_links():
    assert public_url("javascript:alert(1)") == ""
    assert public_url("https://user:secret@example.com") == ""
    assert public_url("https://example.com/mcp") == "https://example.com/mcp"
