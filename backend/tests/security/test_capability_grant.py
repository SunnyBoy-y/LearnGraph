from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


ROOT = Path(__file__).resolve().parents[2]


def test_capability_grant_model_exists() -> None:
    models = (ROOT / "app" / "domain" / "models.py").read_text(encoding="utf-8")
    assert "class CapabilityGrant(Base, TimestampMixin, WorkspaceScopedMixin)" in models
    assert '__tablename__ = "capability_grants"' in models
    assert "action: Mapped[str]" in models
    assert "resources: Mapped[dict[str, Any]]" in models
    assert "single_use: Mapped[bool]" in models
    assert "usage_limit: Mapped[int]" in models


def test_capability_grant_service_methods_exist() -> None:
    authz = (ROOT / "app" / "services" / "sandbox_authz.py").read_text(encoding="utf-8")
    assert "def create_capability_grant(" in authz
    assert "def consume_capability_grant(" in authz
    assert "def revoke_capability_grant(" in authz
    assert "CapabilityGrant" in authz