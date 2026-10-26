from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import json
import time

import pytest
from fastapi.testclient import TestClient

from sandboxd.main import create_app
from sandboxd.migration_agent import ENTRYPOINT, ManagedMigrationAgent, MigrationAgentError
from tests.fake_runtime import FakeRuntime

JOB = "a" * 32
PROJECT = "lg-test"
NETWORK = PROJECT + "_default"
DATA = {"Type": "volume", "Name": PROJECT + "_data", "Destination": "/data", "RW": True}
SOCKET = {"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock", "RW": True}


def container(role, *, status="running"):
    image = SimpleNamespace(id="sha256:" + "a" * 64, attrs={"Config": {"Labels": {
        "io.learngraph.primary-database": "sqlite-postgresql-v1",
    }}})
    labels = {"com.docker.compose.project": PROJECT, "com.docker.compose.service": role}
    item = SimpleNamespace(id=role + "-id", labels=labels, image=image, status=status)
    item.attrs = {
        "Config": {"Labels": labels, "Entrypoint": ENTRYPOINT, "Cmd": [], "User": "root",
                   "Env": ["LEARNGRAPH_MASTER_KEY=private-test-key", "SANDBOXD_DEPLOYMENT_ID=deploy-test"]},
        "HostConfig": {"RestartPolicy": {"Name": "no"}},
        "Mounts": deepcopy([SOCKET] if role == "sandboxd" else [DATA, SOCKET] if role == "database-migration-agent" else [DATA]),
        "NetworkSettings": {"Networks": {NETWORK: {"Aliases": ["database-migration-agent"]}}},
    }
    item.reload = Mock()
    item.start = Mock(side_effect=lambda: setattr(item, "status", "running"))
    item.stop = Mock(side_effect=lambda **_: setattr(item, "status", "exited"))
    return item


@pytest.fixture
def setup(config):
    daemon, app = container("sandboxd"), container("app")
    agents = []
    client = SimpleNamespace(close=Mock())
    client.api = SimpleNamespace(create_endpoint_config=Mock(side_effect=lambda **kwargs: {"Aliases": kwargs["aliases"]}))
    def listing(*, all, filters):
        return [app] if "com.docker.compose.service=app" in filters["label"] else agents
    def create(image, **kwargs):
        assert image == app.image.id
        assert kwargs["entrypoint"] == ENTRYPOINT and kwargs["command"] == []
        assert set(kwargs["volumes"]) == {PROJECT + "_data", "/var/run/docker.sock"}
        assert kwargs["network"] == NETWORK and kwargs["restart_policy"] == {"Name": "no"}
        assert kwargs["networking_config"] == {NETWORK: {"Aliases": ["database-migration-agent"]}}
        assert kwargs["environment"] == {"LEARNGRAPH_MASTER_KEY": "private-test-key"}
        result = container("database-migration-agent", status="created")
        agents.append(result)
        return result
    client.containers = SimpleNamespace(get=Mock(return_value=daemon), list=Mock(side_effect=listing), create=Mock(side_effect=create))
    client.networks = SimpleNamespace(get=Mock(return_value=SimpleNamespace(attrs={"Labels": {
        "com.docker.compose.project": PROJECT,
    }})))
    manager = ManagedMigrationAgent(replace(config, managed_database_enabled=True), client_factory=lambda: client)
    return manager, client, agents, app


def test_creates_only_fixed_agent_and_reuses_it(setup):
    manager, client, agents, _ = setup
    assert manager.start(JOB) == {"state": "running", "job_id": JOB}
    manager.start(JOB)
    client.containers.create.assert_called_once()
    agents[0].start.assert_called_once()
    manager.stop(JOB)
    agents[0].stop.assert_called_once_with(timeout=10)
    assert not manager.path.exists()


def test_preexisting_profile_container_can_be_started(setup):
    manager, client, agents, _ = setup
    agents.append(container("database-migration-agent", status="exited"))
    manager.start(JOB)
    client.containers.create.assert_not_called()
    agents[0].start.assert_called_once()


