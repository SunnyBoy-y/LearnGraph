"""Sandbox bootstrap mode selection (auto / prebuilt / build).

These tests never touch a Docker daemon: the prebuilt rejection paths return
before any worker thread is spawned, and the reference validator is a pure
function.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.domain.schemas.sandbox import SandboxBootstrapStartRequest
from app.services.sandbox_bootstrap import (
    SandboxBootstrapService,
    _prebuilt_image_ref,
)

ACR_REF = "crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph:1.0.0"


class TestPrebuiltImageRefValidation:
    def test_accepts_acr_tagged_reference(self) -> None:
        assert _prebuilt_image_ref(ACR_REF) == ACR_REF

    def test_accepts_digest_reference(self) -> None:
        digest = "sha256:" + "0" * 64
        ref = "crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph@" + digest
        assert _prebuilt_image_ref(ref) == ref

    def test_accepts_plain_local_image_name(self) -> None:
        assert _prebuilt_image_ref("learngraph-sandbox:local") == "learngraph-sandbox:local"

    def test_rejects_whitespace(self) -> None:
        with pytest.raises(ValueError):
            _prebuilt_image_ref("crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph:1.0.0 ")

    def test_rejects_shell_characters(self) -> None:
        with pytest.raises(ValueError):
            _prebuilt_image_ref("learngraph-sandbox:$(rm -rf /)")

    def test_empty_returns_none(self) -> None:
        assert _prebuilt_image_ref("") is None
        assert _prebuilt_image_ref(None) is None


class TestBootstrapStartRequestSchema:
    def test_defaults_to_auto(self) -> None:
        assert SandboxBootstrapStartRequest().mode == "auto"

    def test_accepts_all_modes(self) -> None:
        for mode in ("auto", "prebuilt", "build"):
            assert SandboxBootstrapStartRequest(mode=mode).mode == mode

    def test_rejects_unknown_mode(self) -> None:
        with pytest.raises(ValidationError):
            SandboxBootstrapStartRequest(mode="pull")


class TestBootstrapStartModeSelection:
    def test_prebuilt_mode_rejected_without_configuration(self, monkeypatch) -> None:
        service = SandboxBootstrapService()
        monkeypatch.setattr(service, "_probe_docker", lambda: (True, None))
        # tests/api/conftest.py disables the sandbox by default; the bootstrap
        # gate tests opt back in explicitly.
        settings = Settings(sandbox_enabled=True)  # sandbox_prebuilt_image defaults to None
        result = service.start(settings, actor_id="u-test", mode="prebuilt")
        assert result["accepted"] is False
        assert result["error_code"] == "prebuilt_image_not_configured"
        assert "LEARNGRAPH_SANDBOX_PREBUILT_IMAGE" in result["error_message"]

    def test_build_mode_accepted_without_configuration(self, monkeypatch) -> None:
        service = SandboxBootstrapService()
        monkeypatch.setattr(service, "_probe_docker", lambda: (True, None))
        settings = Settings(sandbox_enabled=True)
        # Do not let the worker thread actually touch Docker.
        monkeypatch.setattr(service, "_run_job", lambda job, s: None)
        result = service.start(settings, actor_id="u-test", mode="build")
        assert result["accepted"] is True
        assert result["joined_existing"] is False
        assert result["job"]["mode"] == "build"

    def test_prebuilt_mode_accepted_when_configured(self, monkeypatch) -> None:
        service = SandboxBootstrapService()
        monkeypatch.setattr(service, "_probe_docker", lambda: (True, None))
        monkeypatch.setattr(service, "_run_job", lambda job, s: None)
        settings = Settings(sandbox_enabled=True, sandbox_prebuilt_image=ACR_REF)
        result = service.start(settings, actor_id="u-test", mode="prebuilt")
        assert result["accepted"] is True
        assert result["job"]["mode"] == "prebuilt"

    def test_auto_mode_uses_prebuilt_when_configured(self, monkeypatch) -> None:
        service = SandboxBootstrapService()
        monkeypatch.setattr(service, "_probe_docker", lambda: (True, None))
        monkeypatch.setattr(service, "_run_job", lambda job, s: None)
        settings = Settings(sandbox_enabled=True, sandbox_prebuilt_image=ACR_REF)
        result = service.start(settings, actor_id="u-test", mode="auto")
        assert result["accepted"] is True
        assert result["job"]["mode"] == "auto"

    def test_invalid_mode_rejected(self, monkeypatch) -> None:
        service = SandboxBootstrapService()
        monkeypatch.setattr(service, "_probe_docker", lambda: (True, None))
        result = service.start(settings := Settings(), actor_id="u-test", mode="nope")
        assert result["accepted"] is False
        assert result["error_code"] == "invalid_bootstrap_mode"

    def test_status_exposes_prebuilt_configuration(self) -> None:
        service = SandboxBootstrapService()
        status = service.status(Settings(sandbox_prebuilt_image=ACR_REF))
        assert status["prebuilt_image_configured"] is True
        assert status["prebuilt_image_ref"] == ACR_REF.split("@")[0]

    def test_status_reports_missing_prebuilt(self) -> None:
        service = SandboxBootstrapService()
        status = service.status(Settings())
        assert status["prebuilt_image_configured"] is False
        assert status["prebuilt_image_ref"] is None
