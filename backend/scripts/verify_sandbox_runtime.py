"""Real HTTP + SQLite + Docker acceptance for the sandbox lifecycle.

This script uses no route interception, fake provider, in-memory repository, or
host-process execution fallback. It starts the real FastAPI application against
an isolated on-disk database, drives public APIs, and inspects the actual Docker
containers created by those APIs.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import docker
import httpx


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    token: str | None = None,
    workspace_id: str | None = None,
    expected: int = 200,
    **kwargs,
):
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if workspace_id:
        headers["X-Workspace-ID"] = workspace_id
    response = client.request(method, path, headers=headers, **kwargs)
    if response.status_code != expected:
        raise AssertionError(
            f"{method} {path}: expected {expected}, got {response.status_code}: "
            f"{response.text[:1000]}"
        )
    return response.json()


def _wait_for_lifecycle(
    client: httpx.Client,
    token: str,
    workspace_id: str,
    session_id: str,
    state: str,
    timeout: float = 20,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = _request(
            client,
            "GET",
            f"/api/v1/sandbox/sessions/{session_id}",
            token=token,
            workspace_id=workspace_id,
        )
        if session["lifecycle_state"] == state:
            return session
        time.sleep(0.5)
    raise AssertionError(f"Sandbox session {session_id} did not reach {state}")


def main() -> int:
    backend_root = Path(__file__).resolve().parents[1]
    runtime_path = backend_root / "data" / "sandbox-runtime.json"
    if not runtime_path.is_file():
        raise SystemExit("Run scripts/bootstrap_sandbox_runtime.py first")
    runtime_config = json.loads(runtime_path.read_text(encoding="utf-8"))
    if not runtime_config.get("image_digest") or not runtime_config.get(
        "browser_image_digest"
    ):
        raise SystemExit("Both python-node and browser image digests are required")

    docker_client = docker.from_env()
    docker_client.ping()
    run_id = uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix="learngraph-sandbox-real-") as temp:
        root = Path(temp)
        (root / "sandbox-runtime.json").write_text(
            json.dumps(runtime_config), encoding="utf-8"
        )
        port = _free_port()
        env = os.environ.copy()
        env.update(
            {
                "LEARNGRAPH_ENV": "test",
                "LEARNGRAPH_DATABASE_URL": f"sqlite:///{(root / 'verify.db').as_posix()}",
                "LEARNGRAPH_STORAGE_ROOT": str(root / "storage"),
                "LEARNGRAPH_MEMORY_ROOT": str(root / "memory"),
                "LEARNGRAPH_SANDBOX_WORKSPACE_ROOT": str(root / "workspaces"),
                "LEARNGRAPH_SANDBOX_CONTAINER_IDLE_TTL_SECONDS": "5",
                "LEARNGRAPH_SANDBOX_CONTAINER_ABSOLUTE_TTL_SECONDS": "60",
                "LEARNGRAPH_SANDBOX_WORKSPACE_IDLE_TTL_SECONDS": "120",
                "LEARNGRAPH_SANDBOX_WORKSPACE_ABSOLUTE_TTL_SECONDS": "300",
                "LEARNGRAPH_SANDBOX_CLEANUP_INTERVAL_SECONDS": "1",
                "LEARNGRAPH_SANDBOX_MEMORY_BYTES": str(512 * 1024 * 1024),
                "LEARNGRAPH_SANDBOX_MEMORY_SWAP_BYTES": str(512 * 1024 * 1024),
                "LEARNGRAPH_SANDBOX_CPU_COUNT": "1",
                "LEARNGRAPH_SANDBOX_PIDS_MAX": "256",
                "LEARNGRAPH_SANDBOX_DISK_BYTES": str(1024 * 1024),
                "LEARNGRAPH_SANDBOX_WALL_TIME_SECONDS": "5",
                "LEARNGRAPH_SANDBOX_ACTIVE_PER_USER": "2",
                "LEARNGRAPH_SANDBOX_RETAINED_WORKSPACES_PER_USER": "10",
                "LEARNGRAPH_SANDBOX_HOST_MINIMUM_FREE_DISK_BYTES": "1",
                "LEARNGRAPH_MASTERY_EMBEDDED_SCHEDULER_ENABLED": "false",
                "LEARNGRAPH_MEMORY_RETENTION_SCHEDULER_ENABLED": "false",
                "LEARNGRAPH_ENABLE_DEMO_SEED": "false",
                "LEARNGRAPH_BOOTSTRAP_ADMIN_PASSWORD": f"Verify!{run_id}#Control",
            }
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=backend_root,
            env=env,
        )
        base_url = f"http://127.0.0.1:{port}"
        session_ids: list[str] = []
        try:
            with httpx.Client(base_url=base_url, timeout=30) as client:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        if client.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() >= deadline:
                        raise AssertionError("FastAPI did not become ready")
                    time.sleep(0.25)

                password = f"Sandbox!{run_id}#Pass"
                auth = _request(
                    client,
                    "POST",
                    "/api/v1/auth/register",
                    expected=201,
                    json={
                        "username": f"sandbox_{run_id}",
                        "display_name": "Sandbox Verification",
                        "password": password,
                    },
                )
                token = auth["access_token"]
                workspace_id = auth["default_workspace_id"]
                chat = _request(
                    client,
                    "POST",
                    "/api/v1/sessions",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={"title": f"sandbox-real-{run_id}"},
                )

                base_session = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/sessions",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": chat["id"],
                        "runtime": "python-node",
                    },
                )
                session_ids.append(base_session["id"])
                _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/files/write",
                    token=token,
                    workspace_id=workspace_id,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "path": "work/state.py",
                        "content": "print('cold-resume-ok')\n",
                    },
                )
                command = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/commands",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "argv": ["python", "work/state.py"],
                    },
                )
                assert command["status"] == "completed"
                assert "cold-resume-ok" in command["stdout_summary"]
                first_container = next(
                    item
                    for item in docker_client.containers.list()
                    if item.labels.get("com.learngraph.session_id")
                    == base_session["id"]
                )
                first_container.reload()
                host_config = first_container.attrs["HostConfig"]
                assert host_config["NetworkMode"] == "none"
                assert host_config["ReadonlyRootfs"] is True
                assert any(
                    item["Name"] == "fsize"
                    for item in host_config.get("Ulimits") or []
                )
                assert first_container.attrs["Config"]["User"] == "65532:65532"
                assert "ALL" in (host_config.get("CapDrop") or [])
                assert not (host_config.get("CapAdd") or [])
                assert any(
                    str(option).startswith("no-new-privileges")
                    for option in host_config.get("SecurityOpt") or []
                )
                mounts = first_container.attrs["Mounts"]
                assert len(mounts) == 1 and mounts[0]["Type"] == "bind"
                assert Path(mounts[0]["Source"]).resolve().is_relative_to(
                    (root / "workspaces").resolve()
                )
                first_container_id = first_container.id

                _wait_for_lifecycle(
                    client,
                    token,
                    workspace_id,
                    base_session["id"],
                    "COLD",
                )
                resumed = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/commands",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "argv": ["python", "work/state.py"],
                    },
                )
                assert resumed["status"] == "completed"
                assert "cold-resume-ok" in resumed["stdout_summary"]
                second_container = next(
                    item
                    for item in docker_client.containers.list()
                    if item.labels.get("com.learngraph.session_id")
                    == base_session["id"]
                )
                assert second_container.id != first_container_id

                browser_chat = _request(
                    client,
                    "POST",
                    "/api/v1/sessions",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={"title": f"browser-real-{run_id}"},
                )
                browser_script = (
                    "const { chromium } = require('playwright-core');\n"
                    "(async()=>{const b=await chromium.launch({"
                    "executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,"
                    "headless:true,chromiumSandbox:true});"
                    "const p=await b.newPage();"
                    "await p.setContent('<button id=\"ok\">ready</button>');"
                    "console.log(await p.textContent('#ok'));await b.close();})()"
                    ".catch(e=>{console.error(e);process.exit(1)});\n"
                )
                browser_session = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/sessions",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": browser_chat["id"],
                        "runtime": "python-node-browser",
                    },
                )
                session_ids.append(browser_session["id"])
                browser_container = next(
                    item
                    for item in docker_client.containers.list()
                    if item.labels.get("com.learngraph.session_id")
                    == browser_session["id"]
                )
                browser_container.reload()
                browser_host_config = browser_container.attrs["HostConfig"]
                assert "ALL" in (browser_host_config.get("CapDrop") or [])
                assert not (browser_host_config.get("CapAdd") or [])
                assert any(
                    str(option).startswith("no-new-privileges")
                    for option in browser_host_config.get("SecurityOpt") or []
                )
                _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/files/write",
                    token=token,
                    workspace_id=workspace_id,
                    json={
                        "chat_session_id": browser_chat["id"],
                        "sandbox_session_id": browser_session["id"],
                        "path": "work/browser.js",
                        "content": browser_script,
                    },
                )
                browser_command = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/commands",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": browser_chat["id"],
                        "sandbox_session_id": browser_session["id"],
                        "argv": ["node", "work/browser.js"],
                    },
                )
                assert browser_command["status"] == "completed", browser_command
                assert "ready" in browser_command["stdout_summary"]
                _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/commands",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "argv": ["python", "work/state.py"],
                    },
                )
                _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/commands",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": browser_chat["id"],
                        "sandbox_session_id": browser_session["id"],
                        "argv": ["node", "work/browser.js"],
                    },
                )

                third_chat = _request(
                    client,
                    "POST",
                    "/api/v1/sessions",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={"title": f"quota-real-{run_id}"},
                )
                quota_error = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/sessions",
                    token=token,
                    workspace_id=workspace_id,
                    expected=429,
                    json={
                        "chat_session_id": third_chat["id"],
                        "runtime": "python-node",
                    },
                )
                assert quota_error["error"]["code"] == "sandbox_user_concurrency_limit"

                _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/files/write",
                    token=token,
                    workspace_id=workspace_id,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "path": "work/keeper.txt",
                        "content": "must-survive-without-grant",
                    },
                )
                _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/files/write",
                    token=token,
                    workspace_id=workspace_id,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "path": "work/delete.py",
                        "content": (
                            "from pathlib import Path\n"
                            "Path('work/keeper.txt').unlink()\n"
                        ),
                    },
                )
                deletion_attempt = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/commands",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "argv": ["python", "work/delete.py"],
                    },
                )
                assert deletion_attempt["status"] == "failed"
                assert deletion_attempt["error_class"] == "sandbox_auth_required"
                live_base = next(
                    item
                    for item in docker_client.containers.list()
                    if item.labels.get("com.learngraph.session_id")
                    == base_session["id"]
                )
                live_base.reload()
                live_workspace = Path(
                    next(
                        mount["Source"]
                        for mount in live_base.attrs["Mounts"]
                        if mount["Destination"] == "/workspace"
                    )
                )
                assert (live_workspace / "work" / "keeper.txt").is_file()
                _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/files/write",
                    token=token,
                    workspace_id=workspace_id,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "path": "work/replace.py",
                        "content": (
                            "import os\n"
                            "from pathlib import Path\n"
                            "Path('work/replacement.tmp').write_text('replacement')\n"
                            "os.replace('work/replacement.tmp', 'work/keeper.txt')\n"
                        ),
                    },
                )
                replacement_attempt = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/commands",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "argv": ["python", "work/replace.py"],
                    },
                )
                assert replacement_attempt["status"] == "failed"
                assert replacement_attempt["error_class"] == "sandbox_auth_required"
                assert (
                    live_workspace / "work" / "keeper.txt"
                ).read_text(encoding="utf-8") == "must-survive-without-grant"
                _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/authorizations",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "path_prefix": "work/keeper.txt",
                        "reason": "real verification of code-mediated deletion",
                    },
                )
                granted_replacement = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/commands",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "argv": ["python", "work/replace.py"],
                    },
                )
                assert granted_replacement["status"] == "completed"
                assert (
                    live_workspace / "work" / "keeper.txt"
                ).read_text(encoding="utf-8") == "replacement"
                granted_deletion = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/commands",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "argv": ["python", "work/delete.py"],
                    },
                )
                assert granted_deletion["status"] == "completed"
                assert not (live_workspace / "work" / "keeper.txt").exists()

                _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/files/write",
                    token=token,
                    workspace_id=workspace_id,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "path": "work/timeout.py",
                        "content": "import time\ntime.sleep(7)\n",
                    },
                )
                timed_out = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/commands",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "argv": ["python", "work/timeout.py"],
                    },
                )
                assert timed_out["status"] == "failed"
                assert timed_out["error_class"] == "sandbox_timeout"
                cold_after_timeout = _request(
                    client,
                    "GET",
                    f"/api/v1/sandbox/sessions/{base_session['id']}",
                    token=token,
                    workspace_id=workspace_id,
                )
                assert cold_after_timeout["lifecycle_state"] == "COLD"
                assert not any(
                    item.labels.get("com.learngraph.session_id")
                    == base_session["id"]
                    for item in docker_client.containers.list()
                )

                _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/files/write",
                    token=token,
                    workspace_id=workspace_id,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "path": "work/fill.py",
                        "content": (
                            "from pathlib import Path\n"
                            "for i in range(8):\n"
                            "    Path(f'work/fill-{i}.bin').write_bytes(b'x' * 400000)\n"
                        ),
                    },
                )
                disk_limited = _request(
                    client,
                    "POST",
                    "/api/v1/sandbox/agent/commands",
                    token=token,
                    workspace_id=workspace_id,
                    expected=201,
                    json={
                        "chat_session_id": chat["id"],
                        "sandbox_session_id": base_session["id"],
                        "argv": ["python", "work/fill.py"],
                    },
                )
                assert disk_limited["status"] == "failed", disk_limited
                assert (
                    disk_limited["error_class"]
                    == "sandbox_workspace_quota_exceeded"
                ), disk_limited

                second_auth = _request(
                    client,
                    "POST",
                    "/api/v1/auth/register",
                    expected=201,
                    json={
                        "username": f"sandbox_other_{run_id}",
                        "display_name": "Sandbox Other",
                        "password": password + "2",
                    },
                )
                cross = _request(
                    client,
                    "GET",
                    "/api/v1/sandbox/sessions",
                    token=token,
                    workspace_id=second_auth["default_workspace_id"],
                    expected=403,
                )
                assert cross["error"]["code"] == "workspace_forbidden"

                for session_id in session_ids:
                    cleaned = _request(
                        client,
                        "POST",
                        f"/api/v1/sandbox/sessions/{session_id}/cleanup",
                        token=token,
                        workspace_id=workspace_id,
                    )
                    assert cleaned["lifecycle_state"] == "EXPIRED"
                assert not any((root / "workspaces").rglob("state.py"))
                print(
                    json.dumps(
                        {
                            "status": "passed",
                            "run_id": run_id,
                            "verified": [
                                "real_fastapi_http",
                                "real_sqlite",
                                "real_docker",
                                "managed_bind_mount",
                                "cold_resume",
                                "browser_playwright_chromium",
                                "user_concurrency_limit",
                                "aggregate_workspace_disk_quota",
                                "code_mediated_delete_grant",
                                "timeout_releases_runtime_reservation",
                                "cross_workspace_denial",
                                "formal_cleanup",
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            for container in docker_client.containers.list(all=True):
                if container.labels.get("com.learngraph.session_id") in session_ids:
                    container.remove(force=True)
    docker_client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
