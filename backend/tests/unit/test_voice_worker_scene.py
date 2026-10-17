import json
from types import SimpleNamespace

from app.providers.ports.model import ProviderStreamEvent
from app.services.sandbox_agent_executor import execute_subagent_job


def run_scene_worker(*, max_rounds=5, voice=True, pause_after_publish=False, checkpoint=None):
    events = []
    snapshots = [
        {"block_id": "compare", "component": "table", "revision": revision,
         "state": "streaming" if revision == 0 else "completed", "props": {"title": "Methods"},
         "rows": [{"id": "a", "label": "A", "values": {"memory": 12}}]}
        for revision in (0, 1, 0)
    ]
    class Provider:
        provider_id = model_id = "local-scene-test"
        remote_capability = False
        last_usage = {}
        calls = int((checkpoint or {}).get("rounds_used") or 0)
        def stream_chat(self, messages, *, tools=None):
            names = [definition["function"]["name"] for definition in tools or []]
            assert ("voice_publish_scene" in names) is voice
            if voice:
                from jsonschema import Draft202012Validator
                schema = next(item["function"]["parameters"] for item in tools if item["function"]["name"] == "voice_publish_scene")
                Draft202012Validator(schema).validate({"snapshot": snapshots[0]})
            self.calls += 1
            if self.calls <= len(snapshots):
                yield ProviderStreamEvent(type="tool_calls", tool_calls=[{"id": f"call-{self.calls}", "function": {
                    "name": "voice_publish_scene", "arguments": json.dumps({"snapshot": snapshots[self.calls - 1]})}}])
            else:
                # The stale revision is reported to the worker, not published.
                if voice:
                    assert "revision must increase" in messages[-1].content
                yield ProviderStreamEvent(type="text_delta", content=json.dumps({
                    "summary": "Compared the supplied data", "artifacts": [], "evidence": [],
                    "acceptance": [{"criterion": "Comparison supplied", "status": "passed"}],
                    "limitations": ["Example only"],
                }))
            yield ProviderStreamEvent(type="completed")
    settings = SimpleNamespace(sandbox_subagent_max_rounds=max_rounds, sandbox_subagent_max_seconds=60,
                               sandbox_subagent_default_max_tokens=10000, sandbox_subagent_default_max_cost_usd=.15)
    spec = {"task_id": "task-1", "prompt": "Compare the supplied data", "role_key": "tool", "tools": [],
            "budget": {"max_rounds": max_rounds, "max_seconds": 60}}
    if checkpoint:
        spec["_resume_checkpoint"] = checkpoint
    job = SimpleNamespace(id="job-1", payload_json=spec, workspace_id="w", owner_user_id="u", chat_session_id="c",
                          attempt=1, status="RUNNING", cancel_requested=False)
    task = SimpleNamespace(id="task-1", spec_json={"origin": "voice"} if voice else {})
    def emit(kind, payload):
        events.append((kind, payload))
        if pause_after_publish and kind == "artifact.upsert":
            task.spec_json = {**task.spec_json, "voice_pause_requested": True}
    outcome = execute_subagent_job(settings, job, task, provider=Provider(), emit_event=emit)
    return outcome, events


def test_voice_worker_publishes_incremental_scenes_and_rejects_old_revisions():
    outcome, events = run_scene_worker()
    assert outcome.status == "SUCCEEDED"
    updates = [payload for kind, payload in events if kind == "artifact.upsert"]
    assert [item["snapshot"]["revision"] for item in updates] == [0, 1]
    assert outcome.deliverables["visuals"][0]["revision"] == 1


def test_budget_exhaustion_keeps_already_published_scene():
    outcome, events = run_scene_worker(max_rounds=1)
    assert outcome.status == "PARTIAL"
    assert outcome.deliverables["visuals"][0]["revision"] == 0
    assert any(kind == "artifact.upsert" for kind, _ in events)


def test_pause_resume_reuses_completed_tools_and_preserves_budget():
    paused, _ = run_scene_worker(pause_after_publish=True)
    assert paused.status == "PAUSED"
    checkpoint = paused.deliverables["_checkpoint"]
    assert checkpoint["tool_calls_total"] == 1
    assert checkpoint["rounds_used"] == 1
    assert checkpoint["messages"][-1]["role"] == "tool"
    resumed, events = run_scene_worker(checkpoint=checkpoint)
    assert resumed.status == "SUCCEEDED"
    assert [payload["snapshot"]["revision"] for kind, payload in events if kind == "artifact.upsert"] == [1]
    assert resumed.attempt_record["tool_calls"] == 3
    exhausted, _ = run_scene_worker(max_rounds=1, checkpoint=checkpoint)
    assert exhausted.status == "PARTIAL"


def test_resume_does_not_repeat_an_already_executed_file_write():
    task = SimpleNamespace(id="row-1", task_id="public-1", spec_json={"origin": "voice"})
    path = "work/subagents/row-1/outputs/result.txt"
    writes = []
    class Sandbox:
        def execute_agent_tool(self, name, arguments, **kwargs):
            writes.append((name, arguments["path"]))
            task.spec_json = {**task.spec_json, "voice_pause_requested": True}
            return {"status": "completed", "written": True}
        def list_files(self, request):
            return {"files": [{"path": path, "size_bytes": 3}]}
    class Provider:
        provider_id = model_id = "local-checkpoint-test"
        remote_capability = False
        last_usage = {}
        calls = 0
        def stream_chat(self, messages, *, tools=None):
            self.calls += 1
            assert "work/subagents/row-1" in messages[0].content
            if self.calls == 1:
                yield ProviderStreamEvent(type="tool_calls", tool_calls=[{"id": "write-once", "function": {
                    "name": "sandbox_write_file", "arguments": json.dumps({"path": path, "content": "one"})}}])
            else:
                assert messages[-1].role == "tool"
                assert '"written": true' in messages[-1].content
                yield ProviderStreamEvent(type="text_delta", content=json.dumps({"summary": "File created", "artifacts": [{"path": path}], "acceptance": [{"criterion": "Written", "status": "passed"}]}))
            yield ProviderStreamEvent(type="completed")
    settings = SimpleNamespace(sandbox_subagent_max_rounds=4, sandbox_subagent_max_seconds=60,
                               sandbox_subagent_default_max_tokens=10000, sandbox_subagent_default_max_cost_usd=.15)
    job = SimpleNamespace(id="job-1", workspace_id="w", owner_user_id="u", chat_session_id="c", attempt=1,
                          status="RUNNING", cancel_requested=False, payload_json={"task_id": task.id, "prompt": "Create the file once",
                          "role_key": "tool", "tools": ["sandbox_write_file"], "budget": {"max_rounds": 4, "max_seconds": 60}})
    provider, sandbox = Provider(), Sandbox()
    paused = execute_subagent_job(settings, job, task, provider=provider, sandbox_service=sandbox)
    assert paused.status == "PAUSED"
    task.spec_json.pop("voice_pause_requested")
    job.payload_json["_resume_checkpoint"] = paused.deliverables["_checkpoint"]
    resumed = execute_subagent_job(settings, job, task, provider=provider, sandbox_service=sandbox)
    assert resumed.status == "SUCCEEDED"
    assert writes == [("sandbox_write_file", path)]
