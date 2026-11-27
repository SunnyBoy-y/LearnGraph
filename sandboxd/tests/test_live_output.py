from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sandboxd.config import SandboxdConfig
from sandboxd.controller import SandboxController
from sandboxd.runtime.supervisor import (
    LIVE_TAIL_MAX_BYTES,
    LIVE_TAIL_MAX_LINES,
    SUPERVISOR,
    extract_live_tail,
)
from sandboxd.store import SandboxRecord, SandboxdStore


def test_extract_live_tail_bounds_lines_and_bytes() -> None:
    many_lines = "".join(f"line-{i}\n" for i in range(300)).encode("utf-8")
    text, truncated = extract_live_tail(many_lines)
    assert truncated is True
    lines = text.splitlines()
    assert len(lines) == LIVE_TAIL_MAX_LINES
    assert lines[0] == "line-100"
    assert lines[-1] == "line-299"

    huge_single = ("x" * (LIVE_TAIL_MAX_BYTES + 1024)).encode("utf-8")
    text2, truncated2 = extract_live_tail(huge_single)
    assert truncated2 is True
    assert len(text2.encode("utf-8")) <= LIVE_TAIL_MAX_BYTES

    assert "stdout_tail" in SUPERVISOR
    assert "LIVE_FLUSH_INTERVAL" in SUPERVISOR


def test_controller_get_execution_forwards_live_tail_while_running(tmp_path: Path) -> None:
    store = SandboxdStore(tmp_path / "sandboxd.sqlite3")
    store.init()
    config = SandboxdConfig(
        listen_host="127.0.0.1",
        port=0,
        token="test-token",
        admin_token=None,
        state_path=str(tmp_path / "sandboxd.sqlite3"),
        deployment_id="dep-live",
        docker_host=None,
        runtime_backend="docker",
        runtime_image="img@sha256:" + "a" * 64,
        egress_network_enabled=False,
        egress_proxy_url=None,
        egress_proxy_container=None,
        max_request_bytes=16 * 1024 * 1024,
        max_file_bytes=8 * 1024 * 1024,
        max_stdout_bytes=1024 * 1024,
        max_active=4,
        workspace_uid="1000:1000",
        reconcile_on_start=False,
        reconcile_grace_seconds=5,
        ttl_sweep_interval_seconds=60,
        network_label="learngraph.egress",
        seccomp_dir=str(tmp_path),
    )
    runtime = SimpleNamespace(
        execution_result=lambda handle, execution_id: {
            "status": "running",
            "pid": 1234,
            "stdout_tail": "step 1\nstep 2\n",
            "stderr_tail": "warn: slow\n",
            "live_output_truncated": True,
            "latency_ms": 840,
        }
    )
    controller = SandboxController(config, store, runtime)
    store.insert_sandbox(
        SandboxRecord(
            sandbox_id="sb_1",
            deployment_id="dep-live",
            owner_scope="ws:user",
            owner_workspace_id="ws",
            owner_session_id="sess",
            session_id="sess",
            workspace_key="wk",
            runtime_kind="python-node",
            state="RUNNING",
            volume_name="vol",
            container_id="cid",
            image_digest="img@sha256:" + "a" * 64,
            runner_abi="1",
            policy_digest=None,
            egress_network=None,
            limits_json=json.dumps({}),
            ttl_seconds=600,
            expires_at="2099-01-01T00:00:00+00:00",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            last_used_at="2026-01-01T00:00:00+00:00",
        )
    )
    store.create_execution(
        execution_id="ex_live_1",
        sandbox_id="sb_1",
        deployment_id="dep-live",
        operation="agent",
        argv_digest="abc",
    )

    view = controller.get_execution("ex_live_1", "ws:user")
    assert view["status"] == "running"
    assert view["stdout_tail"] == "step 1\nstep 2\n"
    assert view["stderr_tail"] == "warn: slow\n"
    assert view["live_output_truncated"] is True
    assert view["latency_ms"] == 840
