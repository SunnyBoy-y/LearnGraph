from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_settings, workspace_context
from app.api.routers import migrations
from app.core.config import Settings
from app.core.errors import install_error_handlers
from app.core.security import Principal, hash_password


@pytest.fixture
def api(monkeypatch, tmp_path):
    state = SimpleNamespace(principal=Principal("admin", "admin", "tenant", "session", is_system_admin=True), started=False)
    user = SimpleNamespace(is_system_admin=True, status="active", password_hash=hash_password("Admin-test-password!"))
    settings = Settings(_env_file=None, storage_root=tmp_path / "storage")
    class Controller:
        def status(self): return {"active_database": "sqlite"}
        def preflight(self): return {"ready": True}
        def start(self, **kwargs): state.started = True; return {"state": "QUEUED", **kwargs}
        def commit(self): return {"state": "ACTIVATING"}
        def cancel(self): return {"state": "CANCELLED"}
        def resume(self): return {"state": "CATCHING_UP"}
    monkeypatch.setattr(migrations, "controller", lambda _: Controller())
    app = FastAPI()
    app.include_router(migrations.router, prefix="/api/v1")
    install_error_handlers(app)
    app.dependency_overrides[workspace_context] = lambda: SimpleNamespace(principal=state.principal, workspace_id="ws", require_permission=lambda _: None)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(get=lambda *_: user, commit=lambda: None)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, state


def test_workspace_owner_cannot_access_any_database_control(api):
    client, state = api
    state.principal = Principal("owner", "owner", "tenant", "session", is_system_admin=False)
    for method, path, body in [("GET", "status", None), ("POST", "preflight", None), ("POST", "start", {"confirm": True, "current_password": "Admin-test-password!"}), ("POST", "activate", {"confirm": True, "current_password": "Admin-test-password!"}), ("POST", "cancel", {"confirm": True}), ("POST", "resume", {"confirm": True})]:
        assert client.request(method, f"/api/v1/migrations/database/{path}", json=body).status_code == 403
    assert client.get("/api/v1/migrations/database-configurations").status_code == 403
    assert not state.started


def test_start_requires_explicit_confirmation_and_reauthentication(api):
    client, state = api
    assert client.post("/api/v1/migrations/database/start", json={}).status_code == 422
    assert client.post("/api/v1/migrations/database/start", json={"confirm": True, "current_password": "wrong"}).status_code == 403
    assert not state.started
    response = client.post("/api/v1/migrations/database/start", json={"confirm": True, "current_password": "Admin-test-password!", "managed": True})
    assert response.status_code == 202 and state.started
    assert "password" not in response.text
