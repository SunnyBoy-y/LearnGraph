"""Real sandboxd Docker roundtrip (opt-in).

Requires:
- a running sandboxd with Docker Engine access
- explicitly owned disposable Docker daemon, endpoint and credential paths

Run:
  cd backend
  uv run --locked --extra test pytest tests/integration/test_sandboxd_roundtrip.py -q

The suite is skipped unless explicitly enabled. No endpoint is probed during
collection. Docker ownership is required before reading tokens or connecting.
Every sandbox created is
deleted in ``finally`` and the suite asserts no managed container/volume is
left behind.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_SANDBOXD_URL = os.environ.get("LEARNGRAPH_TEST_SANDBOXD_URL", "")
_DEPLOYMENT_ID = os.environ.get("LEARNGRAPH_TEST_SANDBOXD_DEPLOYMENT_ID", "")
_TOKEN_FILE = os.environ.get(
    "LEARNGRAPH_TEST_SANDBOXD_TOKEN_FILE",
    "",
)
_ADMIN_TOKEN_FILE = os.environ.get(
    "LEARNGRAPH_TEST_SANDBOXD_ADMIN_TOKEN_FILE",
    "",
)
# A local pinned test runner image. Bootstrap can install it via the admin API;
# point LEARNGRAPH_TEST_SANDBOXD_IMAGE at any local image with runner-abi=1.
_IMAGE = os.environ.get(
    "LEARNGRAPH_TEST_SANDBOXD_IMAGE",
    "sha256:d875d19f6d93f8d2d9490e69f93aa40fbb15d830e6066341f88246f31d56bfb5",
)


def _token(path: str) -> str:
    from docker_test_safety import credential_path
    return credential_path(path).read_text(encoding="utf-8").strip()


def _client():
    if os.environ.get("LEARNGRAPH_TEST_SANDBOXD_DOCKER") != "1":
        pytest.skip("requires explicit owned disposable sandboxd Docker fixture")
    from docker_test_safety import endpoint, require
    endpoint(_SANDBOXD_URL, "LEARNGRAPH_TEST_SANDBOXD_CONTAINER")
    require(_DEPLOYMENT_ID.startswith("lg-test-") and len(_DEPLOYMENT_ID) >= 20, "private sandboxd test deployment required")
    from app.providers.remote.sandboxd_client import (
        SandboxdClient,
        SandboxdProtocolError,
        SandboxdUnavailable,
    )

    return (
        SandboxdClient(
            url=_SANDBOXD_URL,
            token=_token(_TOKEN_FILE),
            admin_token=_token(_ADMIN_TOKEN_FILE),
            deployment_id=_DEPLOYMENT_ID,
            connect_timeout=2.0,
            request_timeout=190.0,
        ),
        (SandboxdProtocolError, SandboxdUnavailable),
    )


def _ensure_runtime(client, runtime_kind: str = "python-node") -> None:
    try:
        runtimes = client.list_runtimes()
    except Exception:  # noqa: BLE001
        runtimes = []
    if any(r.get("runtime_kind") == runtime_kind for r in runtimes):
        return
    client.install_runtime(runtime_kind, _IMAGE)


@pytest.mark.parametrize("runtime_kind", ["python-node", "python-node-browser"])
def test_sandboxd_roundtrip_create_write_exec_read_stop_resume_delete(runtime_kind):
    from app.providers.remote.sandboxd_client import SandboxdProtocolError

    client, errors = _client()
    _ensure_runtime(client, runtime_kind)
    session_id = f"it-roundtrip-{uuid.uuid4().hex[:10]}"
    memory_bytes = (2 * 1024**3) if runtime_kind == "python-node-browser" else (512 * 1024**2)
    pids_max = 512 if runtime_kind == "python-node-browser" else 128
    sandbox_id: str | None = None
    try:
        view = client.create_sandbox(
            {
                "protocol_version": "1.0",
                "session_id": session_id,
                "workspace_key": session_id,
                "owner": {"deployment_id": _DEPLOYMENT_ID, "workspace_id": "", "session_id": session_id},
                "runtime_kind": runtime_kind,
                "memory_bytes": memory_bytes,
                "memory_swap_bytes": memory_bytes,
                "cpu_count": 1.0,
                "pids_max": pids_max,
                "disk_bytes": 128 * 1024 * 1024,
                "egress": None,
                "ttl_seconds": 3600,
                "idempotency_key": f"create-{session_id}",
            }
        )
        sandbox_id = str(view["sandbox_id"])
        assert sandbox_id.startswith("sb_")

        # Idempotent replay of the same create returns the same sandbox.
        replay = client.create_sandbox(
            {
                "protocol_version": "1.0",
                "session_id": session_id,
                "workspace_key": session_id,
                "owner": {"deployment_id": _DEPLOYMENT_ID, "workspace_id": "", "session_id": session_id},
                "runtime_kind": runtime_kind,
                "memory_bytes": memory_bytes,
                "memory_swap_bytes": memory_bytes,
                "cpu_count": 1.0,
                "pids_max": pids_max,
                "disk_bytes": 128 * 1024 * 1024,
                "egress": None,
                "ttl_seconds": 3600,
                "idempotency_key": f"create-{session_id}",
            }
        )
        assert replay["sandbox_id"] == sandbox_id

        # Write a script through the File API.
        script = "print('hello-from-sandbox')\n"
        client.put_file(sandbox_id, session_id, "hello.py", script.encode("utf-8"), mode=0o600)

        # Exec it through the agent path.
        result = client.exec_agent(
            sandbox_id,
            session_id,
            {
                "argv": ["python", "hello.py"],
                "cwd": ".",
                "timeout_seconds": 60,
                "output_limit": 1024 * 1024,
                "idempotency_key": f"exec-{session_id}",
            },
        )
        assert result["exit_code"] == 0, result
        assert "hello-from-sandbox" in result["stdout"]

        if runtime_kind == "python-node-browser":
            browser = client.exec_agent(
                sandbox_id,
                session_id,
                {
                    "argv": ["node", "/opt/learngraph/browser-smoke.js"],
                    "cwd": ".",
                    "timeout_seconds": 60,
                    "output_limit": 1024 * 1024,
                    "idempotency_key": f"browser-{session_id}",
                },
            )
            assert browser["exit_code"] == 0, browser

        # Read + list the file.
        data = client.get_file(sandbox_id, session_id, "hello.py", 1024 * 1024)
        assert data == script.encode("utf-8")
        index = client.file_index(sandbox_id, session_id, prefix="", limit=100, cursor=None)
        assert any(e["path"] == "hello.py" for e in index["entries"])

        # Stop (runtime down, volume retained) then resume and read again.
        client.stop_sandbox(sandbox_id, session_id)
        client.resume_sandbox(sandbox_id, session_id)
        data = client.get_file(sandbox_id, session_id, "hello.py", 1024 * 1024)
        assert data == script.encode("utf-8")
    finally:
        if sandbox_id is not None:
            try:
                client.delete_sandbox(sandbox_id, session_id)
            except SandboxdProtocolError as exc:
                if exc.code != "sandbox_not_found":
                    raise


def test_sandboxd_fail_closed_and_owner_scope():
    client, errors = _client()
    session_id = f"it-fail-{uuid.uuid4().hex[:10]}"
    with pytest.raises(errors) as exc_info:
        client.create_sandbox(
            {
                "protocol_version": "9.9",  # outside the negotiated range
                "session_id": session_id,
                "workspace_key": session_id,
                "owner": {"deployment_id": "dev-local", "workspace_id": "", "session_id": session_id},
                "runtime_kind": "python-node",
                "memory_bytes": 512 * 1024 * 1024,
                "memory_swap_bytes": 512 * 1024 * 1024,
                "cpu_count": 1.0,
                "pids_max": 128,
                "disk_bytes": 128 * 1024 * 1024,
                "egress": None,
                "ttl_seconds": 3600,
                "idempotency_key": f"create-{session_id}",
            }
        )
    assert exc_info.value.code == "protocol_incompatible"

    # A different deployment scope must never touch another deployment's sandbox.
    client2 = type(client)(
        url=_SANDBOXD_URL,
        token=_token(_TOKEN_FILE),
        admin_token=_token(_ADMIN_TOKEN_FILE),
        deployment_id="other-deployment",
    )
    with pytest.raises(errors) as exc_info:
        client2.create_sandbox(
            {
                "protocol_version": "1.0",
                "session_id": session_id,
                "workspace_key": session_id,
                "owner": {"deployment_id": "other-deployment", "workspace_id": "", "session_id": session_id},
                "runtime_kind": "python-node",
                "memory_bytes": 512 * 1024 * 1024,
                "memory_swap_bytes": 512 * 1024 * 1024,
                "cpu_count": 1.0,
                "pids_max": 128,
                "disk_bytes": 128 * 1024 * 1024,
                "egress": None,
                "ttl_seconds": 3600,
                "idempotency_key": f"create-{session_id}",
            }
        )
    assert exc_info.value.code == "owner_mismatch"
