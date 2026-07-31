from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domain.models import Base, ChatSession, SandboxDestructiveGrant, utc_now
from app.services.sandbox_authz import SandboxAuthorizationService


def test_single_use_grant_is_consumed_once(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'grants.db'}")
    Base.metadata.create_all(engine)
    now = utc_now()
    with Session(engine) as db:
        db.add(ChatSession(id="chat-1", workspace_id="workspace-1", title="test"))
        db.add(
            SandboxDestructiveGrant(
                id="grant-1",
                workspace_id="workspace-1",
                owner_user_id="user-1",
                chat_session_id="chat-1",
                sandbox_session_id="sandbox-1",
                action="delete_path",
                path_prefix="work/file.txt",
                status="active",
                granted_by="user-1",
                expires_at=now + timedelta(minutes=5),
            )
        )
        db.commit()
        authz = SandboxAuthorizationService(db, "workspace-1", "user-1")
        assert authz.has_active_grant(
            chat_session_id="chat-1", path="work/file.txt"
        )
        assert authz.active_delete_prefixes(
            chat_session_id="chat-1",
            sandbox_session_id="sandbox-1",
            consume=True,
        ) == ("work/file.txt",)
        assert not authz.has_active_grant(
            chat_session_id="chat-1", path="work/file.txt"
        )
        grant = db.get(SandboxDestructiveGrant, "grant-1")
        assert grant.status == "consumed"
        assert grant.consumed_at is not None
