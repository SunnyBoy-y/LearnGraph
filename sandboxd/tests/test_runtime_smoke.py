"""Browser registration must verify Chromium under the production profile."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from sandboxd.runtime.docker import DockerRuntimeBackend
from sandboxd.runtime.port import RuntimeExecResult


@pytest.mark.parametrize("browser_exit", [0, 1])
@pytest.mark.parametrize("runtime_kind", ["python-node", "python-node-browser"])
def test_smoke_resources_browser_check_and_cleanup(monkeypatch, browser_exit, runtime_kind):
    calls = []
    create_options = {}
    cleanup = []
    container = SimpleNamespace(
        start=lambda: None,
        remove=lambda **kwargs: cleanup.append("container"),
    )

    def create(*args, **kwargs):
        create_options.update(kwargs)
        return container

    client = SimpleNamespace(
        images=SimpleNamespace(get=lambda ref: None),
        containers=SimpleNamespace(create=create),
        close=lambda: cleanup.append("client"),
    )
    runtime = DockerRuntimeBackend(
        deployment_id="smoke-test",
        seccomp_dir=str(Path(__file__).resolve().parents[2] / "backend" / "sandbox"),
    )
    monkeypatch.setattr(runtime, "_client", lambda: client)

    def run_exec(client, container, argv, **kwargs):
        calls.append(argv)
        exit_code = browser_exit if "browser-smoke.js" in argv[-1] else 0
        stderr = b"launch arguments " * 100 + b"\nFATAL: browser failed"
        return RuntimeExecResult(exit_code, b"", stderr, False, False, 1)

    monkeypatch.setattr(runtime, "_run_exec", run_exec)
    ok, detail = runtime.smoke_test("sha256:" + "a" * 64, runtime_kind)
    is_browser = runtime_kind == "python-node-browser"
    assert calls[-1] == (("node", "/opt/learngraph/browser-smoke.js") if is_browser else ("node", "--version"))
    assert create_options["mem_limit"] == (2 * 1024**3 if is_browser else 512 * 1024**2)
    assert create_options["memswap_limit"] == create_options["mem_limit"]
    assert create_options["pids_limit"] == (512 if is_browser else 128)
    assert create_options["network_mode"] == "none"
    assert create_options["read_only"] is True
    assert create_options["cap_drop"] == ["ALL"]
    assert ok is (browser_exit == 0 or not is_browser)
    if browser_exit and is_browser:
        assert "FATAL: browser failed" in detail
    assert cleanup == ["container", "client"]
