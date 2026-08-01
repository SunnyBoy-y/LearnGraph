from __future__ import annotations

from pathlib import Path

from app.core.config import DEPLOYMENT_PROFILES, Settings


ROOT = Path(__file__).resolve().parents[2]


def test_deployment_profile_declared_and_validated() -> None:
    source = (ROOT / "app" / "core" / "config.py").read_text(encoding="utf-8")
    assert "DEPLOYMENT_PROFILES" in source
    assert "personal_desktop" in source
    assert "self_hosted_team" in source
    assert "cloud_saas" in source
    assert "deployment_profile" in source
    assert "profile_listen_host" in source
    assert "profile_validate" in source


def test_personal_desktop_listens_on_loopback_by_default() -> None:
    s = Settings(deployment_profile="personal_desktop")
    assert s.profile_listen_host == "127.0.0.1"


def test_self_hosted_team_listens_on_all_addresses() -> None:
    s = Settings(deployment_profile="self_hosted_team")
    assert s.profile_listen_host == "0.0.0.0"


def test_cloud_saas_listens_on_all_addresses() -> None:
    s = Settings(deployment_profile="cloud_saas")
    assert s.profile_listen_host == "0.0.0.0"


def test_unknown_profile_is_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="Deployment profile"):
        Settings(deployment_profile="unknown_profile")


def test_main_app_checks_profile_at_startup() -> None:
    main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "profile_validate()" in main_py
    assert "Deployment profile conflicts" in main_py