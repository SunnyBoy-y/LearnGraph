"""SEC-02 regression: Skill lifecycle permissions are explicit.

Design decision (confirmed by product): ordinary workspace members with
``workspace.write`` may install, edit, validate, scan, translate, update and
revoke Skills. Workspace-wide authorization and execution stay on
``workspace.manage`` because grants are workspace-scoped and invocation runs
the Skill's builtin-tool steps.

The old behaviour relied on a trailing-slash mismatch between
``/api/v1/skills/`` (sensitive prefix) and ``/api/v1/skills`` (exact install
path), which silently allowed installs for members while a later prefix change
could flip the boundary. These tests pin the explicit contract.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.database import SessionLocal, engine
from app.core.security import hash_session_token
from app.domain import models as m
from app.main import app

TENANT = "tenant-sec02"
ORG = "org-sec02"
WS = "ws-sec02"
ADMIN = "admin-sec02"
MEMBER = "member-sec02"
VIEWER = "viewer-sec02"

TOKEN_ADMIN = "token-admin-sec02"
TOKEN_MEMBER = "token-member-sec02"
TOKEN_VIEWER = "token-viewer-sec02"

VALID_INSTALL_PAYLOAD = {
    "skill_key": "audit_permission_test",
    "name": "audit permission test",
    "source": "https://example.com/skill",
    "version": "1.0.0",
    "generated_by": "user_import",
    "manifest": {
        "schema_version": "1.0",
        "kind": "declarative_review",
        "instructions_markdown": "test",
        "required_tools": ["builtin.graph.read"],
        "permissions": ["graph.read"],
        "allowed_components": [],
        "input_schema": {"type": "object", "additionalProperties": False},
        "steps": [{"tool": "builtin.graph.read", "arguments": {}}],
    },
}


def _seed_identity(session: m.Session) -> tuple[str, str, str]:
    now = m.utc_now()
    session.add(m.Tenant(id=TENANT, name="sec02"))
    for user_id in (ADMIN, MEMBER, VIEWER):
        session.add(
            m.User(
                id=user_id,
                tenant_id=TENANT,
                username=user_id,
                username_normalized=user_id,
                password_hash="unused",
                status="active",
            )
        )
    session.add(m.Organization(id=ORG, tenant_id=TENANT, name="sec02", owner_user_id=ADMIN))
    session.add(
        m.Workspace(
            id=WS,
            tenant_id=TENANT,
            owner_user_id=ADMIN,
            organization_id=ORG,
            workspace_kind="organization",
            name="sec02",
        )
    )

    permissions = {
        key: session.query(m.Permission).filter(m.Permission.key == key).one()
        for key in ("workspace.read", "workspace.write", "workspace.manage")
    }

    session.add(m.Role(id="role-member", tenant_id=TENANT, organization_id=ORG, name="member"))
    session.add(m.Role(id="role-viewer", tenant_id=TENANT, organization_id=ORG, name="viewer"))
    session.add_all(
        [
            m.RolePermission(
                id="rp-member-read",
                role_id="role-member",
                permission_id=permissions["workspace.read"].id,
            ),
            m.RolePermission(
                id="rp-member-write",
                role_id="role-member",
                permission_id=permissions["workspace.write"].id,
            ),
            m.RolePermission(
                id="rp-viewer-read",
                role_id="role-viewer",
                permission_id=permissions["workspace.read"].id,
            ),
        ]
    )
    # Flush roles before memberships so the SQLite FK insert order is explicit
    # instead of relying on SQLAlchemy's topological sort for this graph.
    session.flush()
    session.add_all(
        [
            m.Membership(
                id="mem-member",
                tenant_id=TENANT,
                organization_id=ORG,
                user_id=MEMBER,
                role_id="role-member",
                status="active",
            ),
            m.Membership(
                id="mem-viewer",
                tenant_id=TENANT,
                organization_id=ORG,
                user_id=VIEWER,
                role_id="role-viewer",
                status="active",
            ),
        ]
    )
    for raw_token, session_id, user_id in (
        (TOKEN_ADMIN, "sess-admin", ADMIN),
        (TOKEN_MEMBER, "sess-member", MEMBER),
        (TOKEN_VIEWER, "sess-viewer", VIEWER),
    ):
        session.add(
            m.AuthSession(
                id=session_id,
                tenant_id=TENANT,
                user_id=user_id,
                token_hash=hash_session_token(raw_token),
                expires_at=now + timedelta(days=1),
                last_seen_at=now,
            )
        )
    session.commit()
    return ADMIN, MEMBER, VIEWER


def _auth_headers(raw_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {raw_token}",
        "X-Workspace-ID": WS,
    }


def test_skill_permission_boundaries_are_explicit() -> None:
    m.Base.metadata.create_all(engine)
    with TestClient(app) as client, SessionLocal() as session:
        _seed_identity(session)

        # Viewer (read only) cannot install a Skill.
        viewer = client.post("/api/v1/skills", json=VALID_INSTALL_PAYLOAD, headers=_auth_headers(TOKEN_VIEWER))
        assert viewer.status_code == 403

        # Member (write) can install; this is the regression this test pins.
        member = client.post("/api/v1/skills", json=VALID_INSTALL_PAYLOAD, headers=_auth_headers(TOKEN_MEMBER))
        assert member.status_code == 201, member.text
        skill_id = member.json()["id"]

        # Execution and workspace-wide authorization remain manage-only.
        for path, payload in (
            (f"/api/v1/skills/{skill_id}/invoke", {"input": {}}),
            (f"/api/v1/skills/{skill_id}/authorize", {"decision": "deny", "permissions": []}),
            (
                f"/api/v1/skills/{skill_id}/sandbox-run",
                {"script_path": "scripts/main.py", "argv_extra": []},
            ),
        ):
            blocked = client.post(path, json=payload, headers=_auth_headers(TOKEN_MEMBER))
            assert blocked.status_code == 403, (path, blocked.status_code, blocked.text)

        # Owner passes the manage gate and reaches the service boundary.
        authorized = client.post(
            f"/api/v1/skills/{skill_id}/authorize",
            json={"decision": "always", "permissions": ["graph.read"], "reason": "sec02 audit"},
            headers=_auth_headers(TOKEN_ADMIN),
        )
        assert authorized.status_code == 201, authorized.text
        invoked = client.post(
            f"/api/v1/skills/{skill_id}/invoke",
            json={"input": {}},
            headers=_auth_headers(TOKEN_ADMIN),
        )
        assert invoked.status_code != 403, invoked.text
