from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.errors import AppError
from app.domain.schemas.sandbox import SandboxAgentCommandRequest
from app.services.agent_runtime import AgentToolRuntime
from app.services.tool_results import ToolResultStore, result_preview


def test_large_failure_preserves_stderr_and_complete_result(tmp_path):
    runtime = object.__new__(AgentToolRuntime)
    runtime.settings = SimpleNamespace(storage_root=tmp_path)
    runtime.workspace_id, runtime.actor_id = "ws", "actor"
    runtime._result_session_id, runtime._result_max_bytes = "chat", 4096
    stdout = "中文输出\n" * 40000
    content, meta, _ = runtime._failure("sandbox_command_failed", "failed", data={
        "status": "failed", "exit_code": 7, "stdout": stdout, "stderr": "IMPORTANT ERROR",
    })
    preview = json.loads(content)
    assert preview["exit_code"] == 7 and preview["stderr"] == "IMPORTANT ERROR"
    assert len(content.encode()) <= 4096
    store = ToolResultStore(tmp_path, "ws", "actor", "chat")
    cursor, restored = None, ""
    while True:
        page = store.read(preview["result_ref"], field="stdout", cursor=cursor, max_chars=10000)
        restored += page["content"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert restored == stdout
    assert meta["status"] == "failed"
    with pytest.raises(AppError) as exc:
        ToolResultStore(tmp_path, "ws", "other", "chat").read(preview["result_ref"])
    assert exc.value.code == "tool_result_not_found"
    with pytest.raises(AppError):
        ToolResultStore(tmp_path, "ws", "actor", "other-chat").read(preview["result_ref"])


@pytest.mark.parametrize("name", ["sandbox_bash", "sandbox_exec", "sandbox_git", "sandbox_get_job"])
def test_all_execution_failures_are_failed(name):
    runtime = object.__new__(AgentToolRuntime)
    runtime.sandbox = SimpleNamespace(execute_agent_tool=Mock(return_value={
        "status": "FAILED", "exit_code": 1, "stderr": "failure", "error_class": "sandbox_command_failed",
    }))
    runtime.sandbox_authorized = True
    _, meta, _ = runtime.execute({"function": {"name": name, "arguments": "{}"}},
                                  chat_session_id="chat", allowed_domains=[])
    assert meta["status"] == "failed"


def test_inline_execution_needs_one_call_and_canonicalizes_queued_payload():
    request = SandboxAgentCommandRequest(chat_session_id="chat", command="python - <<'PY'\nprint(42)\nPY")
    assert request.argv == ["bash", "-lc", "python - <<'PY'\nprint(42)\nPY"]
    assert SandboxAgentCommandRequest.model_validate(request.model_dump()).argv == request.argv
    with pytest.raises(ValueError):
        SandboxAgentCommandRequest(chat_session_id="chat", command="true", argv=["python", "a.py"])


def test_edit_uses_observed_version_without_manual_hash_copy():
    calls = []
    def execute(name, arguments, **kwargs):
        calls.append((name, arguments))
        if name == "sandbox_read_file":
            return {"path": "work/a.py", "content": "old", "sha256": "a" * 64}
        assert arguments["expected_sha256"] == "a" * 64
        return {"path": "work/a.py", "sha256": "b" * 64}
    runtime = object.__new__(AgentToolRuntime)
    runtime.sandbox = SimpleNamespace(execute_agent_tool=execute)
    runtime.sandbox_authorized = True
    for name, args in [("sandbox_read_file", {"path": "work/a.py"}),
                       ("sandbox_edit_file", {"path": "work/a.py", "old_string": "old", "new_string": "new"})]:
        _, meta, _ = runtime.execute({"function": {"name": name, "arguments": json.dumps(args)}}, chat_session_id="chat", allowed_domains=[])
        assert meta["status"] == "completed"
    assert len(calls) == 2
    _, meta, _ = runtime.execute({"function": {"name": "sandbox_edit_file", "arguments": json.dumps({"path": "work/a.py"})}},
        chat_session_id="other-chat", allowed_domains=[])
    assert meta["error_code"] == "sandbox_edit_read_required"


def test_non_ascii_preview_keeps_control_fields():
    preview = result_preview({"status": "failed", "exit_code": 2, "stdout": "中" * 20000,
                              "stderr": "错" * 10000}, "result_" + "a" * 32, 4096)
    value = json.loads(preview)
    assert len(preview.encode()) <= 4096
    assert value["exit_code"] == 2 and value["stderr"]


def test_runtime_catalog_reduces_schemas_and_specialists_can_be_activated():
    from app.core.config import get_settings
    from app.services.mcp_skills import MCPAndSkillService
    from app.services.sandbox import SandboxAgentWorkspaceService

    extensions = SimpleNamespace(db=Mock(), agent_tool_definitions=lambda **kw: [
        MCPAndSkillService._capability_search_tool_definition(),
        MCPAndSkillService._capability_activate_tool_definition(),
    ])
    runtime = AgentToolRuntime(workspace_id="ws", actor_id="actor", search_provider=None,
        extensions=extensions, sandbox=SimpleNamespace(agent_tool_definitions=SandboxAgentWorkspaceService.agent_tool_definitions),
        sandbox_authorized=True, settings=get_settings(), can_manage_providers=True)
    full = runtime.definitions(agent_mode_enabled=True, web_search_enabled=False)
    compact = runtime.definitions(agent_mode_enabled=True, web_search_enabled=False, capability_families=set(), activated_capabilities=set())
    names = {d["function"]["name"] for d in compact}
    assert {"sandbox_exec", "sandbox_read_result", "sandbox_write_file", "lg_capability_search"} <= names
    assert {"create_provider", "sandbox_bash", "sandbox_pipeline"}.isdisjoint(names)
    assert len(json.dumps(compact)) < len(json.dumps(full)) * 0.7
    assert all("sandbox_session_id" not in d["function"]["parameters"].get("properties", {})
               for d in compact if d["function"]["name"].startswith("sandbox_"))
    service = object.__new__(MCPAndSkillService)
    service.runtime_tool_descriptors = extensions.runtime_tool_descriptors
    service._authorized_packages = lambda: []
    service._authorized_declarative_skills = lambda: []
    service.list_servers = lambda: []
    service._builtin_descriptor = lambda *args: {"capability_id": "x", "name": "x", "family": "builtin_extension", "kind": "builtin_tool"}
    result = service.search_capabilities("create_provider")
    assert result["results"][0]["descriptor"]["capability_id"] == "runtime:create_provider"
    activated = service.activate_capabilities(["runtime:create_provider"])
    visible = runtime.definitions(agent_mode_enabled=True, web_search_enabled=False, capability_families=set(),
        activated_capabilities=set(activated["activated_capability_ids"]))
    assert "create_provider" in {d["function"]["name"] for d in visible}
    print(json.dumps({"catalog_unfiltered_tools": len(full), "catalog_default_tools": len(compact),
        "catalog_unfiltered_bytes": len(json.dumps(full).encode()),
        "catalog_default_bytes": len(json.dumps(compact).encode()), "specialist_activation": "passed"}))
