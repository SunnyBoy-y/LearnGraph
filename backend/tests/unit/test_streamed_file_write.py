from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.schemas.sandbox import SandboxAgentFileWriteRequest
from app.providers.ports.model import ProviderStreamEvent
from app.services.agent_runtime import AgentToolRuntime
from app.services.streamed_file_write import ContentDecoder, StreamedFileWrite


@pytest.mark.parametrize("size", [1, 2, 3, 7, 64])
def test_decoder_handles_every_escape_boundary_and_content_before_path(size):
    content = '中🙂\n"\\\t\b\f\r/control'
    raw = json.dumps({"content": content, "path": "work/a.txt"}, ensure_ascii=True)
    pieces = []
    decoder = ContentDecoder(pieces.append)
    for start in range(0, len(raw), size):
        decoder.feed(raw[start:start + size])
    assert "".join(pieces) == content


class LocalBackend:
    def __init__(self, root):
        self.root = root

    def write_agent_file(self, handle, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def delete_agent_file(self, handle, path):
        (self.root / path).unlink(missing_ok=True)

    def exec_agent(self, handle, argv, **kwargs):
        result = subprocess.run([sys.executable, *argv[1:]], cwd=self.root, capture_output=True, timeout=10)
        return SimpleNamespace(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr,
                               timed_out=False, truncated=False)


class LocalSandbox:
    def __init__(self, root):
        self.settings = SimpleNamespace(sandbox_agent_file_bytes=1048576)
        self.backend = LocalBackend(root)

    def _resolve_file_session(self, session_id, chat_id):
        return SimpleNamespace(id="sandbox"), "handle"

    def _pooling_enabled(self, session):
        return False

    def _runtime_backend(self, session):
        return self.backend

    def execute_agent_tool(self, name, arguments, *, chat_session_id, agent_authorized, streamed_file=None):
        payload = SandboxAgentFileWriteRequest(chat_session_id=chat_session_id, **arguments)
        _, chunks = streamed_file.materialize(payload)
        return {"status": "completed", "streamed": True, "stream_chunks": chunks}


def test_real_parameter_stream_arrives_before_completion_and_commits_once(tmp_path):
    sandbox = LocalSandbox(tmp_path)
    target = tmp_path / "work/main.py"
    target.parent.mkdir()
    target.write_text("OLD", encoding="utf-8")
    content = "# 中🙂\n" * 14000
    raw = json.dumps({"content": content, "path": "work/main.py"}, ensure_ascii=True)
    observed = []

    class Provider:
        def stream_chat(self, messages, **kwargs):
            for start in range(0, len(raw), 4096):
                if list(tmp_path.rglob("*.part")):
                    observed.append(start)
                    assert target.read_text(encoding="utf-8") == "OLD"
                yield ProviderStreamEvent("tool_call_delta", tool_calls=[{
                    "id": "call", "function": {"name": "sandbox_write_file", "arguments": raw[start:start + 4096]},
                }])
            yield ProviderStreamEvent("tool_calls", tool_calls=[{
                "id": "call", "function": {"name": "sandbox_write_file", "arguments": raw},
            }])
            yield ProviderStreamEvent("completed")

    runtime = object.__new__(AgentToolRuntime)
    runtime.sandbox, runtime.sandbox_authorized = sandbox, True
    runtime.settings = SimpleNamespace(storage_root=tmp_path)
    runtime.workspace_id, runtime.actor_id = "workspace", "actor"
    events = list(runtime.stream_chat(Provider(), [], chat_session_id="chat", tools=[{
        "function": {"name": "sandbox_write_file"},
    }]))
    assert observed and observed[0] < len(raw) - 4096
    call = next(e.tool_calls[0] for e in events if e.type == "tool_calls")
    result, meta, _ = runtime.execute(call, chat_session_id="chat", allowed_domains=[])
    assert meta["status"] == "completed" and json.loads(result)["streamed"]
    assert target.read_text(encoding="utf-8") == content
    assert not list(tmp_path.rglob("*.part"))
    assert not runtime._streamed_writes


def test_cancelled_stream_keeps_original_file_and_removes_parts(tmp_path):
    sandbox = LocalSandbox(tmp_path)
    target = tmp_path / "work/main.txt"
    target.parent.mkdir()
    target.write_text("OLD")

    class Provider:
        def stream_chat(self, messages, **kwargs):
            yield ProviderStreamEvent("tool_call_delta", tool_calls=[{
                "id": "call", "function": {"name": "sandbox_write_file", "arguments": '{"content":"' + "x" * 65536},
            }])
            raise RuntimeError("connection lost")

    runtime = object.__new__(AgentToolRuntime)
    runtime.sandbox, runtime.sandbox_authorized = sandbox, True
    with pytest.raises(RuntimeError):
        list(runtime.stream_chat(Provider(), [], chat_session_id="chat", tools=[{
            "function": {"name": "sandbox_write_file"},
        }]))
    assert target.read_text() == "OLD"
    assert not list(tmp_path.rglob("*.part"))
    assert not runtime._streamed_writes
