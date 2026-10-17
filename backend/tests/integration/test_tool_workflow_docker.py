"""Opt-in real Docker validation through isolated sandboxd and AgentToolRuntime.

No production database/daemon/credentials are used. Every created runner and
volume belongs to a random deployment and is deleted in finally.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("LEARNGRAPH_TEST_TOOL_WORKFLOW_DOCKER") != "1", reason="real Docker opt-in")]


@pytest.mark.parametrize("pooled", [False, True])
def test_tool_workflow_real_docker(tmp_path, monkeypatch, pooled):
    import docker
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "sandboxd"))
    from sandboxd.config import SandboxdConfig
    from sandboxd.main import create_app
    from app.core.config import get_settings
    from app.core.database import Base
    from app.domain.models import ChatSession, SandboxSession
    from app.providers.ports.model import ProviderStreamEvent
    from app.providers.remote.sandboxd_backend import SandboxdBackend
    from app.providers.remote.sandboxd_client import SandboxdClient
    from app.services.agent_runtime import AgentToolRuntime
    from app.services.mcp_skills import MCPAndSkillService
    from app.services.sandbox import SandboxAgentWorkspaceService

    deployment = "tool-workflow-" + uuid.uuid4().hex[:12]
    token = uuid.uuid4().hex
    docker_client = docker.from_env()
    image = docker_client.images.get(os.environ.get("LEARNGRAPH_TEST_TOOL_WORKFLOW_IMAGE", "learngraph-sandbox:reader-preview-verify")).id
    config = SandboxdConfig(listen_host="127.0.0.1", port=0, token=token, admin_token=token,
        state_path=str(tmp_path / "daemon.db"), deployment_id=deployment, docker_host=None,
        runtime_backend="docker", runtime_image=image, egress_network_enabled=False,
        egress_proxy_url=None, egress_proxy_container=None, max_request_bytes=16*1048576,
        max_file_bytes=8*1048576, max_stdout_bytes=2*1048576, max_active=4,
        workspace_uid="65532:65532", reconcile_on_start=False, reconcile_grace_seconds=0,
        ttl_sweep_interval_seconds=60, network_label="com.learngraph.sandbox",
        seccomp_dir=str(Path(__file__).resolve().parents[2] / "sandbox"))
    settings = get_settings().model_copy(update={
        "storage_root": tmp_path / "storage", "sandbox_workspace_root": str(tmp_path / "workspaces"),
        "sandbox_backend": "sandboxd", "sandbox_image": image, "sandbox_browser_image": image,
        "sandboxd_deployment_id": deployment,
        "sandbox_instance_pooling_enabled": pooled, "sandbox_egress_enabled": False,
        "sandbox_host_minimum_free_disk_bytes": 0, "sandbox_wall_time_seconds": 60,
    })
    engine = create_engine(f"sqlite:///{tmp_path / 'backend.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    application = create_app(config, start_workers=False)
    with TestClient(application) as transport, Session(engine) as db:
        client = SandboxdClient(url="http://testserver", token=token, admin_token=token,
                                deployment_id=deployment, client=transport)
        client.install_runtime("python-node", image)
        backend = SandboxdBackend(settings, client=client)
        import app.core.database as database_module
        from app.providers.sandbox_registry import get_sandbox_backend_registry
        monkeypatch.setattr(database_module, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
        registry = get_sandbox_backend_registry()
        monkeypatch.setattr(registry, "default", lambda *args, **kwargs: backend)
        monkeypatch.setattr(registry, "for_backend_id", lambda *args, **kwargs: backend)
        db.add(ChatSession(id="chat-tools", workspace_id="ws-tools", title="tool workflow validation"))
        db.commit()
        sandbox = SandboxAgentWorkspaceService(db, "ws-tools", "actor-tools", settings, backend=backend)
        monkeypatch.setattr(sandbox, "_runtime_backend", lambda session: backend)
        execution_errors = []
        original_execute = sandbox.execute_agent_tool
        def checked_execute(*args, **kwargs):
            try:
                return original_execute(*args, **kwargs)
            except Exception as exc:
                execution_errors.append(exc)
                raise
        monkeypatch.setattr(sandbox, "execute_agent_tool", checked_execute)
        extensions = MCPAndSkillService(db, "ws-tools", "actor-tools", settings)
        runtime = AgentToolRuntime(workspace_id="ws-tools", actor_id="actor-tools", search_provider=None,
            extensions=extensions, sandbox=sandbox, sandbox_authorized=True, settings=settings)
        calls = []

        def invoke(name, args):
            calls.append(name)
            content, meta, _ = runtime.execute({"id": uuid.uuid4().hex, "function": {
                "name": name, "arguments": json.dumps(args)}}, chat_session_id="chat-tools", allowed_domains=[])
            if meta.get("error_code") == "agent_tool_failed" and execution_errors:
                raise RuntimeError("Unexpected sandbox callback failure") from execution_errors[-1]
            return json.loads(content), meta

        try:
            written, meta = invoke("sandbox_write_file", {"path": "work/live.txt", "content": "OLD"})
            if meta["status"] != "completed":
                sandbox._resolve_file_session(None, "chat-tools")
            assert meta["status"] == "completed", written
            result, meta = invoke("sandbox_exec", {"command": "printf 'NEW' > work/live.txt; printf 'GENERATED\\nSECOND\\nTHIRD\\n' > work/generated.txt; cat work/live.txt"})
            assert meta["status"] == "completed" and result["stdout"] == "NEW", result
            read, _ = invoke("sandbox_read_file", {"path": "work/live.txt"})
            assert read["content"] == "NEW"
            batch, _ = invoke("sandbox_read_file", {"items": [
                {"path": "work/live.txt", "start_line": 1, "end_line": 1},
                {"path": "work/generated.txt", "start_line": 3, "end_line": 3},
            ]})
            assert [row["content"] for row in batch["files"]] == ["NEW", "THIRD\n"]
            found, _ = invoke("sandbox_grep", {"pattern": "GENERATED"})
            assert any(row["path"] == "work/generated.txt" for row in found["matches"])
            deleted, meta = invoke("sandbox_delete_file", {"path": "work/generated.txt"})
            assert meta["status"] == "completed" and deleted["deleted"], deleted
            missing, meta = invoke("sandbox_read_file", {"path": "work/generated.txt"})
            assert meta["status"] == "failed" and missing["error"] == "sandbox_file_unavailable", missing
            published = sandbox.publish_workspace_file(chat_session_id="chat-tools", path="work/live.txt",
                content=None, title=None, sandbox_session_id=written["sandbox_session_id"])
            assert sandbox.workspace_files.blobs.read_bytes(published["blob_sha256"]) == b"NEW"
            failure, meta = invoke("sandbox_exec", {"command": "python - <<'PY'\nimport sys\nprint('X'*150000)\nprint('IMPORTANT ERROR',file=sys.stderr)\nraise SystemExit(7)\nPY", "max_output_tokens": 1000})
            assert meta["status"] == "failed" and failure.get("exit_code") == 7, failure
            assert "IMPORTANT ERROR" in failure["stderr"]
            page, _ = invoke("sandbox_read_result", {"result_ref": failure["result_ref"], "field": "stdout", "max_chars": 16000})
            restored = page["content"]
            while page["next_cursor"]:
                page, _ = invoke("sandbox_read_result", {"result_ref": failure["result_ref"], "field": "stdout", "cursor": page["next_cursor"]})
                restored += page["content"]
            assert restored == "X"*150000 + "\n"

            text = "# streamed 中🙂\n" * 6000
            raw = json.dumps({"path": "work/streamed.py", "content": text}, ensure_ascii=True)
            observed = []

            class StreamingProvider:
                def stream_chat(self, messages, **kwargs):
                    for offset in range(0, len(raw), 12000):
                        if offset > len(raw)//2 and not observed:
                            session = db.get(SandboxSession, written["sandbox_session_id"])
                            entries = backend.list_files(sandbox._ensure_backend_session(session), 1000)
                            observed.extend(row.path for row in entries if row.path.endswith(".part"))
                        yield ProviderStreamEvent("tool_call_delta", tool_calls=[{"id": "stream-call", "function": {
                            "name": "sandbox_write_file", "arguments": raw[offset:offset+12000]}}])
                    yield ProviderStreamEvent("tool_calls", tool_calls=[{"id": "stream-call", "function": {
                        "name": "sandbox_write_file", "arguments": raw}}])
                    yield ProviderStreamEvent("completed")

            events = list(runtime.stream_chat(StreamingProvider(), [], chat_session_id="chat-tools",
                tools=[{"function": {"name": "sandbox_write_file"}}]))
            assert observed, "No file chunk reached the real sandbox before parameters completed"
            call = next(e.tool_calls[0] for e in events if e.type == "tool_calls")
            result_text, meta, _ = runtime.execute(call, chat_session_id="chat-tools", allowed_domains=[])
            assert meta["status"] == "completed", result_text
            assert json.loads(result_text)["streamed"]
            page, _ = invoke("sandbox_read_file", {"path": "work/streamed.py", "max_chars": 200000})
            if "result_ref" in page:
                page, _ = invoke("sandbox_read_result", {"result_ref": page["result_ref"], "field": "content"})
            session = db.get(SandboxSession, written["sandbox_session_id"])
            runtime_path = sandbox._container_path("chat-tools", "work/streamed.py") if pooled else "work/streamed.py"
            assert backend.read(sandbox._ensure_backend_session(session), runtime_path, 1048576).decode() == text
            inline_calls = len(calls)
            result, meta = invoke("sandbox_exec", {"command": "python - <<'PY'\nprint(sum(range(100)))\nPY"})
            assert meta["status"] == "completed" and result["stdout"].strip() == "4950"
            assert len(calls) - inline_calls == 1  # Previously write-script + exec.
            queued, meta = invoke("sandbox_exec", {"command": "python - <<'PY'\nimport time\ntime.sleep(0.5)\nprint('ASYNC-DONE')\nPY", "yield_time_ms": 0})
            assert queued.get("job_id"), queued
            completed, meta = invoke("sandbox_get_job", {"job_id": queued["job_id"], "wait_seconds": 10})
            assert completed["status"] == "SUCCEEDED" and completed["stdout"].strip() == "ASYNC-DONE", completed
            repeated, _ = invoke("sandbox_get_job", {"job_id": queued["job_id"], "output_cursor": completed["next_output_cursor"]})
            assert repeated["stdout"] == "" and repeated["stderr"] == ""
            live_model = os.environ.get("LEARNGRAPH_TEST_TOOL_WORKFLOW_LIVE_MODEL_CONTAINER")
            live_metrics = None
            if live_model and not pooled:
                import subprocess
                from dataclasses import asdict
                from app.providers.ports.model import ProviderChatMessage

                bridge = '''import os,json,sys
from pathlib import Path
from dataclasses import asdict
key=Path("/data/.master-key")
if key.exists(): os.environ["LEARNGRAPH_MASTER_KEY"]=key.read_text().strip()
from app.core.database import SessionLocal
from app.core.config import get_settings
from app.providers.factory import model_provider_for_workspace
from app.providers.ports.model import ProviderChatMessage
request=json.load(sys.stdin)
with SessionLocal() as db:
    provider=model_provider_for_workspace(db,"demo-workspace",get_settings(),thinking_mode="off")
    if not provider.available: raise RuntimeError("configured model unavailable")
    provider.max_output_tokens=1024
    messages=[ProviderChatMessage(**row) for row in request["messages"]]
    events=[asdict(e) for e in provider.stream_chat(messages,tools=request["tools"]) if e.type in {"text_delta","tool_calls","completed"}]
    print(json.dumps({"events":events,"model":provider.model_id,"usage":provider.last_usage},ensure_ascii=False))
'''
                definitions = [d for d in sandbox.agent_tool_definitions() if d["function"]["name"] == "sandbox_exec"]
                messages = [ProviderChatMessage(role="system", content="Use the supplied sandbox tool for the requested calculation. Use command for inline Python. Be concise."),
                    ProviderChatMessage(role="user", content="用沙箱里的 Python 精确计算 1 到 10000 的整数平方和，尽量少调用工具。完成后只输出整数。")]
                live_calls = []
                request_count = 0
                for _ in range(3):
                    request_count += 1
                    response = subprocess.run(["docker", "exec", "-i", live_model, "python", "-c", bridge],
                        input=json.dumps({"messages": [asdict(row) for row in messages], "tools": definitions}, ensure_ascii=False),
                        capture_output=True, text=True, encoding="utf-8", timeout=120)
                    assert response.returncode == 0, "Live-model request failed; inspect the configured Provider"
                    packet = json.loads(response.stdout)
                    tool_calls = [call for e in packet["events"] if e["type"] == "tool_calls" for call in e["tool_calls"]]
                    answer = "".join(e.get("content") or "" for e in packet["events"] if e["type"] == "text_delta")
                    if not tool_calls:
                        assert str(sum(i*i for i in range(1,10001))) in answer
                        break
                    messages.append(ProviderChatMessage(role="assistant", content=answer, tool_calls=tool_calls))
                    for call in tool_calls:
                        assert call["function"]["name"] == "sandbox_exec"
                        content, meta, _ = runtime.execute(call, chat_session_id="chat-tools", allowed_domains=[], disclosed_tool_names={"sandbox_exec"})
                        assert meta["status"] == "completed", content
                        live_calls.append(call["function"]["name"])
                        messages.append(ProviderChatMessage(role="tool", tool_call_id=call["id"], content=content))
                else:
                    pytest.fail("Live model failed to finish the task in three requests")
                assert len(live_calls) == 1
                live_metrics = {"model": packet["model"], "tool_calls": len(live_calls), "model_requests": request_count, "answer_correct": True}
            print(json.dumps({"real_docker": True, "pooled": pooled, "early_stream_parts": len(observed),
                "full_stdout_recovered": len(restored), "inline_tool_calls_before": 2, "inline_tool_calls_after": 1,
                "file_views": "exec/read/grep/publish/delete agree", "heterogeneous_ranges_one_call": True,
                "async_wait_and_output_cursor": "passed", "live_model": live_metrics}))
        finally:
            runtime.discard_streamed_writes()
            for record in application.state.store.list_sandboxes(deployment):
                if record.state != "DELETED":
                    client.delete_sandbox(record.sandbox_id, record.session_id)
            assert not docker_client.containers.list(all=True, filters={"label": f"com.learngraph.deployment_id={deployment}"})
            assert not docker_client.volumes.list(filters={"label": f"com.learngraph.deployment_id={deployment}"})
    engine.dispose()
    docker_client.close()
