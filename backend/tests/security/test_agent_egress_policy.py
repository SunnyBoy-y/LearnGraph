"""Generic Agent egress policy wiring (D2.1 T4.1) — approval-derived policy.

Covers the chain that closes "approve -> sandbox can reach the host":

1. ``derive_egress_policy_for_agent`` turns the durable allowlist into a valid
   HTTPS-443-only ``EgressPolicy`` with ``issuer=agent_egress_authorization``;
   empty host sets fail closed.
2. ``EgressApprovalService.ensure_agent_egress_policy`` persists the derived
   policy file from workspace grants + active ``allow_once`` leases, preserving
   a reviewed baseline (non-agent issuer) and dropping consumed leases.
3. ``authorize_connect`` allows an approved public host and denies unknown
   hosts / private-address answers.
"""

from __future__ import annotations

import json
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain import models as m
from app.services.egress_approvals import EgressApprovalService
from app.services.sandbox_network_policy import (
    AGENT_EGRESS_POLICY_ISSUER,
    EgressPolicyDenied,
    authorize_connect,
    derive_egress_policy_for_agent,
    load_workspace_policy_file,
    store_workspace_policy_file,
)

WORKSPACE = "ws-agent-egress"
ACTOR = "user-agent-egress"


@pytest.fixture()
def policy_dir(tmp_path: Path) -> Path:
    return tmp_path / "egress-policies"


@pytest.fixture()
def settings(policy_dir: Path):
    return SimpleNamespace(
        sandbox_egress_enabled=True,
        sandbox_egress_policy_dir=str(policy_dir),
        sandbox_agent_egress_approvals_enabled=True,
    )


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


def _grant(db: Session, hostname: str, *, revoked: bool = False) -> m.HostAuthorizationGrant:
    grant = m.HostAuthorizationGrant(
        workspace_id=WORKSPACE,
        capability=m.EGRESS_APPROVAL_CAPABILITY,
        subject_type="workspace",
        subject_id=WORKSPACE,
        hostname=hostname,
        ports=[443],
        protocols=["https"],
        source_request_id="req-1",
        granted_by=ACTOR,
        granted_at=__import__("app.domain.models", fromlist=["utc_now"]).utc_now(),
        revoked_at=None,
        revoked_by=None,
    )
    if revoked:
        grant.revoked_at = __import__("app.domain.models", fromlist=["utc_now"]).utc_now()
    db.add(grant)
    db.commit()
    return grant


def _lease(db: Session, hostname: str, *, status: str = "approved", consumed: bool = False) -> m.EgressAuthorizationRequest:
    from datetime import datetime, timezone

    req = m.EgressAuthorizationRequest(
        workspace_id=WORKSPACE,
        hostname=hostname,
        capability=m.EGRESS_APPROVAL_CAPABILITY,
        requested_by=ACTOR,
        chat_session_id=None,
        status=status,
        decision="allow_once",
        allow_always=False,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        ttl_seconds=600,
        dedupe_key=hostname,
    )
    if consumed:
        req.status = "consumed"
        req.consumed_at = datetime.now(timezone.utc)
    db.add(req)
    db.commit()
    return req


def test_derive_policy_allow_and_deny(policy_dir: Path):
    policy = derive_egress_policy_for_agent(
        workspace_id=WORKSPACE,
        allowed_hosts=["api.example.com", "cdn.example.com"],
        ttl_seconds=600,
    )
    assert policy.issuer == AGENT_EGRESS_POLICY_ISSUER
    store_workspace_policy_file(str(policy_dir), policy)

    loaded = load_workspace_policy_file(str(policy_dir), WORKSPACE)
    assert loaded is not None
    assert loaded.digest == policy.digest

    ip, _ = authorize_connect(loaded, "api.example.com", 443, resolver=lambda _host: ["93.184.216.34"])
    assert ip == "93.184.216.34"

    with pytest.raises(EgressPolicyDenied) as exc:
        authorize_connect(loaded, "evil.example.com", 443, resolver=lambda _host: ["93.184.216.34"])
    assert exc.value.reason == "host_not_in_allowlist"


def test_derive_policy_empty_fails_closed():
    with pytest.raises(Exception):
        derive_egress_policy_for_agent(workspace_id=WORKSPACE, allowed_hosts=[], ttl_seconds=600)


def test_ensure_policy_from_grants_and_leases(db: Session, settings, policy_dir: Path):
    _grant(db, "always.example.com")
    _lease(db, "once.example.com")
    service = EgressApprovalService(db, WORKSPACE, settings)

    policy = service.ensure_agent_egress_policy()
    assert policy is not None
    hosts = {item.host for item in policy.hosts}
    assert hosts == {"always.example.com", "once.example.com"}
    assert (policy_dir / f"{WORKSPACE}.json").exists()


def test_ensure_policy_drops_consumed_lease(db: Session, settings, policy_dir: Path):
    _grant(db, "always.example.com")
    lease = _lease(db, "once.example.com")
    service = EgressApprovalService(db, WORKSPACE, settings)
    service.ensure_agent_egress_policy()
    assert "once.example.com" in {
        item.host
        for item in load_workspace_policy_file(str(policy_dir), WORKSPACE).hosts
    }

    service.consume_once(request_id=lease.id)
    policy = service.ensure_agent_egress_policy()
    hosts = {item.host for item in policy.hosts}
    assert "once.example.com" not in hosts
    assert "always.example.com" in hosts


def test_ensure_policy_no_approvals_fails_closed(db: Session, settings, policy_dir: Path):
    service = EgressApprovalService(db, WORKSPACE, settings)
    assert service.ensure_agent_egress_policy() is None
    assert not (policy_dir / f"{WORKSPACE}.json").exists()
