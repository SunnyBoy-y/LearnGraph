"""Real membership and system-admin boundaries for the existing billing APIs."""
from uuid import uuid4

from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.models import Membership, User, Workspace


def test_billing_permissions_follow_membership_not_system_admin(client, register_user, auth_headers, monkeypatch):
    token_a, personal, *_ = register_user("billing-owner")
    token_b, _, username, password = register_user("billing-member")
    owner = client.get("/api/v1/auth/me", headers=auth_headers(token_a)).json()
    member = client.get("/api/v1/auth/me", headers=auth_headers(token_b)).json()
    with SessionLocal() as db:
        db.get(User, member["id"]).tenant_id = owner["tenant_id"]
        db.commit()
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    token_b = response.json()["access_token"]
    org = client.post("/api/v1/organizations", headers=auth_headers(token_a),
        json={"name": "Billing roles " + uuid4().hex, "workspace_name": "Shared billing"}).json()
    with SessionLocal() as db:
        workspace = db.scalar(select(Workspace.id).where(Workspace.organization_id == org["id"]))
    roles = client.get(f"/api/v1/organizations/{org['id']}/roles", headers=auth_headers(token_a)).json()
    role_member = next(role["id"] for role in roles if role["name"] == "Member")
    role_viewer = next(role["id"] for role in roles if role["name"] == "Viewer")
    response = client.post(f"/api/v1/organizations/{org['id']}/memberships", headers=auth_headers(token_a),
        json={"user_id": member["id"], "role_id": role_member})
    assert response.status_code == 201, response.text
    owner_headers, member_headers = auth_headers(token_a, workspace), auth_headers(token_b, workspace)
    policy = client.post("/api/v1/usage/budget-policies", headers=owner_headers,
        json={"name": "native", "currency": "USD", "hard_limit": "1"})
    assert policy.status_code == 201, policy.text
    policy_id = policy.json()["id"]
    from app.api.routers import usage
    monkeypatch.setattr(usage, "refresh_snapshot", lambda: {"source": "test", "origin": "test", "fetched_at": None,
        "provider_count": 0, "model_count": 0, "priced_model_count": 0})
    for role_id, is_system_admin in ((role_member, False), (role_viewer, False), (role_viewer, True)):
        with SessionLocal() as db:
            membership = db.scalar(select(Membership).where(Membership.user_id == member["id"], Membership.organization_id == org["id"]))
            membership.role_id = role_id
            db.get(User, member["id"]).is_system_admin = is_system_admin
            db.commit()
        for endpoint in ("summary", "events", "budget-policies", "budget-status", "budget-alerts", "manual-prices", "price-catalog"):
            response = client.get(f"/api/v1/usage/{endpoint}?currency=USD", headers=member_headers)
            assert response.status_code == 200, (endpoint, response.text)
        writes = [("post", "budget-policies", {"name": "blocked", "currency": "CNY", "hard_limit": "2"}),
            ("put", f"budget-policies/{policy_id}", {"name": "blocked", "hard_limit": "2"}),
            ("delete", f"budget-policies/{policy_id}", None), ("delete", "events?currency=USD", None),
            ("put", "manual-prices", {"provider_id": "*", "model_id": "blocked", "currency": "USD", "input_per_million": "0", "output_per_million": "0"}),
            ("delete", "manual-prices?price_id=missing", None), ("put", "alert-email", {}),
            ("post", "alert-email/test", None), ("post", "budget-alerts/missing/acknowledge", None)]
        for method, endpoint, body in writes:
            response = client.request(method.upper(), f"/api/v1/usage/{endpoint}", headers=member_headers, json=body)
            assert response.status_code == 403, (endpoint, response.text)
        assert client.get("/api/v1/usage/alert-email", headers=member_headers).status_code == 403
        assert client.post("/api/v1/usage/models-dev/refresh", headers=member_headers).status_code == 403
        assert client.get("/api/v1/usage/summary", headers=auth_headers(token_b, personal)).status_code == 403
    assert client.post("/api/v1/usage/models-dev/refresh", headers=owner_headers).status_code == 403
    with SessionLocal() as db:
        db.get(User, owner["id"]).is_system_admin = True
        db.commit()
    assert client.post("/api/v1/usage/models-dev/refresh", headers=owner_headers).status_code == 200
    assert client.get("/api/v1/usage/alert-email", headers=owner_headers).status_code == 200
    assert client.get("/api/v1/usage/budget-policies?currency=USD", headers=owner_headers).json()[0]["hard_limit"] == "1"
