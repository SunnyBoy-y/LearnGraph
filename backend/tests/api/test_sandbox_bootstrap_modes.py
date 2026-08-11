"""Sandbox bootstrap mode selection (auto / prebuilt / build).

These tests never touch a Docker daemon: the prebuilt rejection paths return
before any worker thread is spawned, and the reference validator is a pure
function.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.domain.schemas.sandbox import SandboxBootstrapStartRequest
from app.services.sandbox_bootstrap import (
    BootstrapJob,
    SandboxBootstrapService,
    _prebuilt_image_ref,
)

ACR_REF = "crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph:1.0.0"


class _FakeDockerClient:
    """Minimal docker client double for the local-build fallback path."""

    def __init__(self) -> None:
        self.api = _FakeApi()
        self.images = _FakeImages()

    def close(self) -> None:
        pass


class _FakeApi:
    def build(self, **kwargs: Any):
        return iter([])  # empty build stream


class _FakeImages:
    def get(self, ref: str) -> "_FakeImage":
        return _FakeImage()


class _FakeImage:
    id = "sha256:" + "0" * 64


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
        # gate tests opt back in explicitly. Explicit None also beats a local
        # .env LEARNGRAPH_SANDBOX_PREBUILT_IMAGE (init args > env > dotenv).
        settings = Settings(sandbox_enabled=True, sandbox_prebuilt_image=None)  # sandbox_prebuilt_image defaults to None
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
        status = service.status(Settings(sandbox_prebuilt_image=None))
        assert status["prebuilt_image_configured"] is False
        assert status["prebuilt_image_ref"] is None


class TestAutoModeLocalBuildFallback:
    """Auto mode promises "pull when configured, otherwise build locally".

    A failed prebuilt pull (image not pushed, private registry without login,
    wrong tag) must fall back to the local Docker build instead of stranding
    the deployment uninitialized — even when the settings page persisted
    source mode ``prebuilt``.  Only explicit ``prebuilt`` requests keep
    failing closed.
    """

    @staticmethod
    def _failing_pull(service: SandboxBootstrapService):
        def _pull(job: BootstrapJob, settings: Settings, ref: str) -> None:
            service._fail(job, "prebuilt_pull_failed", "simulated pull failure")
            return None

        return _pull

    def test_auto_mode_falls_back_to_local_build(self, monkeypatch, tmp_path) -> None:
        from app.services import sandbox_bootstrap as sb

        service = SandboxBootstrapService()
        monkeypatch.setattr(service, "_probe_docker", lambda: (True, None))
        monkeypatch.setattr(service, "_pull_prebuilt_image", self._failing_pull(service))
        monkeypatch.setattr(service, "_sandbox_root", lambda: tmp_path)
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        monkeypatch.setattr(service, "_smoke_test", lambda *args, **kwargs: None)
        monkeypatch.setattr(sb, "save_runtime_config", lambda *args, **kwargs: None)
        monkeypatch.setattr(service, "_docker_client", lambda: _FakeDockerClient())
        settings = Settings(sandbox_enabled=True, sandbox_prebuilt_image=ACR_REF)
        job = BootstrapJob(id="u-fallback", actor_id="u-test", mode="auto")
        service._run_job(job, settings)  # noqa: SLF001 - unit-level verification
        assert job.status == "succeeded"
        assert any("[auto-fallback]" in line for line in job.log_lines)
        assert job.image_digest == _FakeImage.id
        assert job.error_code is None

    def test_auto_mode_falls_back_even_when_source_mode_is_prebuilt(
        self, monkeypatch, tmp_path
    ) -> None:
        """One-click init (auto) degrades even with a persisted prebuilt source.

        The settings page may persist source mode ``prebuilt`` (the operator
        prefers the registry image), but a failed pull must still degrade to
        the local build instead of stranding the deployment uninitialized.
        Only an explicit request mode ``prebuilt`` fails closed.
        """
        from app.services import sandbox_bootstrap as sb
        from app.services.sandbox_runtime import save_bootstrap_source

        service = SandboxBootstrapService()
        monkeypatch.setattr(service, "_probe_docker", lambda: (True, None))
        monkeypatch.setattr(service, "_pull_prebuilt_image", self._failing_pull(service))
        monkeypatch.setattr(service, "_sandbox_root", lambda: tmp_path)
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        monkeypatch.setattr(service, "_smoke_test", lambda *args, **kwargs: None)
        monkeypatch.setattr(sb, "save_runtime_config", lambda *args, **kwargs: None)
        monkeypatch.setattr(service, "_docker_client", lambda: _FakeDockerClient())
        settings = Settings(sandbox_enabled=True)
        save_bootstrap_source(
            settings, mode="prebuilt", prebuilt_image=ACR_REF, actor_id="u-test"
        )
        job = BootstrapJob(id="u-fallback-persisted", actor_id="u-test", mode="auto")
        service._run_job(job, settings)  # noqa: SLF001 - unit-level verification
        assert job.status == "succeeded"
        assert any("[auto-fallback]" in line for line in job.log_lines)
        assert job.error_code is None

    def test_explicit_prebuilt_mode_does_not_fallback(self, monkeypatch) -> None:
        service = SandboxBootstrapService()
        monkeypatch.setattr(service, "_probe_docker", lambda: (True, None))
        monkeypatch.setattr(service, "_pull_prebuilt_image", self._failing_pull(service))
        settings = Settings(sandbox_enabled=True, sandbox_prebuilt_image=ACR_REF)
        job = BootstrapJob(id="u-nofallback", actor_id="u-test", mode="prebuilt")
        service._run_job(job, settings)  # noqa: SLF001 - unit-level verification
        assert job.status == "failed"
        assert job.error_code == "prebuilt_pull_failed"
        assert not any("[auto-fallback]" in line for line in job.log_lines)


class TestBootstrapStartHttpContract:
    """The one-click init button posts without a body.

    POST /sandbox/bootstrap with no JSON body must default to mode="auto"
    instead of FastAPI rejecting the request with 422 "Field required".
    """

    def test_post_without_body_is_accepted(self, client, register_user, auth_headers) -> None:
        token, ws, _, _ = register_user()
        resp = client.post(
            "/api/v1/sandbox/bootstrap", headers=auth_headers(token, ws)
        )
        # The test environment disables the sandbox, so the request is parsed
        # and rejected by policy (sandbox_disabled) — never by the schema
        # layer (422). A body-less request must not be required.
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["accepted"] is False
        assert body["error_code"] == "sandbox_disabled"

    def test_post_with_empty_json_body_defaults_to_auto(self, client, register_user, auth_headers) -> None:
        token, ws, _, _ = register_user()
        resp = client.post(
            "/api/v1/sandbox/bootstrap",
            headers=auth_headers(token, ws),
            json={},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["error_code"] == "sandbox_disabled"
