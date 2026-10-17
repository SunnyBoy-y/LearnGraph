from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import current_principal, get_db, get_settings
from app.api.routers import migrations
from app.core.config import Settings
from app.core.errors import install_error_handlers
from app.core.security import Principal, hash_password


@pytest.fixture
def api(monkeypatch, tmp_path):
    user = SimpleNamespace(is_system_admin=True, status="active", password_hash=hash_password("Current-admin-password!"))
    principal = Principal(str(uuid4()), "admin", "tenant", "session", is_system_admin=True)
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'site.db').as_posix()}")
    state = SimpleNamespace(principal=principal, exported=False, staged=False)
    class Service:
        def __init__(self, *_): pass
        def export(self, password):
            assert password == "Separate-backup-password!"
            state.exported = True
            return b"encrypted-fixture-zip"
        def stage_restore(self, payload, password):
            assert password == "Separate-backup-password!"
            state.staged = True
            return dict(job_id="job", source_workspace_id=None, tables=2, records=4, files=1, memory_files=1, mode="staged", restore_path="test.zip", restart_required=True)
    monkeypatch.setattr(migrations, "SiteBackupService", Service)
    app = FastAPI()
    app.include_router(migrations.router, prefix="/api/v1")
    install_error_handlers(app)
    app.dependency_overrides[current_principal] = lambda: state.principal
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(get=lambda *_: user)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, state


def test_admin_export_uses_password_post_and_no_store(api):
    client, state = api
    assert client.get("/api/v1/migrations/backup").status_code == 405
    assert client.post("/api/v1/migrations/backup", json={}).status_code == 422
    wrong = client.post("/api/v1/migrations/backup", json={"current_password": "wrong", "backup_password": "Separate-backup-password!"})
    assert wrong.status_code == 403 and not state.exported
    result = client.post("/api/v1/migrations/backup", json={"current_password": "Current-admin-password!", "backup_password": "Separate-backup-password!"})
    assert result.status_code == 200 and state.exported
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["content-type"] == "application/zip"


def test_workspace_owner_cannot_export_or_restore_site(api):
    client, state = api
    state.principal = Principal(str(uuid4()), "owner", "tenant", "session", is_system_admin=False)
    assert client.get("/api/v1/migrations/backup").status_code == 403
    assert client.post("/api/v1/migrations/backup", json={"current_password": "Current-admin-password!", "backup_password": "Separate-backup-password!"}).status_code == 403
    response = client.post("/api/v1/migrations/restore", files={"backup": ("site.zip", b"zip", "application/zip")}, data={"confirm": "true", "current_password": "Current-admin-password!", "backup_password": "Separate-backup-password!"})
    assert response.status_code == 403
    assert not state.exported and not state.staged


def test_restore_requires_password_and_confirmation_and_returns_staged(api):
    client, state = api
    files = {"backup": ("site.zip", b"zip", "application/zip")}
    data = {"current_password": "Current-admin-password!", "backup_password": "Separate-backup-password!"}
    assert client.post("/api/v1/migrations/restore", files=files, data=data).status_code == 400
    result = client.post("/api/v1/migrations/restore", files=files, data={**data, "confirm": "true"})
    assert result.status_code == 200 and state.staged
    assert result.json()["mode"] == "staged"
