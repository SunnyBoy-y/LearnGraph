"""sandboxd HTTP API tests via TestClient (fake runtime, no Docker)."""

import pytest
from fastapi.testclient import TestClient

from sandboxd.config import SandboxdConfig
from sandboxd.main import create_app

from tests.fake_runtime import FakeRuntime


def _client(config, runtime=None, store=None):
    app = create_app(
        config,
        store=store,
        runtime=runtime or FakeRuntime(),
        start_workers=False,
    )
    # TestClient without a context manager skips lifespan; initialize the
    # store eagerly so routes see their tables.
    app.state.store.init()
    return TestClient(app)


def _headers(token="test-token", scope="deploy-test|ses-1", **extra):
    headers = {"Authorization": f"Bearer {token}"}
    if scope:
        headers["X-Sandbox-Scope"] = scope
    headers.update(extra)
    return headers


def test_durable_execution_replay_result_and_owner_scope(config,store,runtime):
    native={}; launches=[]
    def start(handle,argv,*,execution_id,**kwargs):
        if execution_id not in native:
            launches.append(execution_id);native[execution_id]={'status':'running'}
    runtime.start_exec=start
    runtime.execution_result=lambda handle,eid:native.get(eid)
    runtime.cancel_exec=lambda handle,eid:True
    client=_client(config,runtime,store)
    created=client.post('/v1/sandboxes',headers=_headers(),json=_create_payload())
    assert created.status_code==200
    sandbox=created.json()['sandbox_id'];eid='ex_'+'a'*32
    body={'argv':['python','main.py'],'cwd':'.','timeout_seconds':5,'output_limit':4096,
          'idempotency_key':eid,'execution_id':eid}
    url='/v1/sandboxes/'+sandbox+'/executions/agent/start'
    assert client.post(url,headers=_headers(),json=body).json()['status']=='running'
    assert client.post(url,headers=_headers(),json=body).status_code==200
    assert launches==[eid]
    assert client.get('/v1/executions/'+eid,headers=_headers(scope='deploy-test|other')).status_code==403
    native[eid]={'status':'succeeded','exit_code':0,'stdout':'COMPLETE','stderr':'',
                 'timed_out':False,'truncated':False,'latency_ms':12}
    completed=client.get('/v1/executions/'+eid,headers=_headers()).json()
    assert completed['stdout']=='COMPLETE'
    # A new controller joins the persisted result even with no runtime-local map.
    native.clear()
    restarted=_client(config,runtime,store)
    assert restarted.get('/v1/executions/'+eid,headers=_headers()).json()==completed
    assert restarted.post(url,headers=_headers(),json=body).json()==completed


def _create_payload(**overrides):
    payload = dict(
        protocol_version="1.0",
        session_id="ses-1",
        workspace_key="u1/ses-1",
        owner={"deployment_id": "deploy-test", "workspace_id": "ws-1", "session_id": "ses-1"},
        runtime_kind="python-node",
        memory_bytes=256 * 1024 * 1024,
        memory_swap_bytes=256 * 1024 * 1024,
        cpu_count=1.0,
        pids_max=64,
        disk_bytes=1024 * 1024 * 1024,
        ttl_seconds=3600,
        idempotency_key="api-create-1",
    )
    payload.update(overrides)
    return payload


def test_live_without_auth(config):
    client = _client(config)
    response = client.get("/v1/health/live")
    assert response.status_code == 200 and response.json() == {"ok": True}


