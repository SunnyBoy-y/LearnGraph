from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.models import Base, ProviderConfig
from app.providers.factory import (
    _secret_for_provider,
    fetch_provider_for_workspace,
    search_provider_for_workspace,
)
from app.providers.remote.fetch import FirecrawlFetchProvider, UnavailableFetchProvider
from app.providers.remote.search import SearXNGSearchProvider, UnavailableSearchProvider


@pytest.fixture
def scratch_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="lg-bridge-factory-"))


def _engine(scratch_dir: Path):
    engine = create_engine(f"sqlite:///{scratch_dir / 'providers.db'}")
    Base.metadata.create_all(engine)
    return engine


def _firecrawl_provider() -> ProviderConfig:
    return ProviderConfig(
        display_name="Firecrawl",
        provider_type="firecrawl_fetch",
        base_url="https://api.firecrawl.dev",
        workspace_id="workspace-a",
        enabled=True,
        remote_capability=True,
    )


def _searxng_provider() -> ProviderConfig:
    return ProviderConfig(
        display_name="SearXNG",
        provider_type="searxng",
        base_url="https://search.example.com",
        workspace_id="workspace-a",
        enabled=True,
        remote_capability=True,
    )


def _resolved(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _settings(**overrides) -> Settings:
    defaults = {"secret_provider": "environment", "allow_private_bridge_urls": False}
    defaults.update(overrides)
    return Settings(**defaults)


def test_fetch_provider_degrades_when_bridge_dns_is_unsafe(monkeypatch, scratch_dir) -> None:
    # A proxy "fake-ip" DNS answer (private address for a public host) must make
    # the fetch provider unavailable, never crash the calling endpoint.
    monkeypatch.setattr("app.providers.factory._secret_for_provider", lambda *a, **k: "sk-test")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _resolved("10.0.0.2"))
    engine = _engine(scratch_dir)
    with Session(engine) as db:
        db.add(_firecrawl_provider())
        db.commit()
        provider = fetch_provider_for_workspace(db, "workspace-a", _settings())
    assert isinstance(provider, UnavailableFetchProvider)
    assert "blocked" in provider.reason or "unavailable" in provider.reason


def test_fetch_provider_allow_private_flag_constructs(monkeypatch, scratch_dir) -> None:
    monkeypatch.setattr("app.providers.factory._secret_for_provider", lambda *a, **k: "sk-test")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _resolved("10.0.0.2"))
    engine = _engine(scratch_dir)
    with Session(engine) as db:
        db.add(_firecrawl_provider())
        db.commit()
        provider = fetch_provider_for_workspace(
            db, "workspace-a", _settings(allow_private_bridge_urls=True)
        )
    assert isinstance(provider, FirecrawlFetchProvider)
    assert provider.base_url == "https://api.firecrawl.dev"


def test_search_provider_degrades_when_bridge_dns_is_unsafe(monkeypatch, scratch_dir) -> None:
    monkeypatch.setattr("app.providers.factory._secret_for_provider", lambda *a, **k: "sk-test")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _resolved("10.0.0.2"))
    engine = _engine(scratch_dir)
    with Session(engine) as db:
        db.add(_searxng_provider())
        db.commit()
        provider = search_provider_for_workspace(db, "workspace-a", _settings())
    assert isinstance(provider, UnavailableSearchProvider)
    assert "unavailable" in provider.reason


def test_search_provider_allow_private_flag_constructs(monkeypatch, scratch_dir) -> None:
    monkeypatch.setattr("app.providers.factory._secret_for_provider", lambda *a, **k: "sk-test")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _resolved("10.0.0.2"))
    engine = _engine(scratch_dir)
    with Session(engine) as db:
        db.add(_searxng_provider())
        db.commit()
        provider = search_provider_for_workspace(
            db, "workspace-a", _settings(allow_private_bridge_urls=True)
        )
    assert isinstance(provider, SearXNGSearchProvider)
    assert provider.base_url == "https://search.example.com"
