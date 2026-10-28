from copy import deepcopy

import pytest

from app.providers.ports.model import ProviderStreamEvent
from app.providers.remote.openai import ProviderHTTPError, ProviderResponseError, ProviderTransportError
from app.services.sandbox_agent_executor import execute_subagent_job
from test_sandbox_agent_executor import FakeProvider, FakeSandbox, _call, _done, _handoff_text, _job, _payload, _settings, _task, _text, _tool_calls


class RecoveryProvider(FakeProvider):
    def __init__(self, script):
        super().__init__(script)
        self.histories = []

    def stream_chat(self, messages, **kwargs):
        self.histories.append(deepcopy(messages))
        events = self.script[self.calls]
        self.calls += 1
        for event in events:
            if isinstance(event, Exception):
                raise event
            yield event


def disconnect():
    return ProviderTransportError("DeepSeek chat stream transport failed (RemoteProtocolError)")


def test_retry_resumes_history_without_repeating_completed_write():
    call = _call("write-once", "sandbox_append_file", {"path": "work/subagents/task_pk_1/outputs/x.css", "content": "one"})
    provider = RecoveryProvider([
        [ProviderStreamEvent("reasoning_delta", content="provider reasoning state"), _tool_calls([call]), _done()],
        [_text("discard this incomplete answer"), disconnect()],
        [_text(_handoff_text()), _done()],
    ])
    sandbox, checkpoints = FakeSandbox(), []
    job, task = _job(_payload(budget={"max_rounds": 2, "max_seconds": 60})), _task()
    first = execute_subagent_job(_settings(), job, task, provider=provider, sandbox_service=sandbox,
                                 save_checkpoint=lambda value: checkpoints.append(deepcopy(value)))
    assert first.status == "RETRYING"
    saved = first.deliverables["_checkpoint"]
    assert saved == checkpoints[-1]
    assert saved["rounds_used"] == 1
    assert saved["messages"][-1]["role"] == "tool"
    assert saved["messages"][-2]["reasoning_content"] == "provider reasoning state"
    job.payload_json["_resume_checkpoint"], job.attempt = saved, 2
    second = execute_subagent_job(_settings(), job, task, provider=provider, sandbox_service=sandbox)
    assert second.status == "SUCCEEDED"
    assert provider.histories[1][1:] == provider.histories[2][1:]
    assert len(sandbox.calls) == 1
    assert second.attempt_record["rounds"] == 2
    assert second.attempt_record["tool_calls"] == 1


def test_truncated_tool_batch_never_dispatches_partial_tool_calls():
    call = _call("incomplete", "sandbox_append_file", {"path": "work/subagents/task_pk_1/a", "content": "x"})
    provider = RecoveryProvider([[_tool_calls([call]), disconnect()]])
    sandbox = FakeSandbox()
    outcome = execute_subagent_job(_settings(), _job(_payload()), _task(), provider=provider, sandbox_service=sandbox)
    assert outcome.status == "RETRYING"
    assert sandbox.calls == []
    assert len(outcome.deliverables["_checkpoint"]["messages"]) == 2


class WorkerStopped(BaseException):
    pass


def test_worker_restart_resumes_only_unfinished_suffix_of_tool_batch():
    calls = [_call(name, "sandbox_append_file", {"path": "work/subagents/task_pk_1/a", "content": name}) for name in ("one", "two")]
    first = RecoveryProvider([[_tool_calls(calls), _done()]])
    sandbox, saved = FakeSandbox(), {}
    def commit(value):
        saved.clear()
        saved.update(deepcopy(value))
        if value["tool_calls_total"] == 1 and not value["inflight_call_id"]:
            raise WorkerStopped()
    with pytest.raises(WorkerStopped):
        execute_subagent_job(_settings(), _job(_payload()), _task(), provider=first, sandbox_service=sandbox, save_checkpoint=commit)
    second = RecoveryProvider([[_text(_handoff_text()), _done()]])
    outcome = execute_subagent_job(_settings(), _job(_payload(_resume_checkpoint=saved), attempt=2), _task(), provider=second, sandbox_service=sandbox)
    assert outcome.status == "SUCCEEDED"
    assert [args["content"] for _, args in sandbox.calls] == ["one", "two"]
    assert second.calls == 1
    assert outcome.attempt_record["rounds"] == 2


def test_uncertain_side_effect_is_not_automatically_replayed():
    saved, sandbox = {}, FakeSandbox()
    def commit(value):
        if value["tool_calls_total"]:
            raise WorkerStopped()
        saved.clear()
        saved.update(deepcopy(value))
    call = _call("once", "sandbox_append_file", {"path": "work/subagents/task_pk_1/a", "content": "one"})
    with pytest.raises(WorkerStopped):
        execute_subagent_job(_settings(), _job(_payload()), _task(), provider=RecoveryProvider([[_tool_calls([call]), _done()]]),
                             sandbox_service=sandbox, save_checkpoint=commit)
    assert saved["inflight_call_id"] == "once"
    provider = RecoveryProvider([])
    outcome = execute_subagent_job(_settings(), _job(_payload(_resume_checkpoint=saved)), _task(), provider=provider, sandbox_service=sandbox)
    assert outcome.status == "FAILED"
    assert outcome.error_class == "UncertainToolExecution"
    assert len(sandbox.calls) == 1 and provider.calls == 0


def test_completed_tool_identity_is_deduplicated_across_retry():
    call = _call("stable-id", "sandbox_append_file", {"path": "work/subagents/task_pk_1/a", "content": "one"})
    provider = RecoveryProvider([[_tool_calls([call]), _done()], [disconnect()], [_tool_calls([call]), _done()], [_text(_handoff_text()), _done()]])
    sandbox, job = FakeSandbox(), _job(_payload())
    first = execute_subagent_job(_settings(), job, _task(), provider=provider, sandbox_service=sandbox)
    job.payload_json["_resume_checkpoint"] = first.deliverables["_checkpoint"]
    second = execute_subagent_job(_settings(), job, _task(), provider=provider, sandbox_service=sandbox)
    assert second.status == "SUCCEEDED"
    assert len(sandbox.calls) == 1


@pytest.mark.parametrize("error", [ProviderHTTPError("bad key", status_code=401), ProviderHTTPError("bad request", status_code=400), ProviderResponseError("bad schema")])
def test_permanent_errors_do_not_schedule_retry(error):
    provider = RecoveryProvider([[error]])
    result = execute_subagent_job(_settings(), _job(_payload()), _task(), provider=provider, sandbox_service=FakeSandbox())
    assert result.status == "FAILED"
    assert provider.calls == 1


def test_retry_keeps_token_usage_without_enforcing_historical_budget():
    provider = RecoveryProvider([[disconnect()]])
    provider.last_usage = {"input_tokens": 1000, "output_tokens": 10}
    job = _job(_payload(budget={"max_rounds": 3, "max_seconds": 60, "max_tokens": 1000}))
    first = execute_subagent_job(_settings(), job, _task(), provider=provider, sandbox_service=FakeSandbox())
    assert first.status == "RETRYING"
    assert first.deliverables["_checkpoint"]["used_tokens"] == 1010
    job.payload_json["_resume_checkpoint"] = first.deliverables["_checkpoint"]
    next_provider = RecoveryProvider([[_text(_handoff_text()), _done()]])
    second = execute_subagent_job(_settings(), job, _task(), provider=next_provider, sandbox_service=FakeSandbox())
    assert second.status == "SUCCEEDED"
    assert second.attempt_record["token_usage"] >= 1010
    assert next_provider.calls == 1
