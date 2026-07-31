from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import Base, ChatSession, SandboxDestructiveGrant, utc_now
from app.services.sandbox_authz import (
    SandboxAuthorizationService,
    destructive_intent_digest,
)


def _grant(
    *,
    grant_id: str,
    path_prefix: str,
    sandbox_session_id: str = "sandbox-1",
) -> SandboxDestructiveGrant:
    return SandboxDestructiveGrant(
        id=grant_id,
        workspace_id="workspace-1",
        owner_user_id="user-1",
        chat_session_id="chat-1",
        sandbox_session_id=sandbox_session_id,
        action="delete_path",
        path_prefix=path_prefix,
        status="active",
        granted_by="user-1",
        expires_at=utc_now() + timedelta(minutes=5),
    )


def test_single_use_grant_is_consumed_once(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'grants.db'}")
    Base.metadata.create_all(engine)
    now = utc_now()
    intent_digest = destructive_intent_digest(
        chat_session_id="chat-1",
        sandbox_session_id="sandbox-1",
        argv=("rm", "work/file.txt"),
        paths=("work/file.txt",),
    )
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
                command_intent_digest=intent_digest,
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
        assert authz.consume_delete_prefixes(
            chat_session_id="chat-1",
            sandbox_session_id="sandbox-1",
            paths=("work/file.txt",),
            command_intent_digest=intent_digest,
        ) == ("work/file.txt",)
        assert not authz.has_active_grant(
            chat_session_id="chat-1", path="work/file.txt"
        )
        grant = db.get(SandboxDestructiveGrant, "grant-1")
        assert grant is not None
        assert grant.status == "consumed"
        assert grant.consumed_at is not None
        with pytest.raises(AppError) as exc_info:
            authz.consume_delete_grants(
                chat_session_id="chat-1",
                sandbox_session_id="sandbox-1",
                paths=("work/file.txt",),
            )
        assert exc_info.value.code == "sandbox_auth_required"


def test_consuming_matching_path_preserves_unrelated_grant(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'grants.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ChatSession(id="chat-1", workspace_id="workspace-1", title="test"))
        db.add_all(
            [
                _grant(grant_id="match", path_prefix="work/delete-me.txt"),
                _grant(grant_id="unrelated", path_prefix="work/keep-me.txt"),
            ]
        )
        db.commit()
        authz = SandboxAuthorizationService(db, "workspace-1", "user-1")

        assert authz.consume_delete_grants(
            chat_session_id="chat-1",
            sandbox_session_id="sandbox-1",
            paths=("work/delete-me.txt",),
        ) == ("work/delete-me.txt",)

        matching = db.get(SandboxDestructiveGrant, "match")
        unrelated = db.get(SandboxDestructiveGrant, "unrelated")
        assert matching is not None and matching.status == "consumed"
        assert unrelated is not None and unrelated.status == "active"
        assert unrelated.consumed_at is None


def test_no_grant_is_consumed_without_a_matching_delete_path(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'grants.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ChatSession(id="chat-1", workspace_id="workspace-1", title="test"))
        db.add(_grant(grant_id="grant-1", path_prefix="work/file.txt"))
        db.commit()
        authz = SandboxAuthorizationService(db, "workspace-1", "user-1")

        assert authz.authorize_or_raise(
            chat_session_id="chat-1", argv=("ls", "work")
        ) is None
        grant = db.get(SandboxDestructiveGrant, "grant-1")
        assert grant is not None
        assert grant.status == "active"
        assert grant.consumed_at is None


def test_prefix_grant_covers_nested_path_and_is_consumed(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'grants.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ChatSession(id="chat-1", workspace_id="workspace-1", title="test"))
        db.add(_grant(grant_id="grant-tmp", path_prefix="work/tmp"))
        db.commit()
        authz = SandboxAuthorizationService(db, "workspace-1", "user-1")

        assert authz.consume_delete_grants(
            chat_session_id="chat-1",
            sandbox_session_id="sandbox-1",
            paths=("work/tmp/nested/file.txt",),
        ) == ("work/tmp",)
        grant = db.get(SandboxDestructiveGrant, "grant-tmp")
        assert grant is not None
        assert grant.status == "consumed"


def test_other_sandbox_session_grant_is_not_consumed(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'grants.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ChatSession(id="chat-1", workspace_id="workspace-1", title="test"))
        db.add(
            _grant(
                grant_id="grant-other",
                path_prefix="work/file.txt",
                sandbox_session_id="sandbox-other",
            )
        )
        db.commit()
        authz = SandboxAuthorizationService(db, "workspace-1", "user-1")

        with pytest.raises(AppError) as exc_info:
            authz.consume_delete_grants(
                chat_session_id="chat-1",
                sandbox_session_id="sandbox-1",
                paths=("work/file.txt",),
            )
        assert exc_info.value.code == "sandbox_auth_required"
        grant = db.get(SandboxDestructiveGrant, "grant-other")
        assert grant is not None
        assert grant.status == "active"
        assert grant.consumed_at is None
