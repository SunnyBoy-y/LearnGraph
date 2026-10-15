from __future__ import annotations

from types import SimpleNamespace

from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.api.routers.session_sharing import create_session_share, view_shared_session
from app.core.database import Base
from app.core.security import Principal
from app.domain.models import (
    AuditEvent,
    ChatSession,
    Message,
    SessionShare,
    SessionShareMessage,
    SessionShareToken,
)
from app.domain.schemas.session_sharing import SessionShareCreate


def test_create_session_share_returns_token_after_commit() -> None:
    """A successful create must not turn into a 500 during response validation."""

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            ChatSession.__table__,
            Message.__table__,
            SessionShare.__table__,
            SessionShareMessage.__table__,
            SessionShareToken.__table__,
            AuditEvent.__table__,
        ],
    )

    with Session(engine) as db:
        db.add(
            ChatSession(
                id="session-response-regression",
                workspace_id="workspace-response-regression",
                title="分享响应回归",
            )
        )
        db.add(
            Message(
                id="message-response-regression",
                session_id="session-response-regression",
                workspace_id="workspace-response-regression",
                role="assistant",
                content="可以被分享的回答",
                parts=[],
            )
        )
        db.commit()

        context = WorkspaceContext(
            principal=Principal(
                user_id="user-response-regression",
                username="regression",
                tenant_id="tenant-response-regression",
                session_id="auth-response-regression",
            ),
            workspace=SimpleNamespace(id="workspace-response-regression"),
            permissions=frozenset({"workspace.read", "workspace.write"}),
        )

        response = create_session_share(
            "session-response-regression",
            SessionShareCreate(scope="full"),
            db,
            context,
        )

        assert response.token
        assert response.token_prefix == response.token[:12]
        assert response.view_count == 0
        assert response.revoked_at is None

        public = view_shared_session(
            response.token,
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": f"/api/v1/share/{response.token}",
                    "headers": [(b"user-agent", b"regression")],
                    "client": ("127.0.0.1", 1234),
                    "server": ("test", 80),
                    "scheme": "http",
                }
            ),
            db,
        )

        assert public.title == "分享响应回归"
        assert len(public.messages) == 1
        assert db.get(SessionShareToken, response.id).view_count == 1