def test_protected_endpoints_require_auth(config):
    client = _client(config)
    assert client.get("/v1/capabilities").status_code == 401
    assert client.get("/v1/capabilities", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/health/ready").status_code == 401


def test_capabilities_and_capacity(config):
    client = _client(config)
    caps = client.get("/v1/capabilities", headers=_headers())
    assert caps.status_code == 200
    body = caps.json()
    assert body["protocol_min"] == "1.0" and body["runner_abi_min"] == "1"
    capacity = client.get("/v1/capacity", headers=_headers())
    assert capacity.status_code == 200 and capacity.json()["cpu_count"] == 8


def test_ready_with_fake_runtime(config):
    client = _client(config)
    response = client.get("/v1/health/ready", headers=_headers())
    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_create_and_get(config):
    client = _client(config)
    created = client.post("/v1/sandboxes", json=_create_payload(), headers=_headers())
    assert created.status_code == 200
    sandbox_id = created.json()["sandbox_id"]
    fetched = client.get(f"/v1/sandboxes/{sandbox_id}", headers=_headers())
    assert fetched.status_code == 200 and fetched.json()["sandbox_id"] == sandbox_id


def test_create_requires_scope_match_for_read(config):
    client = _client(config)
    created = client.post("/v1/sandboxes", json=_create_payload(), headers=_headers())
    sandbox_id = created.json()["sandbox_id"]
    wrong = client.get(
        f"/v1/sandboxes/{sandbox_id}",
        headers=_headers(scope="deploy-test|other-ses"),
    )
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "owner_mismatch"


def test_scope_header_required(config):
    client = _client(config)
    created = client.post("/v1/sandboxes", json=_create_payload(), headers=_headers())
    sandbox_id = created.json()["sandbox_id"]
    response = client.get(f"/v1/sandboxes/{sandbox_id}", headers=_headers(scope=None))
    assert response.status_code == 400


def test_file_roundtrip(config):
    client = _client(config)
    created = client.post("/v1/sandboxes", json=_create_payload(), headers=_headers())
    sandbox_id = created.json()["sandbox_id"]
    put = client.put(
        f"/v1/sandboxes/{sandbox_id}/files?path=work/a.py&mode=420",
        content=b"print(1)\n",
        headers=_headers(),
    )
    assert put.status_code == 204
    get = client.get(f"/v1/sandboxes/{sandbox_id}/files?path=work/a.py", headers=_headers())
    assert get.status_code == 200 and get.content == b"print(1)\n"
    index = client.get(f"/v1/sandboxes/{sandbox_id}/file-index", headers=_headers())
    assert index.status_code == 200
    assert any(e["path"] == "work/a.py" for e in index.json()["entries"])
    deleted = client.delete(f"/v1/sandboxes/{sandbox_id}/files?path=work/a.py", headers=_headers())
    assert deleted.status_code == 204


def test_file_too_large_rejected(config):
    client = _client(config)
    created = client.post("/v1/sandboxes", json=_create_payload(), headers=_headers())
    sandbox_id = created.json()["sandbox_id"]
    response = client.put(
        f"/v1/sandboxes/{sandbox_id}/files?path=work/big.bin",
        content=b"x" * (config.max_file_bytes + 1),
        headers=_headers(),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_file_upload_declared_length_enforced(config):
    client = _client(config)
    created = client.post("/v1/sandboxes", json=_create_payload(), headers=_headers())
    sandbox_id = created.json()["sandbox_id"]
    # A client-declared Content-Length above the daemon limit is rejected even
    # when the actual body is tiny (defends against streamed/large uploads).
    response = client.put(
        f"/v1/sandboxes/{sandbox_id}/files?path=work/a.py",
        content=b"x",
        headers=_headers(**{"Content-Length": str(config.max_file_bytes + 1)}),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_exec_agent_endpoint(config):
    client = _client(config)
    created = client.post("/v1/sandboxes", json=_create_payload(), headers=_headers())
    sandbox_id = created.json()["sandbox_id"]
    response = client.post(
        f"/v1/sandboxes/{sandbox_id}/executions/agent",
        json={"argv": ["echo-ok"], "cwd": ".", "timeout_seconds": 30, "output_limit": 1048576, "idempotency_key": "api-exec-1"},
        headers=_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded" and body["stdout"] == "ok\n"
    execution = client.get(f"/v1/executions/{body['execution_id']}", headers=_headers())
    assert execution.status_code == 200 and execution.json()["status"] == "succeeded"


def test_delete_sandbox(config):
    client = _client(config)
    created = client.post("/v1/sandboxes", json=_create_payload(), headers=_headers())
    sandbox_id = created.json()["sandbox_id"]
    assert client.delete(f"/v1/sandboxes/{sandbox_id}", headers=_headers()).status_code == 204
    assert client.delete(f"/v1/sandboxes/{sandbox_id}", headers=_headers()).status_code == 204


def test_admin_surface_disabled_without_admin_token(config):
    client = _client(config)
    response = client.get("/v1/runtimes", headers=_headers())
    assert response.status_code == 503


def test_bootstrap_job_with_admin_token(config):
    from dataclasses import replace

    admin_config = replace(config, admin_token="admin-tok")
    client = _client(admin_config)
    admin_headers = {"Authorization": "Bearer admin-tok"}
    installed = client.post(
        "/v1/bootstrap/jobs",
        json={"runtime_kind": "python-node", "image_tag": "cr.example/learngraph-sandbox:1.0.0"},
        headers=admin_headers,
    )
    assert installed.status_code == 200
    body = installed.json()
    assert body["source"] == "prebuilt" and body["runner_abi"] == "1"
    runtimes = client.get("/v1/runtimes", headers=admin_headers)
    assert runtimes.status_code == 200 and len(runtimes.json()) == 1
    # Service token cannot use the admin surface (rejected as unauthorized).
    assert client.get("/v1/runtimes", headers=_headers()).status_code == 401


def test_request_id_header_echoed(config):
    client = _client(config)
    response = client.get("/v1/health/ready", headers=_headers(**{"X-Request-Id": "req-12345"}))
    assert response.headers.get("X-Request-Id") == "req-12345"
