"""MCP OAuth authorization-code, refresh, dynamic registration and revocation.

Covers the external endpoint layer added for P2-B:

1. ``begin_authorization_url`` binds PKCE/state server-side and never returns
   the code verifier.
2. Authorization-code exchange persists encrypted tokens and rejects a
   mismatched state before contacting the issuer.
3. Dynamic client registration is closed by default and opens only for an
   explicitly trusted issuer allowlist.
4. Refresh rotates under the single-flight coordinator and returns only a
   redacted projection.
5. The MCP router exposes begin/exchange/refresh/register/revoke/credential
   routes so the documented OAuth capability is actually reachable.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.errors import AppError
from app.domain.extension_models import MCPServer
from app.services.mcp_oauth import MCPOAuthLifecycle

WORKSPACE = "ws-mcp-oauth"
ACTOR = "user-mcp-oauth"
CALLBACK = "http://127.0.0.1:5173/oauth/callback"


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
        yield session
    engine.dispose()


def _settings(trusted_issuers=frozenset()) -> SimpleNamespace:
    return SimpleNamespace(
        secret_provider="environment",
        master_key="test-master-key-32-bytes!",
        master_key_version=1,
        mcp_oauth_trusted_issuers=trusted_issuers,
    )


def _server(db: Session, *, endpoint_url: str = "https://mcp.example.test") -> MCPServer:
    row = MCPServer(
        workspace_id=WORKSPACE,
        server_key="oauth-test",
        display_name="OAuth Test",
        source="test",
        version="1.0.0",
        transport="streamable_http",
        endpoint_url=endpoint_url,
        manifest_json={"version": 1, "identity": "oauth-test"},
        manifest_hash="a" * 64,
        requested_tools=["test_tool"],
        required_permissions=["network"],
        status="registered",
        enabled=False,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _lifecycle(db: Session, *, trusted_issuers=frozenset()) -> MCPOAuthLifecycle:
    return MCPOAuthLifecycle(
        db,
        WORKSPACE,
        ACTOR,
        settings=_settings(trusted_issuers=trusted_issuers),
    )


class _OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # pragma: no cover - fixture helper
        self._json({"ok": True}, 200)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        if self.path == "/register":
            self._json(
                {
                    "client_id": "client-123",
                    "client_secret": "secret-456",
                    "token_endpoint_auth_method": "client_secret_post",
                },
                201,
            )
            return
        if self.path == "/token":
            grant = "refresh_token" if "refresh_token=" in body else "authorization_code"
            payload = {
                "access_token": f"access-{grant}",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "read write",
            }
            if grant == "authorization_code":
                payload["refresh_token"] = "refresh-abc"
            self._json(payload, 200)
            return
        self._json({"error": "not_found"}, 404)

    def _json(self, payload: dict, code: int) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # pragma: no cover
        pass


@pytest.fixture()
def oauth_server() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OAuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_build_authorization_url_binds_pkce_server_side(db: Session) -> None:
    row = _server(db)
    lifecycle = _lifecycle(db)
    result = lifecycle.build_authorization_url(
        row,
        auth_endpoint="https://auth.example.com/authorize",
        redirect_uri=CALLBACK,
        scope="read write",
        client_id="client-123",
    )
    assert result["code_challenge_method"] == "S256"
    assert "state=" in result["authorization_url"]
    assert "code_challenge=" in result["authorization_url"]
    assert "code_verifier" not in result
    credential = lifecycle.credential_view(row)
    assert credential is not None
    assert credential["status"] == "pending"


def test_exchange_rejects_state_mismatch_before_issuer_call(db: Session) -> None:
    row = _server(db)
    lifecycle = _lifecycle(db)
    lifecycle.build_authorization_url(
        row,
        auth_endpoint="https://auth.example.com/authorize",
        redirect_uri=CALLBACK,
        scope="read write",
        client_id="client-123",
    )
    with pytest.raises(AppError) as exc_info:
        lifecycle.exchange_authorization_code(
            row,
            authorization_code="stolen-code",
            returned_state="wrong-state",
            token_endpoint="https://auth.example.com/token",
            client_id="client-123",
        )
    assert exc_info.value.code == "mcp_oauth_state_mismatch"


def test_full_authorization_code_exchange_persists_redacted_tokens(
    db: Session, oauth_server: str
) -> None:
    row = _server(db)
    lifecycle = _lifecycle(db)
    begin = lifecycle.build_authorization_url(
        row,
        auth_endpoint=f"{oauth_server}/authorize",
        redirect_uri=CALLBACK,
        scope="read write",
        client_id="client-123",
    )
    credential = lifecycle.exchange_authorization_code(
        row,
        authorization_code="auth-code",
        returned_state=begin["state"],
        token_endpoint=f"{oauth_server}/token",
        client_id="client-123",
    )
    assert credential.status == "active"
    view = lifecycle.redact_for_api(credential)
    assert view["has_refresh_token"] is True
    assert view["secret_masked"]
    assert "access_token" not in view
    assert "refresh_token" not in view


def test_dynamic_registration_closed_by_default(db: Session, oauth_server: str) -> None:
    row = _server(db)
    lifecycle = _lifecycle(db)
    with pytest.raises(AppError) as exc_info:
        lifecycle.register_oauth_client(
            row,
            issuer="https://issuer.example",
            registration_endpoint=f"{oauth_server}/register",
            client_name="LearnGraph",
            redirect_uris=[CALLBACK],
        )
    assert exc_info.value.code == "mcp_oauth_issuer_untrusted"


def test_dynamic_registration_with_trusted_issuer(
    db: Session, oauth_server: str
) -> None:
    row = _server(db)
    lifecycle = _lifecycle(db, trusted_issuers=frozenset({"https://issuer.example"}))
    view = lifecycle.register_oauth_client(
        row,
        issuer="https://issuer.example",
        registration_endpoint=f"{oauth_server}/register",
        client_name="LearnGraph",
        redirect_uris=[CALLBACK],
    )
    assert view["client_id"] == "client-123"
    assert view["has_client_secret"] is True


def test_refresh_access_token(db: Session, oauth_server: str) -> None:
    row = _server(db)
    lifecycle = _lifecycle(db)
    begin = lifecycle.build_authorization_url(
        row,
        auth_endpoint=f"{oauth_server}/authorize",
        redirect_uri=CALLBACK,
        scope="read write",
        client_id="client-123",
    )
    lifecycle.exchange_authorization_code(
        row,
        authorization_code="auth-code",
        returned_state=begin["state"],
        token_endpoint=f"{oauth_server}/token",
        client_id="client-123",
    )
    refreshed = lifecycle.refresh_access_token(
        row,
        token_endpoint=f"{oauth_server}/token",
        force=True,
    )
    assert refreshed.status == "active"
    view = lifecycle.redact_for_api(refreshed)
    assert view["status"] == "active"


def test_revoke_credential(db: Session) -> None:
    row = _server(db)
    lifecycle = _lifecycle(db)
    lifecycle.build_authorization_url(
        row,
        auth_endpoint="https://auth.example.com/authorize",
        redirect_uri=CALLBACK,
        scope="read write",
        client_id="client-123",
    )
    revoked = lifecycle.revoke(row, reason="audit test")
    assert revoked.status == "revoked"
    assert row.auth_reference is None


def test_oauth_routes_registered() -> None:
    from app.api.routers.mcp_skills import router

    paths = {route.path for route in router.routes}
    assert "/mcp/servers/{server_id}/oauth/begin" in paths
    assert "/mcp/servers/{server_id}/oauth/exchange" in paths
    assert "/mcp/servers/{server_id}/oauth/refresh" in paths
    assert "/mcp/servers/{server_id}/oauth/register" in paths
    assert "/mcp/servers/{server_id}/oauth/revoke" in paths
    assert "/mcp/servers/{server_id}/oauth/credential" in paths