@pytest.mark.parametrize("mutation", [
    lambda a: a.attrs["Config"].update(Entrypoint=["sh", "-c", "unsafe"]),
    lambda a: a.attrs["Config"].update(Cmd=["extra"]),
    lambda a: a.attrs["HostConfig"].update(Privileged=True),
    lambda a: a.attrs["HostConfig"].update(PortBindings={"8092/tcp": [{"HostPort": "8092"}]}),
    lambda a: a.attrs["Mounts"].append({"Type": "bind", "Source": "/", "Destination": "/host", "RW": True}),
    lambda a: a.attrs["Mounts"][0].update(Name="another-project-data"),
    lambda a: a.attrs["NetworkSettings"]["Networks"].update({"foreign": {}}),
    lambda a: a.attrs["NetworkSettings"]["Networks"][NETWORK].update(Aliases=[]),
])
def test_refuses_unapproved_existing_container(setup, mutation):
    manager, _, agents, _ = setup
    agent = container("database-migration-agent", status="exited")
    mutation(agent)
    agents.append(agent)
    with pytest.raises(MigrationAgentError):
        manager.start(JOB)
    agent.start.assert_not_called()


def test_job_ownership_prevents_cross_job_start_and_stop(setup):
    manager, _, agents, _ = setup
    manager.start(JOB)
    for operation in (manager.start, manager.stop):
        with pytest.raises(MigrationAgentError):
            operation("b" * 32)
    agents[0].stop.assert_not_called()


def test_lease_survives_daemon_restart_and_cleans_up_when_app_is_stopped(setup):
    manager, client, agents, app = setup
    manager.start(JOB)
    record = json.loads(manager.path.read_text())
    record["expires_at"] = time.time() - 1
    manager._save(record)
    app.status = "exited"
    restarted = ManagedMigrationAgent(manager.config, client_factory=lambda: client)
    restarted.sweep()
    agents[0].stop.assert_called_once()
    assert not manager.path.exists()


def test_stop_never_targets_replaced_container(setup):
    manager, _, agents, _ = setup
    manager.start(JOB)
    agents[0].id = "replacement"
    with pytest.raises(MigrationAgentError):
        manager.stop(JOB)
    agents[0].stop.assert_not_called()


def test_stopped_outdated_agent_is_recreated_without_removing_volumes(setup):
    manager, client, agents, _ = setup
    old = container("database-migration-agent", status="exited")
    old.image = SimpleNamespace(id="outdated")
    old.remove = Mock(side_effect=lambda: agents.remove(old))
    agents.append(old)
    manager.start(JOB)
    old.remove.assert_called_once_with()
    client.containers.create.assert_called_once()
    assert len(agents) == 1 and agents[0].status == "running"


def test_disabled_control_performs_no_docker_access(config):
    factory = Mock()
    manager = ManagedMigrationAgent(config, client_factory=factory)
    with pytest.raises(MigrationAgentError):
        manager.start(JOB)
    factory.assert_not_called()


def test_control_plane_identity_must_match_its_own_deployment(setup):
    manager, client, _, _ = setup
    client.containers.get.return_value.attrs["Config"]["Env"] = ["SANDBOXD_DEPLOYMENT_ID=foreign"]
    with pytest.raises(MigrationAgentError):
        manager.start(JOB)
    client.containers.create.assert_not_called()


def test_admin_only_endpoints_reject_parameters_and_invalid_jobs(config):
    app = create_app(replace(config, admin_token="admin-token"), runtime=FakeRuntime(), start_workers=False)
    manager = Mock()
    manager.start.return_value = {"state": "running"}
    app.state.migration_agent.start = manager.start
    with TestClient(app) as client:
        url = f"/v1/database-migration-agent/{JOB}/start"
        assert client.post(url).status_code == 401
        assert client.post(url, headers={"Authorization": "Bearer test-token"}).status_code == 401
        headers = {"Authorization": "Bearer admin-token"}
        assert client.post(url, headers=headers, json={"image": "arbitrary"}).status_code == 400
        assert client.post(url + "?command=exec", headers=headers).status_code == 400
        assert client.post("/v1/database-migration-agent/not-a-job/start", headers=headers).status_code == 422
        assert client.post(url, headers=headers).status_code == 200
        manager.start.assert_called_once_with(JOB)
