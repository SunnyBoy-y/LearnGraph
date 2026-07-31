from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core import scheduler
from app.core.errors import AppError
from app.domain.models import Base, SandboxAgentCommand, SandboxSession, utc_now
from app.services.sandbox import SandboxAgentWorkspaceService


def test_command_lease_allows_only_one_owner(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'lease.db'}")
    Base.metadata.create_all(engine)
    now = utc_now()
    with Session(engine) as db:
        db.add(
            SandboxSession(
                id="session-1",
                workspace_id="workspace-1",
                owner_user_id="user-1",
                chat_session_id="chat-1",
                manifest_hash="manifest",
                expires_at=now + timedelta(hours=1),
                workspace_expires_at=now + timedelta(hours=1),
                absolute_expires_at=now + timedelta(hours=1),
                lifecycle_state="WARM_IDLE",
                status="ready",
            )
        )
        db.add_all(
            [
                SandboxAgentCommand(
                    id="command-1",
                    workspace_id="workspace-1",
                    owner_user_id="user-1",
                    sandbox_session_id="session-1",
                    chat_session_id="chat-1",
                    argv_digest="a" * 64,
                ),
                SandboxAgentCommand(
                    id="command-2",
                    workspace_id="workspace-1",
                    owner_user_id="user-1",
                    sandbox_session_id="session-1",
                    chat_session_id="chat-1",
                    argv_digest="b" * 64,
                ),
            ]
        )
        db.commit()
        session = db.get(SandboxSession, "session-1")
        first = db.get(SandboxAgentCommand, "command-1")
        second = db.get(SandboxAgentCommand, "command-2")
        service = object.__new__(SandboxAgentWorkspaceService)
        service.db = db
        service.settings = SimpleNamespace(sandbox_wall_time_seconds=30)
        service._claim_command_lease(session, first)
        with pytest.raises(AppError, match="already running") as caught:
            service._claim_command_lease(session, second)
        assert caught.value.code == "sandbox_session_busy"


def test_cleanup_recovers_expired_running_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'cleanup.db'}")
    Base.metadata.create_all(engine)
    now = utc_now()
    workspace_root = tmp_path / "workspaces"
    with Session(engine) as db:
        db.add(
            SandboxSession(
                id="session-1",
                workspace_id="workspace-1",
                owner_user_id="user-1",
                chat_session_id="chat-1",
                manifest_hash="manifest",
                expires_at=now + timedelta(hours=1),
                workspace_expires_at=now + timedelta(hours=1),
                absolute_expires_at=now + timedelta(hours=1),
                lifecycle_state="RUNNING",
                status="running",
                active_command_id="command-1",
                lease_token_hash="token",
                lease_expires_at=now - timedelta(seconds=1),
                heartbeat_at=now - timedelta(seconds=10),
            )
        )
        db.add(
            SandboxAgentCommand(
                id="command-1",
                workspace_id="workspace-1",
                owner_user_id="user-1",
                sandbox_session_id="session-1",
                chat_session_id="chat-1",
                argv_digest="a" * 64,
                status="running",
            )
        )
        db.commit()

    monkeypatch.setattr(scheduler, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(
        scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            resolved_sandbox_workspace_root=workspace_root,
            sandbox_wall_time_seconds=30,
            sandbox_container_idle_ttl_seconds=180,
            sandbox_container_absolute_ttl_seconds=1800,
        ),
    )
    totals = scheduler.run_sandbox_cleanup_sweep(now=now)
    assert totals["recovered"] == 1
    with Session(engine) as db:
        session = db.get(SandboxSession, "session-1")
        command = db.get(SandboxAgentCommand, "command-1")
        assert session.active_command_id is None
        assert command.error_class == "sandbox_command_interrupted"
