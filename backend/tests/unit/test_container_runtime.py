from unittest.mock import MagicMock, Mock
import signal
import subprocess
import sys

import pytest

from app import container_runtime as runtime


@pytest.mark.parametrize("read_only,expected", [(False, "false"), (True, "true")])
def test_child_database_mode_is_process_local(monkeypatch, read_only, expected):
    monkeypatch.setenv("LEARNGRAPH_SQLITE_READ_ONLY", "inherited")
    spawn = Mock()
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    runtime.start("app.preview:preview_app", 8001, read_only=read_only)
    assert spawn.call_args.args[0][:3] == [sys.executable, "-m", "uvicorn"]
    assert spawn.call_args.kwargs["env"]["LEARNGRAPH_SQLITE_READ_ONLY"] == expected
    assert runtime.os.environ["LEARNGRAPH_SQLITE_READ_ONLY"] == "inherited"


@pytest.mark.parametrize("failed", [runtime.API_HEALTH, runtime.PREVIEW_HEALTH, None])
def test_health_requires_both_origins(monkeypatch, failed):
    monkeypatch.setattr(sys, "argv", ["container_runtime", "health"])
    monkeypatch.setattr(runtime, "healthy", lambda url: url != failed)
    assert runtime.main() == (0 if failed is None else 1)


def test_probe_ignores_environment_proxies(monkeypatch):
    proxy = Mock()
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.status = 200
    monkeypatch.setattr(runtime.urllib.request, "ProxyHandler", proxy)
    monkeypatch.setattr(runtime.urllib.request, "build_opener", Mock(return_value=opener))
    assert runtime.healthy(runtime.API_HEALTH)
    proxy.assert_called_once_with({})


def test_probe_failure_is_unhealthy(monkeypatch):
    opener = Mock()
    opener.open.side_effect = OSError("offline")
    monkeypatch.setattr(runtime.urllib.request, "build_opener", Mock(return_value=opener))
    assert not runtime.healthy(runtime.PREVIEW_HEALTH)


def child(*polls):
    process = Mock()
    process.poll.side_effect = polls
    return process


def test_api_failure_does_not_start_preview(monkeypatch):
    api = child(1)
    spawn, cleanup = Mock(return_value=api), Mock()
    monkeypatch.setattr(runtime, "start", spawn)
    monkeypatch.setattr(runtime, "stop", cleanup)
    assert runtime.serve() == 1
    spawn.assert_called_once_with("app.main:app", 8000)
    cleanup.assert_called_once_with([api])


def test_api_timeout_does_not_start_preview(monkeypatch):
    api = child(None)
    spawn, cleanup = Mock(return_value=api), Mock()
    monkeypatch.setenv("LEARNGRAPH_CONTAINER_STARTUP_TIMEOUT", "0")
    monkeypatch.setattr(runtime, "start", spawn)
    monkeypatch.setattr(runtime, "healthy", lambda _: False)
    monkeypatch.setattr(runtime, "stop", cleanup)
    assert runtime.serve() == 1
    assert spawn.call_count == 1
    cleanup.assert_called_once_with([api])


def test_preview_failure_stops_api(monkeypatch):
    api, preview = child(None, None), child(1)
    spawn, cleanup = Mock(side_effect=[api, preview]), Mock()
    monkeypatch.setattr(runtime, "start", spawn)
    monkeypatch.setattr(runtime, "healthy", lambda _: True)
    monkeypatch.setattr(runtime, "stop", cleanup)
    assert runtime.serve() == 1
    assert spawn.call_args_list[1].kwargs == {"read_only": True}
    cleanup.assert_called_once_with([api, preview])


def test_sigterm_stops_both_children_cleanly(monkeypatch):
    api, preview = child(None, None), child(None)
    handlers = {}
    cleanup = Mock()
    monkeypatch.setattr(runtime, "start", Mock(side_effect=[api, preview]))
    monkeypatch.setattr(runtime, "healthy", lambda _: True)
    monkeypatch.setattr(runtime, "stop", cleanup)
    monkeypatch.setattr(runtime.signal, "signal", lambda sig, handler: handlers.setdefault(sig, handler))
    monkeypatch.setattr(runtime.time, "sleep", lambda _: handlers[signal.SIGTERM](signal.SIGTERM, None))
    assert runtime.serve() == 0
    cleanup.assert_called_once_with([api, preview])


def test_stop_terminates_both_before_waiting_and_kills_stuck_child():
    api, preview = child(None), child(None)
    api.wait.side_effect = [subprocess.TimeoutExpired("api", 0), 0]
    preview.wait.side_effect = lambda **_: api.terminate.assert_called_once()
    runtime.stop([api, preview], timeout=0)
    api.terminate.assert_called_once()
    preview.terminate.assert_called_once()
    api.kill.assert_called_once()
    preview.kill.assert_not_called()
