import json
import threading
from http.server import ThreadingHTTPServer

import httpx

from app.core.database_routing import OperationLease, atomic_json
from app.migration_agent import Provisioner, handler
from app.services.database_migration import DatabaseMigrationController
from app.core.errors import AppError
from types import SimpleNamespace
from unittest.mock import Mock
import pytest


def test_agent_has_no_generic_docker_or_command_endpoint(tmp_path, monkeypatch):
    provisioner = Provisioner(tmp_path / "jobs", tmp_path / "token")
    invoked = []
    monkeypatch.setattr(provisioner, "start", lambda job: invoked.append(job) or {"accepted": True})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(provisioner))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            job = "a" * 32
            assert client.post("/v1/postgres/" + job).status_code == 403
            client.headers["Authorization"] = "Bearer " + provisioner.token
            assert client.post("/v1/postgres/" + job, json={"image": "arbitrary"}).status_code == 400
            assert client.post("/v1/postgres/../../containers/create").status_code == 404
            assert client.post("/v1/exec").status_code == 404
            assert client.post("/v1/postgres/" + job).status_code == 202
            assert invoked == [job]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_agent_never_returns_stored_password_material_in_progress(tmp_path):
    provisioner = Provisioner(tmp_path / "jobs", tmp_path / "token")
    job = "b" * 32
    atomic_json(provisioner._path(job), {"state": "starting", "admin_secret": "admin-secret", "app_secret": "app-secret", "database_secret": "database-secret"})
    assert "secret" not in json.dumps(provisioner.status(job))


def test_only_one_executor_owns_the_deployment(tmp_path):
    import pytest
    first = OperationLease(tmp_path)
    try:
        with pytest.raises(BlockingIOError):
            OperationLease(tmp_path)
    finally:
        first.close()
    second = OperationLease(tmp_path)
    second.close()


@pytest.mark.parametrize("token_present", [False, True])
def test_stopped_optional_agent_has_actionable_error(tmp_path, monkeypatch, token_present):
    token = tmp_path / "agent-token"
    if token_present:
        token.write_text("a" * 48)
    controller = object.__new__(DatabaseMigrationController)
    controller.settings = SimpleNamespace(
        database_migration_agent_url="http://database-migration-agent:8092",
        database_migration_agent_token_file=str(token),
    )
    client = Mock()
    client.get.side_effect = httpx.ConnectError("stopped")
    monkeypatch.setattr(httpx, "Client", lambda **_: Mock(
        __enter__=Mock(return_value=client), __exit__=Mock(return_value=False),
    ))
    with pytest.raises(AppError) as error:
        controller._agent_connection()
    assert error.value.code == "database_agent_unavailable"
    assert "up -d --no-deps database-migration-agent" in error.value.message


@pytest.mark.parametrize("outcome", ["ready", "failed", "cancelled"])
def test_automatic_agent_is_stopped_for_every_provisioning_outcome(tmp_path, monkeypatch, outcome):
    controller = object.__new__(DatabaseMigrationController)
    controller.root = tmp_path
    controller._lock = threading.RLock()
    controller._cancel = threading.Event()
    job = {"id": "c" * 32, "state": "BACKUP_VERIFIED", "agent_auto_start": True}
    atomic_json(controller.state_path, job)
    controls = []
    monkeypatch.setattr(controller, "_control_agent", lambda op, job_id: controls.append(op))
    monkeypatch.setattr(controller, "_agent_connection", lambda: ("http://agent", {}))
    monkeypatch.setattr(controller, "_ensure_not_cancelled", lambda: None)
    def provision(*_, **__):
        if outcome == "failed":
            raise RuntimeError("Provisioning failed")
        if outcome == "cancelled":
            raise InterruptedError("Cancelled")
        return "postgresql://target"
    monkeypatch.setattr(controller, "_provision_managed_target", provision)
    if outcome == "ready":
        assert controller._managed_target(job) == "postgresql://target"
    else:
        with pytest.raises(RuntimeError if outcome == "failed" else InterruptedError):
            controller._managed_target(job)
    assert controls == ["start", "stop"]
    assert json.loads(controller.state_path.read_text())["agent_state"] == "stopped"


def test_cleanup_failure_is_visible_and_leaves_lease_fallback(tmp_path, monkeypatch):
    controller = object.__new__(DatabaseMigrationController)
    controller.root, controller._lock = tmp_path, threading.RLock()
    atomic_json(controller.state_path, {"id": "d" * 32, "state": "TARGET_RUNNING"})
    monkeypatch.setattr(controller, "_control_agent", Mock(side_effect=AppError(503, "unavailable", "unavailable")))
    controller._stop_managed_agent({"id": "d" * 32})
    saved = json.loads(controller.state_path.read_text())
    assert saved["state"] == "TARGET_RUNNING" and saved["agent_state"] == "cleanup_pending"


def test_sandboxd_lifecycle_uses_admin_token_and_sends_no_docker_parameters():
    from app.providers.remote.sandboxd_client import SandboxdClient
    requests = []
    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={"state": "running", "enabled": True})
    with httpx.Client(base_url="http://sandboxd", transport=httpx.MockTransport(transport)) as session:
        client = SandboxdClient(url="http://sandboxd", token="service", admin_token="admin",
                                deployment_id="test", client=session)
        client.migration_agent_status()
        client.manage_migration_agent("a" * 32, "start")
        for job, operation in [("../containers", "start"), ("a" * 32, "exec")]:
            with pytest.raises(ValueError):
                client.manage_migration_agent(job, operation)
    assert len(requests) == 2
    assert all(r.headers["Authorization"] == "Bearer admin" and r.content == b"" for r in requests)
    assert requests[1].url.path == "/v1/database-migration-agent/" + "a" * 32 + "/start"
