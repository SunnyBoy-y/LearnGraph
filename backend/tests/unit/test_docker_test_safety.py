"""Rejection happens before network connections, SQL or credential reads."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docker_test_safety import OWNER, endpoint, owned_container


def test_missing_test_owner_fails_before_docker_access(monkeypatch):
    monkeypatch.delenv("LEARNGRAPH_TEST_DOCKER_PROJECT", raising=False)
    docker = Mock()
    monkeypatch.setitem(sys.modules, "docker", docker)
    with pytest.raises(RuntimeError, match="explicit private test project"):
        owned_container("learngraph-app-1")
    docker.from_env.assert_not_called()


def test_production_named_container_is_rejected_without_any_exec(monkeypatch):
    monkeypatch.setenv("LEARNGRAPH_TEST_DOCKER_PROJECT", "lg-pg-maintenance-0123456789")
    monkeypatch.setenv("LEARNGRAPH_TEST_DOCKER_OWNER", "a"*32)
    client = Mock()
    client.containers.get.return_value.attrs = {"Name":"/learngraph-app-1", "Config":{"Labels":{}}, "Mounts":[]}
    monkeypatch.setitem(sys.modules,"docker",SimpleNamespace(from_env=lambda:client))
    with pytest.raises(RuntimeError, match="production container"):
        owned_container("formal-id-mocked-only")
    client.close.assert_called_once()
    client.containers.get.return_value.exec_run.assert_not_called()


def test_readonly_foreign_volume_is_rejected(monkeypatch):
    project, owner = "lg-pg-maintenance-0123456789", "a"*32
    monkeypatch.setenv("LEARNGRAPH_TEST_DOCKER_PROJECT",project)
    monkeypatch.setenv("LEARNGRAPH_TEST_DOCKER_OWNER",owner)
    labels = {OWNER:owner,"com.docker.compose.project":project}
    client = Mock()
    client.containers.get.return_value.attrs = {"Name":"/"+project+"-postgres-test-1", "Config":{"Labels":labels}, "NetworkSettings":{"Networks":{project+"_default":{}}}, "Mounts":[{"Type":"volume","Name":"learngraph_learngraph-data","Destination":"/foreign","RW":False}]}
    client.networks.get.return_value.attrs = {"Labels":labels}
    client.volumes.get.return_value.attrs = {"Labels":{"com.docker.compose.project":"learngraph"}}
    monkeypatch.setitem(sys.modules,"docker",SimpleNamespace(from_env=lambda:client))
    with pytest.raises(RuntimeError, match="production volume"):
        owned_container("fixture-id")
    client.volumes.get.assert_not_called()


def test_database_options_cannot_redirect_test_connection(monkeypatch):
    with pytest.raises(RuntimeError, match="options"):
        endpoint("postgresql://test@127.0.0.1/maintenance_test?host=production", "LEARNGRAPH_TEST_POSTGRES_CONTAINER", postgres=True)


def test_sandboxd_collection_does_not_probe_default_or_read_token(monkeypatch):
    monkeypatch.delenv("LEARNGRAPH_TEST_SANDBOXD_DOCKER", raising=False)
    from integration import test_sandboxd_roundtrip as suite
    with pytest.raises(pytest.skip.Exception):
        suite._client()


def test_legacy_oci_smoke_rejects_without_owner_before_runtime_import(monkeypatch):
    from pathlib import Path
    import runpy
    script = Path("/app/backend-scripts/oci_runtime_smoke.py")
    if not script.is_file():
        pytest.skip("runs in the isolated Docker test worker")
    monkeypatch.delenv("LEARNGRAPH_TEST_DOCKER_PROJECT", raising=False)
    with pytest.raises(RuntimeError, match="Docker test isolation"):
        runpy.run_path(str(script),run_name="__main__")


@pytest.mark.parametrize("url",["postgresql+psycopg://test@formal/learngraph","sqlite:////data/custom-production.db"])
def test_global_suite_rejects_early_plugin_database_without_filename_heuristics(monkeypatch,request,url):
    from pathlib import Path
    from sqlalchemy.engine import make_url
    location=Path(__file__).resolve().parents[1]/"conftest.py"
    plugin=next(p for p in request.config.pluginmanager.get_plugins() if getattr(p,"__file__",None) and Path(p.__file__).resolve()==location)
    monkeypatch.setitem(sys.modules,"app.core.database",SimpleNamespace(engine=SimpleNamespace(url=make_url(url))))
    with pytest.raises(pytest.UsageError,match="process-local scratch SQLite"):
        plugin._assert_engine_is_not_real_database()


def test_global_suite_accepts_its_exact_scratch_database(monkeypatch,request):
    from pathlib import Path
    from sqlalchemy.engine import make_url
    location=Path(__file__).resolve().parents[1]/"conftest.py"
    plugin=next(p for p in request.config.pluginmanager.get_plugins() if getattr(p,"__file__",None) and Path(p.__file__).resolve()==location)
    url="sqlite:///"+(plugin._TEST_ROOT/"test.db").as_posix()
    monkeypatch.setitem(sys.modules,"app.core.database",SimpleNamespace(engine=SimpleNamespace(url=make_url(url))))
    plugin._assert_engine_is_not_real_database()


def test_docker_pytest_without_owner_rejected_before_scratch_creation(monkeypatch):
    from pathlib import Path
    import runpy
    from unittest.mock import patch
    if not Path("/.dockerenv").is_file(): pytest.skip("isolated Docker worker check")
    monkeypatch.delenv("LEARNGRAPH_TEST_DOCKER_OWNER",raising=False)
    with patch("tempfile.mkdtemp") as create, pytest.raises(pytest.UsageError,match="production container execution refused"):
        runpy.run_path(str(Path(__file__).resolve().parents[1]/"conftest.py"),run_name="__guard_probe__")
    create.assert_not_called()
