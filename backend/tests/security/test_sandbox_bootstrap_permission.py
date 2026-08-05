from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routers.sandbox import (
    get_bootstrap_policy,
    start_bootstrap,
    update_bootstrap_policy,
)
from app.core.errors import AppError
from app.domain.schemas.sandbox import SandboxBootstrapPolicyUpdateRequest
from app.services.sandbox_runtime import (
    effective_member_bootstrap_allowed,
    load_bootstrap_policy,
    save_bootstrap_policy,
)


def _context(*, is_system_admin=False, permissions=(), user_id="user-1"):
    return SimpleNamespace(
        principal=SimpleNamespace(
            user_id=user_id, is_system_admin=is_system_admin
        ),
        permissions=frozenset(permissions),
        workspace_id="ws-1",
    )


def _settings(tmp_path, *, member_allowed=True):
    return SimpleNamespace(
        storage_root=tmp_path / "storage",
        sandbox_enabled=True,
        sandbox_image=None,
        sandbox_bootstrap_member_allowed=member_allowed,
    )


def _fake_bootstrap_service(monkeypatch, started: list[dict]) -> None:
    def fake_start(settings, *, actor_id):
        started.append({"actor": actor_id})
        return {
            "accepted": True,
            "joined_existing": False,
            "job": None,
            "status": {},
        }

    monkeypatch.setattr(
        "app.api.routers.sandbox.get_bootstrap_service",
        lambda: SimpleNamespace(start=fake_start),
    )
    monkeypatch.setattr(
        "app.api.routers.sandbox.SandboxBootstrapStartResponse",
        SimpleNamespace(model_validate=lambda result: result),
    )


def test_member_can_bootstrap_by_default(monkeypatch, tmp_path) -> None:
    started: list[dict] = []
    _fake_bootstrap_service(monkeypatch, started)
    response = start_bootstrap(_context(), _settings(tmp_path))
    assert response["accepted"] is True
    assert started == [{"actor": "user-1"}]


def test_member_blocked_when_admin_restricts_bootstrap(tmp_path) -> None:
    save_bootstrap_policy(
        _settings(tmp_path), member_allowed=False, actor_id="admin"
    )
    with pytest.raises(AppError) as excinfo:
        start_bootstrap(_context(), _settings(tmp_path))
    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "sandbox_bootstrap_admin_required"


def test_admin_can_bootstrap_when_restricted(monkeypatch, tmp_path) -> None:
    save_bootstrap_policy(
        _settings(tmp_path), member_allowed=False, actor_id="admin"
    )
    started: list[dict] = []
    _fake_bootstrap_service(monkeypatch, started)
    response = start_bootstrap(
        _context(is_system_admin=True, user_id="admin-1"), _settings(tmp_path)
    )
    assert response["accepted"] is True
    assert started == [{"actor": "admin-1"}]


def test_workspace_manager_can_bootstrap_when_restricted(monkeypatch, tmp_path) -> None:
    save_bootstrap_policy(
        _settings(tmp_path), member_allowed=False, actor_id="admin"
    )
    started: list[dict] = []
    _fake_bootstrap_service(monkeypatch, started)
    response = start_bootstrap(
        _context(permissions=("workspace.manage",), user_id="mgr-1"),
        _settings(tmp_path),
    )
    assert response["accepted"] is True
    assert started == [{"actor": "mgr-1"}]


def test_update_bootstrap_policy_requires_system_admin(tmp_path) -> None:
    with pytest.raises(AppError) as excinfo:
        update_bootstrap_policy(
            SandboxBootstrapPolicyUpdateRequest(member_allowed=False),
            SimpleNamespace(commit=lambda: None),
            _context(),
            _settings(tmp_path),
        )
    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "deployment_admin_required"
    assert load_bootstrap_policy(_settings(tmp_path)) is None


def test_admin_update_bootstrap_policy_persists_and_audits(
    monkeypatch, tmp_path
) -> None:
    recorded: list[dict] = []

    class StubAudit:
        def __init__(self, db, workspace_id):
            pass

        def record(self, **kwargs):
            recorded.append(kwargs)

    monkeypatch.setattr("app.api.routers.sandbox.AuditRepository", StubAudit)
    view = update_bootstrap_policy(
        SandboxBootstrapPolicyUpdateRequest(member_allowed=False),
        SimpleNamespace(commit=lambda: None),
        _context(is_system_admin=True, user_id="admin-1"),
        _settings(tmp_path),
    )
    assert view.member_allowed is False
    assert view.persisted is True
    assert view.updated_by == "admin-1"
    assert load_bootstrap_policy(_settings(tmp_path)).member_allowed is False
    assert recorded[0]["action"] == "sandbox.bootstrap.policy_updated"
    assert recorded[0]["details"] == {"member_allowed": False}


def test_get_bootstrap_policy_reflects_effective_value(tmp_path) -> None:
    view = get_bootstrap_policy(_context(), _settings(tmp_path))
    assert view.member_allowed is True
    assert view.persisted is False
    save_bootstrap_policy(_settings(tmp_path), member_allowed=False, actor_id=None)
    view = get_bootstrap_policy(_context(), _settings(tmp_path))
    assert view.member_allowed is False
    assert view.persisted is True


def test_effective_member_bootstrap_allowed_defaults_to_env_flag(tmp_path) -> None:
    settings = _settings(tmp_path)
    assert load_bootstrap_policy(settings) is None
    assert effective_member_bootstrap_allowed(settings) is True
    restricted = _settings(tmp_path, member_allowed=False)
    assert effective_member_bootstrap_allowed(restricted) is False
    save_bootstrap_policy(settings, member_allowed=False, actor_id="admin")
    # The persisted administrator choice wins over the deployment default.
    assert effective_member_bootstrap_allowed(settings) is False
    assert effective_member_bootstrap_allowed(
        _settings(tmp_path, member_allowed=True)
    ) is False


def test_bootstrap_status_exposes_member_gate(monkeypatch, tmp_path) -> None:
    from app.services.sandbox_bootstrap import SandboxBootstrapService

    service = SandboxBootstrapService()
    monkeypatch.setattr(
        SandboxBootstrapService, "_probe_docker", lambda self: (True, None)
    )
    monkeypatch.setattr(
        "app.services.sandbox_bootstrap.resolve_sandbox_image",
        lambda settings: None,
    )
    monkeypatch.setattr(
        "app.services.sandbox_bootstrap.load_runtime_config",
        lambda settings: None,
    )
    status = service.status(_settings(tmp_path))
    assert status["member_bootstrap_allowed"] is True
    assert status["bootstrap_policy"] is None

    save_bootstrap_policy(
        _settings(tmp_path), member_allowed=False, actor_id="admin"
    )
    status = service.status(_settings(tmp_path))
    assert status["member_bootstrap_allowed"] is False
    assert status["bootstrap_policy"]["member_allowed"] is False
