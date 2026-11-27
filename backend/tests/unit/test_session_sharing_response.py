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


def test_session_share_preserves_sanitized_ui_and_svg_parts() -> None:
    """Shared snapshots keep Stream UI and SVG parts while stripping internal refs."""
    from app.services.session_sharing import _sanitize_parts

    sanitized = _sanitize_parts(
        [
            {
                "id": "ui-1",
                "type": "ui",
                "status": "completed",
                "data": {
                    "protocol": "suip/0.1",
                    "component": "document",
                    "block_id": "doc-1",
                    "revision": 2,
                    "state": "completed",
                    "props": {"title": "报告", "data_ref": "internal-file-1"},
                    "blocks": [
                        {
                            "id": "img-1",
                            "type": "image",
                            "media": [
                                {"url": "https://cdn.example/a.png", "title": "公开图"},
                                {"url": "/api/v1/files/secret/stream", "title": "内部图"},
                            ],
                        }
                    ],
                },
            },
            {
                "id": "ui-removed",
                "type": "ui",
                "status": "completed",
                "data": {"state": "removed", "block_id": "old"},
            },
            {
                "id": "svg-1",
                "type": "svg",
                "status": "completed",
                "data": {
                    "block_id": "svg-block",
                    "title": "示意图",
                    "view_box": "0 0 100 100",
                    "nodes": [{"id": "r1", "tag": "rect", "attrs": {}, "text": ""}],
                    "state": "completed",
                    "internal_secret": "drop-me",
                },
            },
        ]
    )

    assert len(sanitized) == 2
    ui_part, svg_part = sanitized
    assert ui_part["type"] == "ui"
    assert "data_ref" not in ui_part["data"]["props"]
    assert ui_part["data"]["blocks"][0]["media"] == [
        {"url": "https://cdn.example/a.png", "title": "公开图"}
    ]
    assert svg_part["type"] == "svg"
    assert "internal_secret" not in svg_part["data"]
    assert svg_part["data"]["view_box"] == "0 0 100 100"


def test_preview_gateway_injects_preview_control_shim() -> None:
    """Multi-file preview HTML receives preview-control-shim.js before runtime-shim.js."""
    from app.preview import (
        PREVIEW_CONTROL_SHIM_SCRIPT_TAG,
        _inject_runtime_shim,
        serve_preview_control_shim,
    )

    resp = serve_preview_control_shim()
    assert resp.status_code == 200
    assert b"preview.control.ack" in resp.body

    injected = _inject_runtime_shim(
        b"<!doctype html><html><head></head><body>ok</body></html>",
        subapp_mode=True,
    ).decode("utf-8")
    assert PREVIEW_CONTROL_SHIM_SCRIPT_TAG in injected
    assert "/api/v1/subapps/runtime-shim.js" in injected
    assert "/api/v1/subapps/subapp-client.js" in injected

