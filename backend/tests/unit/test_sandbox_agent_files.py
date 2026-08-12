"""Agent sandbox file tools: host-side grep, line-range reads, pattern listing,
replace_all edits, and the single-use authorized delete.

Runs entirely against the content-addressed session workspace store — no Docker
backend is required for write/read/list/grep/edit/delete-file (the container
cleanup path degrades gracefully).
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import Base
from app.core.errors import AppError
from app.core.scheduler import _prune_orphaned_sandbox_snapshots
from app.domain import models as m
from app.domain.schemas.sandbox import SandboxAgentEnvironmentRequest
from app.providers.ports.sandbox import SandboxSessionHandle
from app.providers.remote.sandbox import SandboxBackendUnavailable
from app.services.sandbox import SandboxAgentWorkspaceService
from app.services.sandbox_authz import (
    SandboxAuthorizationService,
    destructive_intent_digest,
)

WORKSPACE = "ws-sandbox-agent-files"
ACTOR = "user-sandbox-agent-files"
CHAT_SESSION_ID = "chat-sandbox-agent-files"


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        session.add(
            m.ChatSession(
                workspace_id=WORKSPACE,
                id=CHAT_SESSION_ID,
                title="sandbox agent files test",
            )
        )
        session.commit()
        yield session
    engine.dispose()


class _FakeBackend:
    """No-op container backend: the host workspace store stays authoritative."""

    def write_agent_file(self, handle, path: str, data: bytes) -> None:
        pass

    def delete_agent_file(self, handle, path: str) -> None:
        pass

    def read(self, handle, path: str, limit_bytes: int) -> bytes:
        raise SandboxBackendUnavailable("no container in unit tests")

    def list_files(self, handle, limit_entries: int) -> list:
        return []


@pytest.fixture()
def service(db: Session, monkeypatch) -> SandboxAgentWorkspaceService:
    instance = SandboxAgentWorkspaceService(db, WORKSPACE, ACTOR, get_settings())
    fake = _FakeBackend()
    monkeypatch.setattr(
        instance,
        "_ensure_backend_session",
        lambda session: SandboxSessionHandle(session.id, "fake-backend"),
    )
    monkeypatch.setattr(instance, "_runtime_backend", lambda session: fake)
    return instance


def _write(service: SandboxAgentWorkspaceService, path: str, content: str) -> dict:
    return service.execute_agent_tool(
        "sandbox_write_file",
        {"path": path, "content": content},
        chat_session_id=CHAT_SESSION_ID,
        agent_authorized=True,
    )


def _read(service: SandboxAgentWorkspaceService, path: str, **extra) -> dict:
    return service.execute_agent_tool(
        "sandbox_read_file",
        {"path": path, **extra},
        chat_session_id=CHAT_SESSION_ID,
        agent_authorized=True,
    )


class TestWriteReadRoundtrip:
    def test_write_then_read(self, db: Session, service) -> None:
        written = _write(service, "work/hello.txt", "hello\nworld\n")
        assert written["sha256"] == written["blob_sha256"]
        assert written["role"] == "work"

        result = _read(service, "work/hello.txt")
        assert result["content"] == "hello\nworld\n"
        assert result["total_lines"] == 2
        assert result["total_bytes"] == len("hello\nworld\n".encode("utf-8"))
        assert result["start_line"] is None
        assert result["end_line"] is None
        assert result["truncated"] is False

    def test_read_line_range(self, db: Session, service) -> None:
        _write(service, "work/lines.txt", "one\ntwo\nthree\nfour\nfive\n")
        result = _read(service, "work/lines.txt", start_line=2, end_line=4)
        assert result["content"] == "two\nthree\nfour\n"
        assert result["total_lines"] == 5
        assert result["start_line"] == 2
        assert result["end_line"] == 4

    def test_read_end_line_clamped(self, db: Session, service) -> None:
        _write(service, "work/lines.txt", "one\ntwo\n")
        result = _read(service, "work/lines.txt", start_line=1, end_line=99)
        assert result["content"] == "one\ntwo\n"
        assert result["end_line"] == 2

    def test_read_start_beyond_file(self, db: Session, service) -> None:
        _write(service, "work/lines.txt", "one\n")
        with pytest.raises(AppError) as excinfo:
            _read(service, "work/lines.txt", start_line=5)
        assert excinfo.value.code == "sandbox_file_range_out_of_bounds"

    def test_read_invalid_range(self, db: Session, service) -> None:
        _write(service, "work/lines.txt", "one\ntwo\n")
        with pytest.raises(AppError) as excinfo:
            _read(service, "work/lines.txt", start_line=3, end_line=1)
        assert excinfo.value.code == "sandbox_file_range_invalid"

    def test_read_max_chars(self, db: Session, service) -> None:
        _write(service, "work/lines.txt", "abcdefghij\n")
        result = _read(service, "work/lines.txt", max_chars=5)
        assert result["content"] == "abcde"
        assert result["truncated"] is True
        assert result["total_lines"] == 1


class TestGrep:
    def test_grep_basic_and_counts(self, db: Session, service) -> None:
        _write(service, "work/a.py", "def foo():\n    return 1\n")
        _write(service, "work/b.py", "FOO = 2\n# unrelated\n")
        result = service.execute_agent_tool(
            "sandbox_grep",
            {"pattern": "foo"},
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        # Case-insensitive default: matches foo and FOO.
        assert [item["path"] for item in result["matches"]] == [
            "work/a.py",
            "work/b.py",
        ]
        assert result["file_counts"] == [
            {"path": "work/a.py", "matches": 1},
            {"path": "work/b.py", "matches": 1},
        ]
        assert result["searched_files"] == 2
        assert result["truncated"] is False

    def test_grep_case_sensitive(self, db: Session, service) -> None:
        _write(service, "work/a.py", "def foo():\n    pass\n")
        _write(service, "work/b.py", "FOO = 2\n")
        result = service.execute_agent_tool(
            "sandbox_grep",
            {"pattern": "foo", "case_sensitive": True},
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        assert [item["path"] for item in result["matches"]] == ["work/a.py"]

    def test_grep_context_and_filter(self, db: Session, service) -> None:
        _write(service, "work/code.py", "before\nTODO: fix\nafter\n")
        _write(service, "work/notes.md", "no match here\n")
        result = service.execute_agent_tool(
            "sandbox_grep",
            {"pattern": "TODO", "context_lines": 1, "path": "work/*.py"},
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        assert len(result["matches"]) == 1
        match = result["matches"][0]
        assert match["path"] == "work/code.py"
        assert match["line_number"] == 2
        assert match["text"] == "TODO: fix"
        assert [row["line_number"] for row in match["context"]] == [1, 3]

    def test_grep_invalid_pattern(self, db: Session, service) -> None:
        with pytest.raises(AppError) as excinfo:
            service.execute_agent_tool(
                "sandbox_grep",
                {"pattern": "([unclosed"},
                chat_session_id=CHAT_SESSION_ID,
                agent_authorized=True,
            )
        assert excinfo.value.code == "sandbox_grep_invalid_pattern"

    def test_grep_skips_binary(self, db: Session, service) -> None:
        service.workspace_files.put_bytes(
            chat_session_id=CHAT_SESSION_ID,
            path="work/blob.bin",
            data=b"\x00\x01\x02\xff\xfe",
            role="work",
            source="agent_write",
        )
        result = service.execute_agent_tool(
            "sandbox_grep",
            {"pattern": "anything"},
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        assert result["searched_files"] == 0
        assert result["skipped_binary"] == 1
        assert result["matches"] == []

    def test_grep_max_matches_truncates(self, db: Session, service) -> None:
        content = "\n".join(f"line {i}" for i in range(200))
        _write(service, "work/big.txt", content + "\n")
        result = service.execute_agent_tool(
            "sandbox_grep",
            {"pattern": r"^line \d+$", "max_matches": 10},
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        assert len(result["matches"]) == 10
        assert result["truncated"] is True


class TestListFiles:
    def test_list_pattern_filter(self, db: Session, service) -> None:
        _write(service, "work/code/main.py", "x\n")
        _write(service, "work/code/util.py", "y\n")
        _write(service, "inputs/note.md", "z\n")
        result = service.execute_agent_tool(
            "sandbox_list_files",
            {"pattern": "work/code/*.py"},
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        paths = [item["path"] for item in result["files"]]
        assert paths == ["work/code/main.py", "work/code/util.py"]
        assert all(item["mtime"] is not None for item in result["files"])

    def test_list_max_results(self, db: Session, service) -> None:
        for index in range(5):
            _write(service, f"work/f{index}.txt", "x\n")
        result = service.execute_agent_tool(
            "sandbox_list_files",
            {"max_results": 2},
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        assert len(result["files"]) == 2


class TestEditFile:
    def test_edit_unique(self, db: Session, service) -> None:
        written = _write(service, "work/code.py", "a = 1\nb = 2\n")
        result = service.execute_agent_tool(
            "sandbox_edit_file",
            {
                "path": "work/code.py",
                "old_string": "b = 2",
                "new_string": "b = 3",
                "expected_sha256": written["sha256"],
            },
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        assert result["replaced_count"] == 1
        assert _read(service, "work/code.py")["content"] == "a = 1\nb = 3\n"

    def test_edit_requires_unique(self, db: Session, service) -> None:
        written = _write(service, "work/code.py", "x = 1\nx = 2\n")
        with pytest.raises(AppError) as excinfo:
            service.execute_agent_tool(
                "sandbox_edit_file",
                {
                    "path": "work/code.py",
                    "old_string": "x =",
                    "new_string": "y =",
                    "expected_sha256": written["sha256"],
                },
                chat_session_id=CHAT_SESSION_ID,
                agent_authorized=True,
            )
        assert excinfo.value.code == "sandbox_edit_match_invalid"

    def test_edit_replace_all(self, db: Session, service) -> None:
        written = _write(service, "work/code.py", "x = 1\nx = 2\nx = 3\n")
        result = service.execute_agent_tool(
            "sandbox_edit_file",
            {
                "path": "work/code.py",
                "old_string": "x =",
                "new_string": "y =",
                "expected_sha256": written["sha256"],
                "replace_all": True,
            },
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        assert result["replaced_count"] == 3
        assert _read(service, "work/code.py")["content"] == "y = 1\ny = 2\ny = 3\n"

    def test_edit_replace_all_cap(self, db: Session, service) -> None:
        written = _write(service, "work/code.py", "x\n" * 101)
        with pytest.raises(AppError) as excinfo:
            service.execute_agent_tool(
                "sandbox_edit_file",
                {
                    "path": "work/code.py",
                    "old_string": "x",
                    "new_string": "y",
                    "expected_sha256": written["sha256"],
                    "replace_all": True,
                },
                chat_session_id=CHAT_SESSION_ID,
                agent_authorized=True,
            )
        assert excinfo.value.code == "sandbox_edit_too_many_matches"

    def test_edit_stale_sha(self, db: Session, service) -> None:
        _write(service, "work/code.py", "a = 1\n")
        with pytest.raises(AppError) as excinfo:
            service.execute_agent_tool(
                "sandbox_edit_file",
                {
                    "path": "work/code.py",
                    "old_string": "a = 1",
                    "new_string": "a = 2",
                    "expected_sha256": "0" * 64,
                },
                chat_session_id=CHAT_SESSION_ID,
                agent_authorized=True,
            )
        assert excinfo.value.code == "sandbox_file_changed"


class TestDeleteFile:
    def _seed_session(self, service) -> str:
        written = _write(service, "work/tmp.txt", "delete me\n")
        return written["sandbox_session_id"]

    def _grant(self, db: Session, session_id: str, path: str) -> None:
        digest = destructive_intent_digest(
            chat_session_id=CHAT_SESSION_ID,
            sandbox_session_id=session_id,
            argv=("sandbox_delete_file", path),
            paths=(path,),
        )
        SandboxAuthorizationService(db, WORKSPACE, ACTOR).grant(
            chat_session_id=CHAT_SESSION_ID,
            path_prefix="work",
            sandbox_session_id=session_id,
            command_intent_digest=digest,
        )

    def test_delete_requires_authorization(self, db: Session, service) -> None:
        self._seed_session(service)
        with pytest.raises(AppError) as excinfo:
            service.execute_agent_tool(
                "sandbox_delete_file",
                {"path": "work/tmp.txt"},
                chat_session_id=CHAT_SESSION_ID,
                agent_authorized=True,
            )
        assert excinfo.value.code == "sandbox_auth_required"
        assert excinfo.value.details["command_intent_digest"]

    def test_delete_with_grant(self, db: Session, service) -> None:
        session_id = self._seed_session(service)
        self._grant(db, session_id, "work/tmp.txt")
        result = service.execute_agent_tool(
            "sandbox_delete_file",
            {"path": "work/tmp.txt", "sandbox_session_id": session_id},
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        assert result["deleted"] is True
        listed = service.execute_agent_tool(
            "sandbox_list_files",
            {},
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        assert all(item["path"] != "work/tmp.txt" for item in listed["files"])

    def test_delete_blocked_outside_work(self, db: Session, service) -> None:
        with pytest.raises(AppError) as excinfo:
            service.execute_agent_tool(
                "sandbox_delete_file",
                {"path": "inputs/note.md"},
                chat_session_id=CHAT_SESSION_ID,
                agent_authorized=True,
            )
        assert excinfo.value.code == "sandbox_path_blocked"


class TestToolSchemas:
    def test_new_tools_registered(self) -> None:
        names = [
            item["function"]["name"]
            for item in SandboxAgentWorkspaceService.agent_tool_definitions()
        ]
        assert "sandbox_grep" in names
        assert "sandbox_delete_file" in names
        assert "sandbox_read_file" in names
        assert "sandbox_list_files" in names

    def test_read_schema_has_line_range(self) -> None:
        by_name = {
            item["function"]["name"]: item["function"]
            for item in SandboxAgentWorkspaceService.agent_tool_definitions()
        }
        read_props = by_name["sandbox_read_file"]["parameters"]["properties"]
        assert {"start_line", "end_line", "max_chars"} <= set(read_props)

    def test_edit_schema_has_replace_all(self) -> None:
        by_name = {
            item["function"]["name"]: item["function"]
            for item in SandboxAgentWorkspaceService.agent_tool_definitions()
        }
        edit_props = by_name["sandbox_edit_file"]["parameters"]["properties"]
        assert "replace_all" in edit_props

    def test_list_schema_has_pattern(self) -> None:
        by_name = {
            item["function"]["name"]: item["function"]
            for item in SandboxAgentWorkspaceService.agent_tool_definitions()
        }
        list_props = by_name["sandbox_list_files"]["parameters"]["properties"]
        assert {"pattern", "max_results"} <= set(list_props)

    def test_grep_schema_shapes(self) -> None:
        by_name = {
            item["function"]["name"]: item["function"]
            for item in SandboxAgentWorkspaceService.agent_tool_definitions()
        }
        grep_props = by_name["sandbox_grep"]["parameters"]["properties"]
        assert {"pattern", "path", "context_lines", "max_matches"} <= set(grep_props)
        assert by_name["sandbox_grep"]["parameters"]["required"] == ["pattern"]


class TestEnvInfo:
    def test_env_info_reports_libraries(self, service) -> None:
        result = service.environment_info(
            SandboxAgentEnvironmentRequest(
                chat_session_id=CHAT_SESSION_ID,
                sandbox_session_id=None,
            )
        )
        assert isinstance(result.get("python_packages"), list)
        assert result["file_limit_bytes"] > 0


def test_snapshot_cleanup_removes_only_stale_snapshot_directories(tmp_path: Path) -> None:
    workspace_root = tmp_path / "sandbox-workspaces"
    owner_root = workspace_root / "user-1"
    owner_root.mkdir(parents=True)

    stale = owner_root / ".learngraph-command-snapshot-stale"
    stale.mkdir()
    (stale / "work.py").write_text("print('stale')", encoding="utf-8")
    fresh = owner_root / ".learngraph-command-snapshot-fresh"
    fresh.mkdir()
    ordinary = owner_root / "session-1"
    ordinary.mkdir()
    prefixed_file = owner_root / ".learngraph-command-snapshot-not-a-directory"
    prefixed_file.write_text("keep", encoding="utf-8")

    now = datetime.now(timezone.utc)
    stale_timestamp = (now - timedelta(hours=1)).timestamp()
    os.utime(stale, (stale_timestamp, stale_timestamp))
    os.utime(prefixed_file, (stale_timestamp, stale_timestamp))

    removed = _prune_orphaned_sandbox_snapshots(
        workspace_root,
        now=now,
        grace_seconds=600,
    )

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()
    assert ordinary.exists()
    assert prefixed_file.exists()
