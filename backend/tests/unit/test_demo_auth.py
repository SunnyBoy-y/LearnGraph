"""Demo opt-in, privilege migration, and credential-free HTTP contract."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routers import auth, deployment
from app.core import seed
from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.core.errors import install_error_handlers
from app.domain.models import User


@pytest.fixture
def demo_app(monkeypatch):
    monkeypatch.delenv("LEARNGRAPH_ENABLE_DEMO_LOGIN", raising=False)
    settings = Settings(
        _env_file=None,
        env="development",
        bootstrap_admin_password="SetupPass123",
        demo_username="configured-preview",
        demo_password="ConfiguredPreview123",
    )
    monkeypatch.setattr(seed, "get_settings", lambda: settings)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed.ensure_auth_identities(db)
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(auth.router, prefix="/api/v1")
        app.include_router(deployment.router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app) as client:
            yield settings, db, client
    engine.dispose()


def enable_demo(settings, db):
    settings.enable_demo_login = True
    seed.ensure_auth_identities(db)


def test_fresh_install_disables_both_login_paths(demo_app):
    settings, db, client = demo_app
    demo = db.get(User, "demo-user")
    assert demo.status == "disabled"
    assert not demo.is_system_admin
    assert not settings.demo_login_enabled
    assert client.get("/api/v1/deployment/profile").json()["demo_login_enabled"] is False
    assert client.post("/api/v1/auth/demo-login", json={}).status_code == 404
    assert client.post("/api/v1/auth/login", json={
        "username": settings.demo_username, "password": settings.demo_password,
    }).status_code == 401


def test_explicit_env_opt_in_and_production_gate(monkeypatch):
    monkeypatch.setenv("LEARNGRAPH_ENABLE_DEMO_LOGIN", "true")
    assert Settings(_env_file=None, env="development").demo_login_enabled
    assert not Settings(_env_file=None, env="production").demo_login_enabled


def test_enabled_demo_uses_server_credentials_and_cannot_administer(demo_app):
    settings, db, client = demo_app
    enable_demo(settings, db)
    response = client.post("/api/v1/auth/demo-login", json={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["username"] == settings.demo_username
    assert "password" not in result
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=headers).json()["is_system_admin"] is False
    assert client.get("/api/v1/users", headers=headers).status_code == 403
    # Older clients may still send stale fields; none influence the identity.
    stale = client.post("/api/v1/auth/demo-login", json={"username": "stale", "password": "stale"})
    assert stale.status_code == 200
    assert stale.json()["username"] == settings.demo_username


def test_upgrade_demotes_only_builtin_demo(demo_app):
    settings, db, client = demo_app
    enable_demo(settings, db)
    db.get(User, "demo-user").is_system_admin = True
    db.commit()
    seed.ensure_auth_identities(db)
    assert not db.get(User, "demo-user").is_system_admin
    assert db.get(User, "bootstrap-admin").is_system_admin


def test_disable_revokes_sessions_even_after_reenable(demo_app):
    settings, db, client = demo_app
    enable_demo(settings, db)
    result = client.post("/api/v1/auth/demo-login", json={}).json()
    headers = {"Authorization": f"Bearer {result['access_token']}"}
    settings.enable_demo_login = False
    seed.ensure_auth_identities(db)
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    enable_demo(settings, db)
    assert db.get(User, "demo-user").status == "active"
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/demo-login", json={}).status_code == 200


def test_web_admin_cannot_enable_demo(demo_app):
    settings, db, client = demo_app
    result = client.post("/api/v1/auth/login", json={
        "username": settings.bootstrap_admin_username,
        "password": settings.bootstrap_admin_password,
    }).json()
    response = client.patch("/api/v1/users/demo-user/status", json={"status": "active"},
        headers={"Authorization": f"Bearer {result['access_token']}"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "demo_status_managed_by_environment"
    assert db.get(User, "demo-user").status == "disabled"


def test_regular_login_cannot_bypass_env_gate(demo_app):
    settings, db, client = demo_app
    db.get(User, "demo-user").status = "active"
    db.commit()
    response = client.post("/api/v1/auth/login", json={
        "username": settings.demo_username, "password": settings.demo_password,
    })
    assert response.status_code == 401


def test_deleted_demo_is_not_restored(demo_app):
    settings, db, client = demo_app
    db.get(User, "demo-user").status = "deleted"
    db.commit()
    enable_demo(settings, db)
    assert db.get(User, "demo-user").status == "deleted"
    assert client.post("/api/v1/auth/demo-login", json={}).status_code == 401
