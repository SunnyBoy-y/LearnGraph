"""Focused service regression tests; no live services or production data."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import SandboxAgentTask, SandboxJob
from app.domain.schemas.sandbox import SandboxAgentSubagentRequest
from app.services.mcp_skills import MCPAndSkillService, SKILL_READ_MAX_CONTENT_CHARS
from app.services.sandbox_scheduler import SandboxSchedulerService
import test_harness_subagent_access as access


class SubagentCreationTests(unittest.TestCase):
    task = staticmethod(access.SubagentAccessTests.task)

    def setUp(self):
        access.SubagentAccessTests.setUp(self)
        self.service.settings = SimpleNamespace(
            sandbox_subagent_enabled=True, sandbox_subagent_max_rounds=12,
            sandbox_subagent_max_seconds=300, sandbox_subagent_default_max_tokens=20000,
            sandbox_subagent_default_max_cost_usd=1.0, sandbox_subagent_queue_deadline_seconds=30,
        )

    def test_reserved_context_wins_over_caller_input(self):
        request = SandboxAgentSubagentRequest(
            chat_session_id="chat-a", prompt="Review", tools=[],
            context_snapshot={"workspace_id": "evil", "user_id": "bob",
                              "chat_session_id": "chat-b", "requirement_version": 999,
                              "voice_session_id": "spoof", "turn_id": "spoof",
                              "requirement_group_id": "spoof", "context_note": "preserve"},
        )
        with patch("app.services.sandbox_scheduler.SandboxSchedulerService") as factory:
            factory.return_value.submit_job.return_value = SimpleNamespace(id="new-job")
            result = self.service.toolkit_subagent(request)
        task = self.db.scalar(select(SandboxAgentTask).where(SandboxAgentTask.task_id == result["task_id"]))
        context = task.spec_json["input_snapshot"]
        for key, value in {"workspace_id": "workspace-a", "user_id": "alice",
                           "chat_session_id": "chat-a", "requirement_version": 1,
                           "voice_session_id": None, "turn_id": None,
                           "requirement_group_id": result["task_id"], "context_note": "preserve"}.items():
            self.assertEqual(context[key], value)

    def test_idempotency_replay_cannot_return_another_chat(self):
        with patch("app.services.sandbox_scheduler.SandboxSchedulerService") as factory:
            factory.return_value.submit_job.return_value = SimpleNamespace(id="new-job")
            original = SandboxAgentSubagentRequest(chat_session_id="chat-a", prompt="Review", tools=[], idempotency_key="stable-key")
            first = self.service.toolkit_subagent(original)
            again = self.service.toolkit_subagent(original)
            self.assertEqual(first["task_id"], again["task_id"])
            self.assertTrue(again["idempotent_replay"])
            with self.assertRaises(AppError) as caught:
                self.service.toolkit_subagent(original.model_copy(update={"chat_session_id": "chat-b"}))
            self.assertEqual(caught.exception.code, "subagent_idempotency_conflict")
            self.assertEqual(factory.return_value.submit_job.call_count, 1)


class SchedulerQuotaTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        SandboxJob.__table__.create(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.scheduler = SandboxSchedulerService.__new__(SandboxSchedulerService)
        self.scheduler.db = self.db
        self.scheduler.settings = SimpleNamespace(
            sandbox_subagent_max_concurrent_chat=1, sandbox_subagent_max_concurrent_user=1,
            sandbox_subagent_max_concurrent_workspace=1,
        )
        self.current = self.add_job("current", "STARTING")

    def add_job(self, name, status, **kwargs):
        fields = dict(id=name, workspace_id="ws", owner_user_id="owner",
                      chat_session_id="chat", kind="subagent", status=status)
        fields.update(kwargs)
        job = SandboxJob(**fields)
        self.db.add(job)
        self.db.commit()
        return job

    def test_queued_tasks_and_self_do_not_fill_execution_quota(self):
        for number in range(4):
            self.add_job(f"queued-{number}", "QUEUED")
        self.add_job("done", "SUCCEEDED")
        self.add_job("command", "RUNNING", kind="agent_command")
        self.assertEqual(self.scheduler._subagent_active_count(exclude_job_id="current"), 0)
        self.assertIsNone(self.scheduler._subagent_quota_exceeded(self.current))

    def test_other_starting_or_running_task_enforces_all_three_limits(self):
        other = self.add_job("other", "STARTING")
        self.assertEqual(self.scheduler._subagent_quota_exceeded(self.current), "subagent_chat_quota")
        other.status = "RUNNING"
        self.db.commit()
        self.scheduler.settings.sandbox_subagent_max_concurrent_chat = 0
        self.assertEqual(self.scheduler._subagent_quota_exceeded(self.current), "subagent_user_quota")
        self.scheduler.settings.sandbox_subagent_max_concurrent_user = 0
        self.assertEqual(self.scheduler._subagent_quota_exceeded(self.current), "subagent_workspace_quota")


class SkillReadPagingTests(unittest.TestCase):
    def setUp(self):
        self.service = MCPAndSkillService.__new__(MCPAndSkillService)
        self.service.db = MagicMock()
        self.service.audit = Mock()
        self.service.workspace_id = "ws"
        self.service.actor_id = "owner"
        self.service.settings = SimpleNamespace()
        self.skill = SimpleNamespace(id="skill-id", skill_key="sample", name="Sample", enabled=True, status="enabled")
        self.service.list_skills = Mock(return_value=[self.skill])
        self.service._is_agent_skill_package = Mock(return_value=True)
        self.service._skill_authorization_hash = Mock(return_value="auth-hash")
        self.service._usable_grant = Mock(return_value=SimpleNamespace(id="grant", decision="always"))
        def create(**kw):
            self.invocation = SimpleNamespace(id="invocation", target_type=kw["target_type"],
                target_id=kw["target_id"], tool_name=kw["tool_name"], input_json=kw["input_json"], input_size_bytes=kw["input_size"])
            return self.invocation
        self.service._create_invocation = Mock(side_effect=create)
        self.service.db.scalar.return_value = SimpleNamespace(blob_sha256="blob")
        self.service.db.scalars.return_value.all.return_value = [SimpleNamespace(relative_path="SKILL.md")]
        self.blob_patch = patch("app.services.session_workspace.BlobStore")
        self.blob = self.blob_patch.start().return_value
        self.addCleanup(self.blob_patch.stop)
        self.content = "中文🙂\n" + "longline" * 35 + "\nend"
        self.blob.read_bytes.return_value = self.content.encode()

    def read(self, **kwargs):
        return self.service.read_skill_package_file({"skill_key": "sample", **kwargs}).result_json

    def test_service_pages_reassemble_exactly_and_record_page_size(self):
        chunks, cursor = [], None
        for _ in range(100):
            page = self.read(max_chars=13, cursor=cursor)
            chunks.append(page["content"])
            self.assertLessEqual(len(page["content"]), 13)
            self.assertEqual(self.service.audit.record.call_args.kwargs["details"]["content_chars"], len(page["content"]))
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        else:
            self.fail("Pagination did not terminate")
        self.assertEqual("".join(chunks), self.content)

    def test_changed_content_and_other_file_reject_old_cursor(self):
        cursor = self.read(max_chars=7)["next_cursor"]
        for kwargs in ({"path": "references/other.md"}, {}):
            if not kwargs:
                self.blob.read_bytes.return_value = b"changed"
            with self.subTest(kwargs=kwargs), self.assertRaises(AppError) as caught:
                self.read(cursor=cursor, **kwargs)
            self.assertEqual(caught.exception.code, "read_version_changed")
            self.assertEqual(self.invocation.status, "failed")

    def test_bad_page_arguments_record_structured_failure(self):
        for value in (0, True, "10", SKILL_READ_MAX_CONTENT_CHARS + 1):
            with self.subTest(value=value), self.assertRaises(AppError) as caught:
                self.read(max_chars=value)
            self.assertEqual(caught.exception.code, "invalid_page_size")
        with self.assertRaises(AppError) as caught:
            self.read(cursor=42)
        self.assertEqual(caught.exception.code, "invalid_read_cursor")

    def test_grant_revalidated_for_each_page(self):
        cursor = self.read(max_chars=7)["next_cursor"]
        self.service._usable_grant.return_value = None
        self.blob.read_bytes.reset_mock()
        with self.assertRaises(AppError) as caught:
            self.read(cursor=cursor)
        self.assertEqual(caught.exception.status_code, 403)
        self.blob.read_bytes.assert_not_called()

    def test_deeply_nested_invalid_cursor_records_failure(self):
        import base64
        cursor = base64.urlsafe_b64encode(b"[" * 1200).decode()
        self.assertLessEqual(len(cursor), 2048)
        with self.assertRaises(AppError) as caught:
            self.read(cursor=cursor)
        self.assertEqual(caught.exception.code, "invalid_read_cursor")
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(self.invocation.status, "failed")

    def test_dispatch_and_final_model_json_keep_cursor_within_byte_budget(self):
        import json
        from app.services.agent_runtime import AgentToolRuntime, AGENT_TOOL_RESULT_MAX_BYTES
        from app.services.mcp_skills import SKILL_READ_FUNCTION_NAME
        runtime = AgentToolRuntime.__new__(AgentToolRuntime)
        samples = ["中文" * 40000, "🙂" * 60000, ('"' + chr(92) + chr(10) + chr(0)) * 22000]
        self.service.db.scalars.return_value.all.return_value = [
            SimpleNamespace(relative_path=f"references/{n}/" + "🙂" * 480)
            for n in range(64)
        ]
        for original in samples:
            self.blob.read_bytes.return_value = original.encode()
            chunks, cursor = [], None
            for _ in range(100):
                data = self.service.invoke_agent_function(SKILL_READ_FUNCTION_NAME, {
                    "skill_key": "sample", "max_chars": 48000, "cursor": cursor,
                })
                encoded, meta, _ = runtime._success(data, {}, [])
                self.assertLessEqual(len(encoded.encode()), AGENT_TOOL_RESULT_MAX_BYTES)
                self.assertFalse(meta["result_truncated"])
                result = json.loads(encoded)["result"]
                chunks.append(result["content"])
                self.assertEqual(result["package_files_total"], 64)
                self.assertTrue(result["package_files_truncated"])
                if not result["has_more"]:
                    self.assertIsNone(result["next_cursor"])
                    break
                self.assertTrue(result["next_cursor"])
                self.assertNotEqual(cursor, result["next_cursor"])
                cursor = result["next_cursor"]
            else:
                self.fail("Final model JSON pagination did not terminate")
            self.assertEqual("".join(chunks), original)


if __name__ == "__main__":
    unittest.main()
