"""Retired research surfaces stay unavailable while ordinary search still works."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.domain.models import ProviderConfig, utc_now
from app.domain.schemas.management import FunctionalModelDefaultsSettingValue
from app.domain.schemas.search import SearchResult
from app.providers.catalog import PROVIDER_IMPORT_TARGETS, provider_catalog
from app.services.agent_runtime import AgentToolRuntime
from app.services.management import ProviderService


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/research"), ("POST", "/research"),
    ("POST", "/research/plan"), ("GET", "/research/old-job"),
    ("GET", "/research/old-job/events"),
    ("POST", "/research/old-job/approve"),
    ("POST", "/research/old-job/cancel"),
])
def test_research_routes_removed(client, method, path):
    assert client.request(method, f"/api/v1{path}", json={}).status_code == 404
    assert not any("/research" in path for path in client.app.openapi()["paths"])


def test_research_providers_and_model_routing_removed():
    assert not any(item["role"] == "deep_research" for item in provider_catalog())
    assert not any("deep_research" in source or any("deep_research" in target for target, _ in targets)
                   for source, targets in PROVIDER_IMPORT_TARGETS.items())
    assert "deep_research" not in FunctionalModelDefaultsSettingValue.model_fields


def test_historic_research_provider_hidden_and_cannot_be_created(client, register_user, auth_headers):
    token, workspace, *_ = register_user("retired")
    with SessionLocal() as db:
        retired_id = str(uuid4())
        db.add(ProviderConfig(id=retired_id, workspace_id=workspace, display_name="retired",
                              provider_type="qwen_deep_research", capabilities={}, enabled=True,
                              remote_capability=True))
        db.commit()
        service = ProviderService(db, workspace, "test", get_settings())
        assert retired_id not in {item.id for item in service.list()}
    response = client.post("/api/v1/providers", headers=auth_headers(token, workspace),
                           json={"display_name": "retired", "provider_type": "qwen_deep_research"})
    assert response.status_code == 422


def test_agent_research_tools_removed(client, register_user):
    _, workspace, *_ = register_user("tools")
    with SessionLocal() as db:
        runtime = AgentToolRuntime(workspace_id=workspace, actor_id="test", search_provider=None,
                                   extensions=SimpleNamespace(db=db), sandbox=None, sandbox_authorized=False)
        names = {item["function"]["name"] for item in runtime._model_invocation_tool_definitions()}
        assert {"transcribe_audio", "analyze_image"} <= names
        assert not {"start_deep_research", "get_deep_research"} & names


def test_search_provider_still_rejects_model_discovery(client, register_user, monkeypatch):
    _, workspace, *_ = register_user("discovery")
    with SessionLocal() as db:
        provider_id = str(uuid4())
        db.add(ProviderConfig(id=provider_id, workspace_id=workspace, display_name="search",
                              provider_type="tavily", capabilities={}, enabled=True,
                              remote_capability=True))
        db.commit()
        service = ProviderService(db, workspace, "test", get_settings())
        monkeypatch.setattr(service, "_discover", lambda _: pytest.fail("Unexpected model discovery"))
        with pytest.raises(AppError) as error:
            service.models(provider_id)
        assert error.value.code == "provider_has_no_models"


def test_search_endpoint_retains_billing_and_results(client, register_user, auth_headers, monkeypatch):
    from app.api.routers import search
    from app.services.billing import BillingService

    token, workspace, *_ = register_user("search")
    provider = SimpleNamespace(provider_id="stub-search", remote_capability=True)
    calls = []

    def execute_search(self, actual_provider, query, max_results, *, allowed_domains):
        calls.append((actual_provider, query, max_results, allowed_domains))
        return [SearchResult(title="Search result", url="https://example.com", snippet="Result",
                             source_type="web", fetched_at=utc_now())], None

    monkeypatch.setattr(search, "search_provider_for_workspace", lambda *args, **kwargs: provider)
    monkeypatch.setattr(BillingService, "execute_search", execute_search)
    response = client.post("/api/v1/search", headers=auth_headers(token, workspace),
                           json={"query": "ordinary search", "max_results": 3})
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["title"] == "Search result"
    assert calls == [(provider, "ordinary search", 3, None)]
