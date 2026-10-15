"""Shared fixtures for sandboxd unit tests (fake runtime, no Docker)."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from sandboxd.config import SandboxdConfig  # noqa: E402
from sandboxd.store import SandboxdStore  # noqa: E402

from tests.fake_runtime import FakeRuntime  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    """Pin pytest's temp base to the sandboxd tests dir.

    Windows can fail scanning the default per-user pytest-of-<user> basetemp
    (PermissionError WinError 5); pinning keeps local and CI behavior
    identical to the backend suite.
    """
    import tempfile

    base = pathlib.Path(__file__).resolve().parent / ".pytest-tmp"
    base.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(base)
    tempfile.tempdir = str(base)

RUNTIME_IMAGE = "sha256:" + "a" * 64


def make_config(tmp_path, **overrides) -> SandboxdConfig:
    defaults = dict(
        listen_host="127.0.0.1",
        port=8090,
        token="test-token",
        admin_token=None,
        state_path=str(tmp_path / "state.db"),
        deployment_id="deploy-test",
        runtime_backend="docker",
        docker_host=None,
        runtime_image=RUNTIME_IMAGE,
        egress_network_enabled=True,
        egress_proxy_url="http://egress-proxy:8888",
        egress_proxy_container="egress-proxy",
        max_request_bytes=16 * 1024 * 1024,
        max_file_bytes=8 * 1024 * 1024,
        max_stdout_bytes=256 * 1024,
        max_active=4,
        workspace_uid="65532:65532",
        reconcile_on_start=False,
        reconcile_grace_seconds=0,
        ttl_sweep_interval_seconds=60,
        network_label="com.learngraph.sandbox",
        seccomp_dir="",
    )
    defaults.update(overrides)
    return SandboxdConfig(**defaults)


@pytest.fixture
def config(tmp_path):
    return make_config(tmp_path)


@pytest.fixture
def store(config):
    instance = SandboxdStore(config.state_path)
    instance.init()
    return instance


@pytest.fixture
def runtime():
    return FakeRuntime()
