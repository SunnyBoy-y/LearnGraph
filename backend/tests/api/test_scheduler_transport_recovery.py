import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.models import SandboxAgentEvent, SandboxAgentTask, SandboxJob
from app.providers.ports.model import ProviderStreamEvent
from app.providers.remote.openai import ProviderTransportError
from app.services import sandbox_agent_executor as executor
from app.services.sandbox_scheduler import MAX_JOB_ATTEMPTS, SandboxSchedulerService


@pytest.mark.parametrize("mode", ["recover", "exhaust", "cancel"])
def test_real_scheduler_persists_checkpoint_retries_boundedly_and_honors_cancel(
    client, register_user, auth_headers, monkeypatch, mode
):
    token, workspace, *_ = register_user("recovery")
    headers = auth_headers(token, workspace)
    chat = client.post("/api/v1/sessions", headers=headers, json={"title": "recovery"}).json()["id"]
    class Provider:
        provider_id = model_id = "recovery-unit"
        remote_capability = False
        last_usage = {}
        calls = 0
        def stream_chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1 or mode != "recover":
                raise ProviderTransportError("DeepSeek stream disconnected")
            yield ProviderStreamEvent("text_delta", content=json.dumps({
                "summary": "Recovered", "artifacts": [], "acceptance": [{"criterion": "Answered", "status": "passed"}]
            }))
            yield ProviderStreamEvent("completed")
    provider, real_execute = Provider(), executor.execute_subagent_job
    monkeypatch.setattr(executor, "execute_subagent_job", lambda settings, job, task, **kwargs:
                        real_execute(settings, job, task, provider=provider, **kwargs))
    settings = get_settings()
    with SessionLocal() as db:
        task = SandboxAgentTask(workspace_id=workspace, owner_user_id="test-recovery", chat_session_id=chat,
                                task_id="recovery-task", role_key="reason", spec_json={})
        db.add(task)
        db.flush()
        job = SandboxJob(workspace_id=workspace, owner_user_id=task.owner_user_id, chat_session_id=chat,
                         kind="subagent", status="STARTING", attempt=1, payload_json={
                             "task_id": task.id, "prompt": "Answer", "role_key": "reason",
                             "budget": {"max_rounds": 2, "max_seconds": 60},
                         })
        db.add(job)
        db.commit()
        job_id, task_id = job.id, task.id
        SandboxSchedulerService(db, settings)._execute_subagent(job)
        assert job.status == task.status == "QUEUED"
        assert task.finished_at is None and job.finished_at is None
        assert job.payload_json["_resume_checkpoint"]["messages"][-1]["role"] == "user"
        events = list(db.scalars(select(SandboxAgentEvent).where(SandboxAgentEvent.task_id == task.id)))
        assert any(event.event_type == "retry_scheduled" for event in events)
        assert not any(event.event_type == "failed" for event in events)
    # A fresh DB session represents another worker picking up the same durable job.
    with SessionLocal() as db:
        job, task = db.get(SandboxJob, job_id), db.get(SandboxAgentTask, task_id)
        if mode == "cancel":
            job.cancel_requested = True
            db.commit()
            SandboxSchedulerService(db, settings)._run_job(job)
            assert job.status == task.status == "CANCELLED"
            assert provider.calls == 1
            return
        for attempt in range(2, MAX_JOB_ATTEMPTS + 1):
            job.status, job.attempt = "STARTING", attempt
            db.commit()
            SandboxSchedulerService(db, settings)._execute_subagent(job)
            if job.status != "QUEUED":
                break
        expected = "SUCCEEDED" if mode == "recover" else "FAILED"
        assert job.status == task.status == expected
        assert len(task.attempts_json) == (2 if mode == "recover" else MAX_JOB_ATTEMPTS)
        assert provider.calls == len(task.attempts_json)
        assert task.finished_at is not None
        if mode == "recover":
            assert job.reason is None
            assert job.error_class is None
            assert job.error_message is None
            assert task.attempts_json[0]["error_class"] == "ProviderTransportError"
