"""Store tests: sandboxes, idempotency ledger, executions."""

import json

from sandboxd.store import SandboxRecord


def _record(sandbox_id="sb_123", state="RUNNING", **overrides):
    base = dict(
        sandbox_id=sandbox_id,
        deployment_id="deploy-test",
        owner_scope="deploy-test|ses-1",
        owner_workspace_id="ws-1",
        owner_session_id="ses-1",
        session_id="ses-1",
        workspace_key="u1/sb_123",
        runtime_kind="python-node",
        state=state,
        volume_name="deploy-test-sb-sb_123",
        container_id="ctr-1",
        image_digest="sha256:" + "a" * 64,
        runner_abi="1",
        policy_digest=None,
        egress_network=None,
        limits_json=json.dumps({"disk_bytes": 1024 * 1024}),
        ttl_seconds=3600,
        expires_at="2099-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        last_used_at="2026-01-01T00:00:00+00:00",
    )
    base.update(overrides)
    return SandboxRecord(**base)


def test_sandbox_crud(store):
    store.insert_sandbox(_record())
    record = store.get_sandbox("sb_123")
    assert record is not None and record.state == "RUNNING"
    store.update_sandbox("sb_123", state="STOPPED", container_id=None)
    assert store.get_sandbox("sb_123").state == "STOPPED"
    assert store.get_sandbox("sb_123").container_id is None
    store.delete_sandbox("sb_123")
    assert store.get_sandbox("sb_123") is None
    assert store.count_active("deploy-test") == 0


def test_count_active_excludes_deleting(store):
    store.insert_sandbox(_record("sb_1"))
    store.insert_sandbox(_record("sb_2", state="DELETING"))
    assert store.count_active("deploy-test") == 1


def test_idempotency_claim_and_complete(store):
    proceed, replay = store.begin_idempotent("scope|sb_1", "key-1", "create", "hash-a")
    assert proceed and replay is None
    proceed2, replay2 = store.begin_idempotent("scope|sb_1", "key-1", "create", "hash-a")
    assert not proceed2 and replay2 is not None and replay2.state == "in_progress"
    store.complete_idempotent("scope|sb_1", "key-1", state="succeeded", result_json='{"ok":1}')
    proceed3, replay3 = store.begin_idempotent("scope|sb_1", "key-1", "create", "hash-a")
    assert not proceed3 and replay3.state == "succeeded" and replay3.result_json == '{"ok":1}'


def test_idempotency_scope_isolation(store):
    store.begin_idempotent("scope-a", "key", "create", "h")
    proceed, _ = store.begin_idempotent("scope-b", "key", "create", "h")
    assert proceed


def test_executions(store):
    store.create_execution(
        execution_id="ex_1", sandbox_id="sb_1", deployment_id="deploy-test", operation="agent", argv_digest="d"
    )
    store.finish_execution(execution_id="ex_1", status="succeeded", exit_code=0, timed_out=False, truncated=False, latency_ms=5)
    record = store.get_execution("ex_1")
    assert record["status"] == "succeeded" and record["exit_code"] == 0 and record["latency_ms"] == 5
    assert store.get_execution("ex_missing") is None
