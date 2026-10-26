from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app.api.deps import workspace_context
from app.api.routers import health, updates
from app.core.config import get_settings
from app.core.errors import install_error_handlers


@pytest.fixture
def api(tmp_path):
    app = FastAPI()
    app.include_router(updates.router, prefix="/api/v1")
    app.include_router(health.router, prefix="/api/v1")
    install_error_handlers(app)
    context = SimpleNamespace(principal=SimpleNamespace(is_system_admin=True))
    settings = SimpleNamespace(update_agent_url="http://host.docker.internal:34116", app_version="v1.0.0",
                               update_agent_token_file=tmp_path / "token", storage_root=tmp_path / "storage")
    settings.update_agent_token_file.write_text("a" * 48)
    app.dependency_overrides[workspace_context] = lambda: context
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, context, settings


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_health_reports_connected_database_instead_of_sqlite_bootstrap(dialect):
    db = SimpleNamespace(execute=lambda _: None, scalar=lambda _: None,
        connection=lambda: SimpleNamespace(dialect=SimpleNamespace(name=dialect)))
    settings = SimpleNamespace(app_version="v1.0.0",database_url="sqlite:////data/learngraph.db")
    assert health.health(db,settings).database == dialect


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/status", None), ("POST", "/check", {}),
    ("POST", "/jobs", {"request_id": "a" * 16, "version": "v1.1.0"}),
    ("POST", "/policy", {"auto_install": True}),
])
def test_all_update_endpoints_require_system_admin(api, method, path, body):
    client, context, _ = api
    context.principal.is_system_admin = False
    with patch.object(updates, "call_agent", new_callable=AsyncMock) as proxy:
        response = client.request(method, "/api/v1/updates" + path, json=body)
    assert response.status_code == 403
    proxy.assert_not_called()


def test_disabled_updater_reports_installation_state(api):
    client, _, settings = api
    settings.update_agent_url = None
    response = client.get("/api/v1/updates/status")
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert client.post("/api/v1/updates/check", json={}).status_code == 503


def test_version_endpoint_is_public_and_independent_of_updater(api):
    client, _, settings = api
    settings.app_version = "v0.5"
    settings.update_agent_url = None
    response = client.get("/api/v1/version")
    assert response.status_code == 200
    assert response.json()["version"] == "v0.5"
    assert response.json()["runtime_mode"] in {"docker", "source"}


@pytest.mark.parametrize("container, mode", [(True, "docker"), (False, "source")])
def test_version_reports_actual_runtime(api, container, mode):
    client, _, _ = api
    with patch.object(health, "running_in_container", return_value=container):
        assert client.get("/api/v1/version").json()["runtime_mode"] == mode


def test_two_component_release_tag_is_forwarded_verbatim(api):
    client, _, _ = api
    with patch.object(updates, "call_agent", new_callable=AsyncMock) as proxy:
        proxy.return_value = {"id": "job", "status": "queued"}
        response = client.post("/api/v1/updates/jobs", json={"request_id": "a" * 16, "version": "v0.5"})
    assert response.status_code == 202
    assert proxy.call_args.args[-1]["version"] == "v0.5"


def test_proxy_keeps_credential_server_side_and_preserves_acceptance(api):
    client, _, _ = api
    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as request:
        request.return_value = httpx.Response(202, json={"id": "job", "status": "queued"})
        response = client.post("/api/v1/updates/jobs", json={"request_id": "a" * 16, "version": "v1.1.0"})
    assert response.status_code == 202
    assert "a" * 48 not in response.text
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer " + "a" * 48
    assert request.call_args.args[1] == "http://host.docker.internal:34116/v1/jobs"


@pytest.mark.parametrize("payload", [
    {"version": "v1.1.0"}, {"version": "v1.1.0", "request_id": "a" * 16, "image": "evil"},
    {"request_id": "a" * 16}, {"request_id": "a" * 16, "version": "../1.0.0"},
    {"request_id": "a" * 16, "version": "v1.1.0", "rollback_id": "b" * 24},
])
def test_invalid_update_requests_never_reach_agent(api, payload):
    client, _, _ = api
    with patch.object(updates, "call_agent", new_callable=AsyncMock) as proxy:
        response = client.post("/api/v1/updates/jobs", json=payload)
    assert response.status_code == 422
    proxy.assert_not_called()


def test_disconnected_agent_is_503_without_leaking_credential(api):
    client, _, _ = api
    with patch.object(httpx.AsyncClient, "request", side_effect=httpx.ConnectError("secret transport detail")):
        response = client.get("/api/v1/updates/status")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "updater_unavailable"
    assert "secret transport detail" not in response.text


def test_missing_agent_token_is_503(api):
    client, _, settings = api
    settings.update_agent_token_file = Path("does-not-exist")
    assert client.get("/api/v1/updates/status").status_code == 503


def test_anonymous_request_is_rejected_before_agent_call(api):
    client, _, _ = api
    client.app.dependency_overrides.pop(workspace_context)
    with patch.object(updates, "call_agent", new_callable=AsyncMock) as proxy:
        response = client.get("/api/v1/updates/status")
    assert response.status_code == 401
    proxy.assert_not_called()


def test_auto_install_policy_requires_a_real_boolean(api):
    client, _, _ = api
    with patch.object(updates, "call_agent", new_callable=AsyncMock) as proxy:
        response = client.post("/api/v1/updates/policy", json={"auto_install": "false"})
    assert response.status_code == 422
    proxy.assert_not_called()


def test_postgresql_keeps_updates_and_auto_install_available(api):
    from app.core.database_routing import atomic_json, state_root
    client, _, settings = api
    atomic_json(state_root(settings) / "active.json", {"phase": "postgresql_active"})
    with patch.object(updates, "call_agent", new_callable=AsyncMock) as proxy:
        proxy.return_value = {"accepted": True}
        assert client.post("/api/v1/updates/jobs", json={"request_id": "a" * 16, "version": "v0.5"}).status_code == 202
        assert client.post("/api/v1/updates/policy", json={"auto_install": True}).status_code == 200
        proxy.return_value = {"auto_install": False}
        assert client.post("/api/v1/updates/policy", json={"auto_install": False}).status_code == 200


def test_pending_activation_still_blocks_updates(api):
    from app.core.database_routing import atomic_json, state_root
    client, _, settings = api
    atomic_json(state_root(settings) / "active.json", {"phase": "activation_pending"})
    with patch.object(updates, "call_agent", new_callable=AsyncMock) as proxy:
        assert client.post("/api/v1/updates/jobs", json={"request_id": "a" * 16, "version": "v0.5"}).status_code == 409
        assert client.post("/api/v1/updates/policy", json={"auto_install": True}).status_code == 409
        proxy.assert_not_called()
