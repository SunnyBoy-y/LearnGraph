"""Controller tests: ownership, idempotency, protocol, lifecycle, files, exec."""

import json

import pytest

from sandboxd.controller import SandboxController, SandboxdError, _payload_hash
from sandboxd.protocol import AgentExecRequest, CreateSandboxRequest, FixedExecRequest, OwnerScope


def _controller(config, store, runtime):
    return SandboxController(config, store, runtime)


def _owner(workspace="ws-1", session="ses-1"):
    return OwnerScope(deployment_id="deploy-test", workspace_id=workspace, session_id=session)


def _create_body(**overrides):
    base = dict(
        protocol_version="1.0",
        session_id="ses-1",
        workspace_key="u1/ses-1",
        owner=_owner(),
        runtime_kind="python-node",
        memory_bytes=256 * 1024 * 1024,
        memory_swap_bytes=256 * 1024 * 1024,
        cpu_count=1.0,
        pids_max=64,
        disk_bytes=1024 * 1024 * 1024,
        egress=None,
        ttl_seconds=3600,
        idempotency_key="create-1",
    )
    base.update(overrides)
    return CreateSandboxRequest(**base)


def test_create_ok(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    assert view.sandbox_id.startswith("sb_")
    assert view.state == "RUNNING"
    assert len(runtime.created) == 1
    assert store.get_sandbox(view.sandbox_id) is not None


def test_create_protocol_incompatible(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    with pytest.raises(SandboxdError) as exc:
        ctrl.create(_create_body(protocol_version="9.9"))
    assert exc.value.code == "protocol_incompatible"
    assert not runtime.created


def test_create_owner_deployment_mismatch(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    owner = OwnerScope(deployment_id="other", workspace_id="w", session_id="s")
    with pytest.raises(SandboxdError) as exc:
        ctrl.create(_create_body(owner=owner))
    assert exc.value.code == "owner_mismatch"


def test_create_idempotent_replay(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    first = ctrl.create(_create_body())
    second = ctrl.create(_create_body())
    assert second.sandbox_id == first.sandbox_id
    assert len(runtime.created) == 1


def test_create_idempotency_conflict(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    ctrl.create(_create_body())
    with pytest.raises(SandboxdError) as exc:
        ctrl.create(_create_body(disk_bytes=2 * 1024 * 1024 * 1024))
    assert exc.value.code == "idempotency_conflict"


def test_create_capacity_exceeded(config, store, runtime):
    from dataclasses import replace

    config = replace(config, max_active=1)
    ctrl = _controller(config, store, runtime)
    ctrl.create(_create_body(idempotency_key="k1"))
    # A different session (new physical sandbox) must be rejected by capacity.
    with pytest.raises(SandboxdError) as exc:
        ctrl.create(
            _create_body(
                idempotency_key="k2",
                session_id="ses-2",
                owner=_owner(session="ses-2"),
                workspace_key="u1/ses-2",
            )
        )
    assert exc.value.code == "capacity_exceeded"


def test_create_same_session_reuse(config, store, runtime):
    """Execution-pool reuse: same session + compatible payload returns the
    same physical sandbox even with a different idempotency key (warm
    instance reuse across a user's chat workspaces)."""
    ctrl = _controller(config, store, runtime)
    first = ctrl.create(_create_body(idempotency_key="k1"))
    second = ctrl.create(_create_body(idempotency_key="k2"))
    assert second.sandbox_id == first.sandbox_id
    assert len(runtime.created) == 1
    assert store.count_active("deploy-test") == 1


def test_create_egress_valid_and_invalid(config, store, runtime):
    from sandboxd.protocol import EgressRef

    ctrl = _controller(config, store, runtime)
    view = ctrl.create(
        _create_body(
            idempotency_key="k-eg",
            egress=EgressRef(policy_digest="b" * 64),
        )
    )
    record = store.get_sandbox(view.sandbox_id)
    assert record.policy_digest == "b" * 64
    assert record.egress_network is not None

    with pytest.raises(SandboxdError) as exc:
        ctrl.create(
            _create_body(
                idempotency_key="k-bad-digest",
                egress=EgressRef(policy_digest="not-a-digest"),
            )
        )
    assert exc.value.code == "invalid_request"


def test_ownership_enforced(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    wrong_scope = "deploy-test|other-ses"
    with pytest.raises(SandboxdError) as exc:
        ctrl.get(view.sandbox_id, wrong_scope)
    assert exc.value.code == "owner_mismatch"
    assert ctrl.get(view.sandbox_id, "deploy-test|ses-1").sandbox_id == view.sandbox_id


def test_stop_resume(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    stopped = ctrl.stop(view.sandbox_id, "deploy-test|ses-1")
    assert stopped.state == "STOPPED"
    resumed = ctrl.resume(view.sandbox_id, "deploy-test|ses-1")
    assert resumed.state == "RUNNING"
    assert view.sandbox_id in runtime.stopped and view.sandbox_id in runtime.resumed


def test_delete_idempotent(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    scope = "deploy-test|ses-1"
    ctrl.delete(view.sandbox_id, scope)
    ctrl.delete(view.sandbox_id, scope)  # second delete is a no-op
    assert view.sandbox_id in runtime.deleted


def test_expired_sandbox(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    record = store.get_sandbox(view.sandbox_id)
    store.update_sandbox(view.sandbox_id, expires_at="2000-01-01T00:00:00+00:00")
    with pytest.raises(SandboxdError) as exc:
        ctrl.get(view.sandbox_id, "deploy-test|ses-1")
    assert exc.value.code == "sandbox_expired"
    del record


def test_file_ops_and_quota(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    scope = "deploy-test|ses-1"
    ctrl.write_file(view.sandbox_id, scope, "work/a.py", b"print(1)")
    assert ctrl.read_file(view.sandbox_id, scope, "work/a.py", 1024) == b"print(1)"
    index = ctrl.list_files(view.sandbox_id, scope, "", 100, None)
    assert any(e.path == "work/a.py" for e in index.entries)
    ctrl.delete_file(view.sandbox_id, scope, "work/a.py")
    with pytest.raises(RuntimeError):
        ctrl.read_file(view.sandbox_id, scope, "work/a.py", 1024)


def test_file_quota_mapped(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    runtime.quota_exceeded = True
    with pytest.raises(SandboxdError) as exc:
        ctrl.write_file(view.sandbox_id, "deploy-test|ses-1", "work/b.py", b"x")
    assert exc.value.code == "workspace_quota_exceeded"


def test_file_too_large(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    with pytest.raises(SandboxdError) as exc:
        ctrl.write_file(view.sandbox_id, "deploy-test|ses-1", "work/big.bin", b"x" * (config.max_file_bytes + 1))
    assert exc.value.code == "file_too_large"
    assert not runtime.files


def test_invalid_path_rejected(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    with pytest.raises(SandboxdError) as exc:
        ctrl.write_file(view.sandbox_id, "deploy-test|ses-1", "../escape", b"x")
    assert exc.value.code == "invalid_path"


def test_exec_agent_success_and_idempotent_replay(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    scope = "deploy-test|ses-1"
    body = AgentExecRequest(
        argv=["echo-ok"],
        cwd=".",
        timeout_seconds=30,
        output_limit=1024 * 1024,
        idempotency_key="exec-1",
    )
    outcome = ctrl.exec_agent(view.sandbox_id, scope, body)
    assert outcome.result is not None and outcome.result.status == "succeeded"
    replay = ctrl.exec_agent(view.sandbox_id, scope, body)
    assert replay.result.execution_id == outcome.result.execution_id
    assert len(runtime.exec_calls) == 1


def test_exec_idempotency_conflict(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    scope = "deploy-test|ses-1"
    ctrl.exec_agent(
        view.sandbox_id, scope,
        AgentExecRequest(argv=["echo-ok"], cwd=".", timeout_seconds=30, output_limit=1024 * 1024, idempotency_key="exec-1"),
    )
    with pytest.raises(SandboxdError) as exc:
        ctrl.exec_agent(
            view.sandbox_id, scope,
            AgentExecRequest(argv=["echo-ok"], cwd=".", timeout_seconds=60, output_limit=1024 * 1024, idempotency_key="exec-1"),
        )
    assert exc.value.code == "idempotency_conflict"


def test_exec_in_progress_is_indeterminate(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    scope = "deploy-test|ses-1"
    payload = {
        "argv": ["echo-ok"],
        "cwd": ".",
        "timeout_seconds": 30,
        "output_limit": 1024 * 1024,
    }
    key_scope = f"{scope}|{view.sandbox_id}"
    store.begin_idempotent(key_scope, "exec-pending", "agent", _payload_hash(payload))
    with pytest.raises(SandboxdError) as exc:
        ctrl.exec_agent(
            view.sandbox_id, scope,
            AgentExecRequest(argv=["echo-ok"], cwd=".", timeout_seconds=30, output_limit=1024 * 1024, idempotency_key="exec-pending"),
        )
    assert exc.value.code == "execution_indeterminate"
    assert len(runtime.exec_calls) == 0


def test_exec_fixed_builds_runner_argv(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body())
    scope = "deploy-test|ses-1"
    body = FixedExecRequest(
        task_type="extract_legacy_doc",
        input_path="input/doc.bin",
        output_path="output/out.json",
        timeout_seconds=30,
        output_limit=1024 * 1024,
        idempotency_key="fixed-1",
    )
    outcome = ctrl.exec_fixed(view.sandbox_id, scope, body)
    assert outcome.result is not None and outcome.result.status == "succeeded"
    argv = runtime.exec_calls[0][1]
    assert argv[0] == "python" and "--task" in argv and "extract_legacy_doc" in argv


def test_sweep_expired_deletes(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    view = ctrl.create(_create_body(idempotency_key="sweep-1"))
    store.update_sandbox(view.sandbox_id, expires_at="2000-01-01T00:00:00+00:00")
    assert ctrl.sweep_expired() == 1
    assert view.sandbox_id in runtime.deleted
    assert store.get_sandbox(view.sandbox_id) is None


def test_install_runtime_from_installed_record(config, store, runtime):
    # Without an env runtime_image pin, create uses the installed record.
    from dataclasses import replace

    config = replace(config, runtime_image=None)
    ctrl = _controller(config, store, runtime)
    installed = ctrl.install_runtime("python-node", "cr.example/learngraph-sandbox:1.0.0")
    assert installed["source"] == "prebuilt"
    assert installed["runner_abi"] == "1"
    assert installed["image_digest"].startswith("sha256:")
    view = ctrl.create(_create_body())
    assert runtime.created[-1].runtime_ref == installed["image_digest"]


def test_install_runtime_rejects_unknown_kind(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    with pytest.raises(SandboxdError) as exc:
        ctrl.install_runtime("not-a-kind", "cr.example/x:1.0.0")
    assert exc.value.code == "invalid_request"


def test_create_fails_closed_without_runtime(config, store, runtime):
    from dataclasses import replace

    config = replace(config, runtime_image=None)
    ctrl = _controller(config, store, runtime)
    with pytest.raises(SandboxdError) as exc:
        ctrl.create(_create_body())
    assert exc.value.code == "runtime_unavailable"
    assert not runtime.created


def test_list_runtimes(config, store, runtime):
    ctrl = _controller(config, store, runtime)
    assert ctrl.list_runtimes() == []
    ctrl.install_runtime("python-node", "cr.example/learngraph-sandbox:1.0.0")
    records = ctrl.list_runtimes()
    assert len(records) == 1 and records[0]["runtime_kind"] == "python-node"
    assert records[0]["image_present"] is True


def test_list_runtimes_checks_image_presence_per_kind(config, store, runtime, monkeypatch):
    ctrl = _controller(config, store, runtime)
    code = ctrl.install_runtime("python-node", "cr.example/runner:code")
    browser = ctrl.install_runtime("python-node-browser", "cr.example/runner:browser")
    monkeypatch.setattr(runtime, "artifact_present", lambda digest: digest != browser["image_digest"])
    records = {r["runtime_kind"]: r for r in ctrl.list_runtimes()}
    assert records["python-node"]["image_present"] is True
    assert records["python-node"]["image_digest"] == code["image_digest"]
    assert records["python-node-browser"]["image_present"] is False
    assert records["python-node-browser"]["smoke_status"] == "passed"
