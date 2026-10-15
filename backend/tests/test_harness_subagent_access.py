"""Regression tests for durable subagent ownership and lifecycle access."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import ChatSession, SandboxAgentEvent, SandboxAgentTask
from app.domain.schemas.sandbox import (
    SandboxAgentSubagentCancelRequest, SandboxAgentSubagentRetryRequest,
    SandboxAgentSubagentStatusRequest, SandboxAgentSubagentWaitRequest,
)
from app.services.sandbox import SandboxAgentWorkspaceService


class SubagentAccessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        for model in (ChatSession, SandboxAgentTask, SandboxAgentEvent):
            model.__table__.create(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.db.add_all([
            ChatSession(id="chat-a", workspace_id="workspace-a", title="A"),
            ChatSession(id="chat-b", workspace_id="workspace-a", title="B"),
            ChatSession(id="chat-c", workspace_id="workspace-b", title="C"),
        ])
        self.db.add_all([
            self.task("own", "workspace-a", "alice", "chat-a"),
            self.task("other-user", "workspace-a", "bob", "chat-a"),
            self.task("other-chat", "workspace-a", "alice", "chat-b"),
            self.task("other-workspace", "workspace-b", "alice", "chat-c"),
        ])
        self.db.commit()
        self.service = SandboxAgentWorkspaceService.__new__(SandboxAgentWorkspaceService)
        self.service.db = self.db
        self.service.actor_id = "alice"
        self.service.workspace_id = "workspace-a"
        self.service.workspace = None
        self.service.principal = None
        self.service.settings = SimpleNamespace(sandbox_subagent_queue_deadline_seconds=30)

    @staticmethod
    def task(task_id, workspace, owner, chat):
        return SandboxAgentTask(task_id=task_id, workspace_id=workspace,
                                owner_user_id=owner, chat_session_id=chat,
                                title=task_id, status="SUCCEEDED", result_text="private:" + task_id)

    def invoke(self, operation, task_id, chat="chat-a"):
        common = {"chat_session_id": chat}
        models = {"status": SandboxAgentSubagentStatusRequest,
                  "wait": SandboxAgentSubagentWaitRequest,
                  "cancel": SandboxAgentSubagentCancelRequest,
                  "retry": SandboxAgentSubagentRetryRequest}
        if operation == "wait":
            common.update(subagent_ids=[task_id], timeout_ms=1000)
        else:
            common["subagent_id"] = task_id
        return getattr(self.service, "toolkit_subagent_" + operation)(models[operation](**common))

    def test_owner_can_read_wait_and_cancel_terminal_task(self):
        self.assertEqual(self.invoke("status", "own")["result"], "private:own")
        self.assertEqual(self.invoke("wait", "own")["tasks"][0]["subagent_id"], "own")
        self.assertEqual(self.invoke("cancel", "own")["status"], "succeeded")

    def test_every_operation_rejects_foreign_or_missing_task(self):
        for operation in ("status", "wait", "cancel", "retry"):
            for task_id in ("other-user", "other-chat", "other-workspace", "missing"):
                with self.subTest(operation=operation, task=task_id):
                    with patch("app.services.sandbox_scheduler.SandboxSchedulerService") as scheduler:
                        with self.assertRaises(AppError) as caught:
                            self.invoke(operation, task_id)
                        self.assertEqual(caught.exception.status_code, 404)
                        self.assertEqual(caught.exception.code, "sandbox_subagent_not_found")
                        scheduler.assert_not_called()
        self.assertEqual(self.db.scalars(select(SandboxAgentEvent)).all(), [])

    def test_foreign_workspace_chat_rejected(self):
        for operation in ("status", "wait", "cancel", "retry"):
            with self.subTest(operation=operation), self.assertRaises(AppError) as caught:
                self.invoke(operation, "other-workspace", chat="chat-c")
            self.assertEqual(caught.exception.code, "session_not_found")

    def test_shared_task_identifier_does_not_cross_owners(self):
        self.db.add(self.task("own", "workspace-a", "bob", "chat-a"))
        self.db.commit()
        result = self.service._load_agent_task("own", chat_session_id="chat-a")
        self.assertEqual(result.owner_user_id, "alice")

    def test_cancel_failure_rolls_back_without_event_or_intent(self):
        task = self.service._load_agent_task("own", chat_session_id="chat-a")
        task.status = "RUNNING"
        task.latest_job_id = "job-a"
        self.db.commit()
        for stage in ("get_job", "cancel_job"):
            with self.subTest(stage=stage):
                with patch("app.services.sandbox_scheduler.SandboxSchedulerService") as factory:
                    factory.return_value.get_job.return_value = SimpleNamespace(
                        chat_session_id=task.chat_session_id, kind="subagent",
                        payload_json={"task_id": task.id},
                    )
                    getattr(factory.return_value, stage).side_effect = AppError(403, "denied", "Denied")
                    with self.assertRaises(AppError) as caught:
                        self.invoke("cancel", "own")
                    self.assertEqual(caught.exception.code, "denied")
                self.db.refresh(task)
                self.assertEqual(task.status, "RUNNING")
                self.assertIsNone(task.status_reason)
                self.assertNotIn("cancel_requested", task.spec_json)
                self.assertEqual(task.event_seq, 0)
        self.assertEqual(self.db.scalars(select(SandboxAgentEvent)).all(), [])

    def test_task_lookup_refreshes_another_session_commit(self):
        task = self.service._load_agent_task("own", chat_session_id="chat-a")
        with Session(self.engine) as other_db:
            other_task = other_db.get(SandboxAgentTask, task.id)
            other_task.status = "RUNNING"
            other_task.event_seq = 3
            other_db.commit()
        self.assertEqual(task.status, "SUCCEEDED")
        refreshed = self.service._load_agent_task("own", chat_session_id="chat-a")
        self.assertEqual(refreshed.status, "RUNNING")
        self.assertEqual(refreshed.event_seq, 3)

    def test_cancel_rejects_orphan_and_mismatched_job(self):
        task = self.service._load_agent_task("own", chat_session_id="chat-a")
        task.status = "RUNNING"
        self.db.commit()
        with self.assertRaises(AppError) as caught:
            self.invoke("cancel", "own")
        self.assertEqual(caught.exception.code, "subagent_job_missing")
        task.latest_job_id = "job-a"
        self.db.commit()
        baseline = dict(chat_session_id=task.chat_session_id, kind="subagent", payload_json={"task_id": task.id})
        for key, bad_value in (("chat_session_id", "chat-b"), ("kind", "agent_command"),
                               ("payload_json", {"task_id": "other"}), ("payload_json", [])):
            with self.subTest(key=key), patch("app.services.sandbox_scheduler.SandboxSchedulerService") as factory:
                factory.return_value.get_job.return_value = SimpleNamespace(**{**baseline, key: bad_value})
                with self.assertRaises(AppError) as caught:
                    self.invoke("cancel", "own")
                self.assertEqual(caught.exception.code, "subagent_job_mismatch")
                factory.return_value.cancel_job.assert_not_called()
                self.db.refresh(task)
                self.assertEqual(task.status, "RUNNING")
                self.assertEqual(task.event_seq, 0)
                self.assertNotIn("cancel_requested", task.spec_json)

    def test_owner_can_cancel_correctly_associated_job(self):
        task = self.service._load_agent_task("own", chat_session_id="chat-a")
        task.status = "RUNNING"
        task.latest_job_id = "job-a"
        self.db.commit()
        job = SimpleNamespace(chat_session_id=task.chat_session_id, kind="subagent",
                              payload_json={"task_id": task.id}, status="CANCELLED")
        with patch("app.services.sandbox_scheduler.SandboxSchedulerService") as factory:
            factory.return_value.get_job.return_value = job
            result = self.invoke("cancel", "own")
            factory.return_value.cancel_job.assert_called_once_with(job)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(task.event_seq, 1)

    def test_worker_rejects_job_task_owner_workspace_or_chat_mismatch(self):
        from app.services.sandbox_scheduler import SandboxSchedulerService
        from unittest.mock import Mock
        task = self.service._load_agent_task("own", chat_session_id="chat-a")
        scheduler = SandboxSchedulerService.__new__(SandboxSchedulerService)
        scheduler.db = self.db
        scheduler._finish = Mock()
        baseline = dict(workspace_id=task.workspace_id, owner_user_id=task.owner_user_id,
                        chat_session_id=task.chat_session_id, payload_json={"task_id": task.id})
        with patch("app.services.sandbox_agent_executor.execute_subagent_job") as execute:
            for field in ("workspace_id", "owner_user_id", "chat_session_id"):
                with self.subTest(field=field):
                    job = SimpleNamespace(**{**baseline, field: "other"})
                    scheduler._execute_subagent(job)
                    self.assertEqual(scheduler._finish.call_args.args[2], "invalid_job")
            execute.assert_not_called()
        self.assertEqual(scheduler._finish.call_count, 3)

    def test_owner_retry_submits_only_authorized_task(self):
        with patch("app.services.sandbox_scheduler.SandboxSchedulerService") as factory:
            factory.return_value.submit_job.return_value = SimpleNamespace(id="retry-job")
            self.invoke("retry", "own")
            kwargs = factory.return_value.submit_job.call_args.kwargs
            self.assertEqual(kwargs["owner_user_id"], "alice")
            self.assertEqual(kwargs["workspace_id"], "workspace-a")
            self.assertEqual(kwargs["chat_session_id"], "chat-a")
        self.assertEqual(self.invoke("status", "own")["status"], "queued")


if __name__ == "__main__":
    unittest.main()
