"""Dual-runtime bootstrap and live, per-runtime sandboxd readiness."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.providers.remote.sandbox import BROWSER_RUNTIME_KIND, CODE_RUNTIME_KIND
from app.providers.remote.sandboxd_client import SandboxdUnavailable
from app.services.sandbox_bootstrap import SandboxBootstrapService


DIGEST = "registry.example/runner@sha256:" + "a" * 64
OTHER_DIGEST = "registry.example/runner@sha256:" + "b" * 64


def _record(kind, *, digest=DIGEST, present=True, smoke="passed"):
    return {
        "runtime_kind": kind,
        "image_digest": digest,
        "smoke_status": smoke,
        "image_present": present,
    }


class _Daemon:
    def __init__(self):
        self.records = []
        self.calls = []
        self.fail_kind = None
        self.docker = True

    def get_ready(self):
        code = next((r for r in self.records if r["runtime_kind"] == CODE_RUNTIME_KIND), None)
        return {
            "docker": self.docker,
            "store": True,
            "runtime": bool(code and code.get("image_present", True)),
            "reasons": [],
        }

    def list_runtimes(self):
        return self.records

    def install_runtime(self, kind, source):
        self.calls.append((kind, source))
        if kind == self.fail_kind:
            raise SandboxdUnavailable("browser smoke failed")
        self.records = [r for r in self.records if r["runtime_kind"] != kind]
        result = _record(kind)
        self.records.append(result)
        return result


@pytest.fixture
def bootstrap(monkeypatch, tmp_path):
    service = SandboxBootstrapService()
    daemon = _Daemon()
    monkeypatch.setattr(service, "_sandboxd_client", lambda settings: (daemon, None))
    settings = Settings(
        _env_file=None,
        sandbox_enabled=True,
        sandbox_backend="sandboxd",
        storage_root=tmp_path / "storage",
    )
    return service, daemon, settings


def test_bootstrap_registers_both_kinds_using_the_same_pinned_image(bootstrap):
    service, daemon, settings = bootstrap
    result = service.start(settings, actor_id="test")
    assert daemon.calls == [
        (CODE_RUNTIME_KIND, settings.effective_sandbox_prebuilt_image),
        (BROWSER_RUNTIME_KIND, DIGEST),
    ]
    assert result["job"]["status"] == "succeeded"
    assert result["job"]["image_digest"] == DIGEST
    assert result["job"]["browser_image_digest"] == DIGEST
    assert result["status"]["image_ready"] is True
    assert result["status"]["browser_image_ready"] is True


def test_browser_install_failure_leaves_truthful_partial_status_and_can_retry(bootstrap):
    service, daemon, settings = bootstrap
    daemon.fail_kind = BROWSER_RUNTIME_KIND
    result = service.start(settings, actor_id="test")
    assert result["job"]["status"] == "failed"
    assert result["status"]["image_ready"] is True
    assert result["status"]["browser_image_ready"] is False
    assert result["status"]["browser_image_digest"] is None
    assert result["status"]["phase"] != "ready"
    daemon.fail_kind = None
    assert service.start(settings, actor_id="test")["job"]["status"] == "succeeded"


def test_bootstrap_rejects_unpinned_code_result(bootstrap, monkeypatch):
    service, daemon, settings = bootstrap
    monkeypatch.setattr(daemon, "install_runtime", lambda *args: {"image_digest": "runner:latest"})
    result = service.start(settings, actor_id="test")
    assert result["job"]["status"] == "failed"
    assert result["job"]["error_code"] == "sandboxd_bootstrap_failed"


def test_bootstrap_rejects_browser_digest_drift(bootstrap, monkeypatch):
    service, daemon, settings = bootstrap
    install = daemon.install_runtime

    def drifting_install(kind, source):
        result = install(kind, source)
        if kind == BROWSER_RUNTIME_KIND:
            result["image_digest"] = OTHER_DIGEST
        return result

    monkeypatch.setattr(daemon, "install_runtime", drifting_install)
    assert service.start(settings, actor_id="test")["job"]["status"] == "failed"


@pytest.mark.parametrize("kind", [CODE_RUNTIME_KIND, BROWSER_RUNTIME_KIND])
@pytest.mark.parametrize("failure", ["missing", "deleted", "smoke_failed", "unpinned"])
def test_status_checks_each_runtime_separately(bootstrap, kind, failure):
    service, daemon, settings = bootstrap
    daemon.records = [
        _record(CODE_RUNTIME_KIND),
        _record(BROWSER_RUNTIME_KIND, digest=OTHER_DIGEST),
    ]
    record = next(r for r in daemon.records if r["runtime_kind"] == kind)
    if failure == "missing":
        daemon.records.remove(record)
    elif failure == "deleted":
        record["image_present"] = False
    elif failure == "smoke_failed":
        record["smoke_status"] = "failed"
    else:
        record["image_digest"] = "runner:mutable"
    status = service.status(settings)
    assert status["image_ready"] is (kind != CODE_RUNTIME_KIND)
    assert status["browser_image_ready"] is (kind != BROWSER_RUNTIME_KIND)
    assert status["phase"] != "ready"
    assert status["can_initialize"] is True


def test_status_preserves_distinct_runtime_digests(bootstrap):
    service, daemon, settings = bootstrap
    daemon.records = [
        _record(CODE_RUNTIME_KIND),
        _record(BROWSER_RUNTIME_KIND, digest=OTHER_DIGEST),
    ]
    status = service.status(settings)
    assert status["image_digest"] == DIGEST
    assert status["browser_image_digest"] == OTHER_DIGEST
    assert status["phase"] == "ready"


def test_docker_unavailable_makes_both_runtimes_unready(bootstrap):
    service, daemon, settings = bootstrap
    daemon.records = [_record(CODE_RUNTIME_KIND), _record(BROWSER_RUNTIME_KIND)]
    daemon.docker = False
    status = service.status(settings)
    assert not status["image_ready"] and not status["browser_image_ready"]


@pytest.mark.parametrize("present", [True, False])
def test_backend_readiness_honors_live_image_presence(bootstrap, present):
    from app.providers.remote.sandboxd_backend import SandboxdBackend

    _, daemon, settings = bootstrap
    daemon.records = [_record(BROWSER_RUNTIME_KIND, present=present)]
    backend = SandboxdBackend(settings, BROWSER_RUNTIME_KIND, client=daemon)
    assert backend.runtime_image_pinned(BROWSER_RUNTIME_KIND) is present


@pytest.mark.parametrize("shared_image", [True, False])
def test_older_daemon_only_proves_presence_for_its_health_checked_digest(bootstrap, shared_image):
    service, daemon, settings = bootstrap
    daemon.records = [
        _record(CODE_RUNTIME_KIND),
        _record(BROWSER_RUNTIME_KIND, digest=DIGEST if shared_image else OTHER_DIGEST),
    ]
    for record in daemon.records:
        record.pop("image_present")
    status = service.status(settings)
    assert status["image_ready"] is True
    assert status["browser_image_ready"] is shared_image
