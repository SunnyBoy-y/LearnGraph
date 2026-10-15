"""A derived Agent egress snapshot must outlive every container it can authorize.

The snapshot is only rewritten when a sandbox container is created, while the
container may live until its session's *absolute* TTL
(``sandbox_workspace_absolute_ttl_seconds``, clamped by ``_touch_session``).
With two equal 24h lifetimes the expiries race, and a container used
continuously could lose egress silently. The effective default TTL is now
derived from the session lifetime (plus a margin) and still clamped by any
earlier lease/baseline expiry.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain import models as m
from app.providers.factory import ACCESS_ALLOWLIST_SETTING_KEY
from app.services.egress_approvals import EgressApprovalService
from app.services.sandbox_network_policy import (
    AGENT_EGRESS_POLICY_DEFAULT_TTL_SECONDS,
    AGENT_EGRESS_POLICY_MAX_TTL_SECONDS,
    AGENT_EGRESS_POLICY_TTL_MARGIN_SECONDS,
    agent_egress_policy_ttl_seconds,
)

WORKSPACE = "ws-agent-egress-ttl"


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
            m.Workspace(id=WORKSPACE, owner_user_id="user-owner", name="ttl workspace")
        )
        session.commit()
        yield session
    engine.dispose()


# ── agent_egress_policy_ttl_seconds ───────────────────────────────────────────


def test_default_ttl_covers_the_longest_legal_container_lifetime():
    assert (
        agent_egress_policy_ttl_seconds(86_400)
        == 86_400 + AGENT_EGRESS_POLICY_TTL_MARGIN_SECONDS
    )


def test_default_ttl_falls_back_when_the_session_lifetime_is_unknown():
    for value in (None, 0, -1, "nonsense"):
        assert (
            agent_egress_policy_ttl_seconds(value)
            == AGENT_EGRESS_POLICY_DEFAULT_TTL_SECONDS
            + AGENT_EGRESS_POLICY_TTL_MARGIN_SECONDS
        )


def test_default_ttl_uses_the_larger_of_both_container_bounds():
    """Raising the execution-container TTL cannot silently outlive the snapshot."""
    assert (
        agent_egress_policy_ttl_seconds(86_400, 2 * 86_400)
        == 2 * 86_400 + AGENT_EGRESS_POLICY_TTL_MARGIN_SECONDS
    )
    assert (
        agent_egress_policy_ttl_seconds(2 * 86_400, 3_600)
        == 2 * 86_400 + AGENT_EGRESS_POLICY_TTL_MARGIN_SECONDS
    )


def test_derived_ttl_covers_every_container_bound_in_the_real_settings():
    """Invariant guard: the snapshot must outlive any container that may hold it."""
    settings = Settings()
    bounds = (
        settings.sandbox_workspace_absolute_ttl_seconds,
        settings.sandbox_container_absolute_ttl_seconds,
    )
    assert agent_egress_policy_ttl_seconds(*bounds) > max(bounds)


def test_default_ttl_never_exceeds_the_hard_maximum():
    assert agent_egress_policy_ttl_seconds(30 * 86_400) == AGENT_EGRESS_POLICY_MAX_TTL_SECONDS


def test_tiny_session_lifetime_still_yields_a_usable_lifetime():
    # The margin carries a tiny session lifetime; the 60s floor guards the
    # degenerate case (an input so small the sum would be non-positive).
    assert agent_egress_policy_ttl_seconds(1) == 1 + AGENT_EGRESS_POLICY_TTL_MARGIN_SECONDS
    assert agent_egress_policy_ttl_seconds(1) >= 60


# ── the derived snapshot on disk ──────────────────────────────────────────────


def _written_policy(policy_dir: Path) -> dict:
    return json.loads((policy_dir / f"{WORKSPACE}.json").read_text(encoding="utf-8"))


def _lifetime_seconds(document: dict) -> float:
    issued = datetime.fromisoformat(document["issued_at"])
    expires = datetime.fromisoformat(document["expires_at"])
    return (expires - issued).total_seconds()


def test_derived_snapshot_uses_the_session_lifetime_plus_margin(db: Session, tmp_path: Path):
    db.add(
        m.WorkspaceSetting(
            workspace_id=WORKSPACE,
            key=ACCESS_ALLOWLIST_SETTING_KEY,
            value={"allow_all": True},
        )
    )
    db.commit()
    policy_dir = tmp_path / "egress-policies"
    settings = Settings(
        sandbox_egress_policy_dir=str(policy_dir),
        sandbox_workspace_absolute_ttl_seconds=86_400,
    )

    policy = EgressApprovalService(db, WORKSPACE, settings).ensure_agent_egress_policy()

    assert policy is not None
    assert _lifetime_seconds(_written_policy(policy_dir)) == pytest.approx(
        86_400 + AGENT_EGRESS_POLICY_TTL_MARGIN_SECONDS, abs=2
    )
    # The snapshot can no longer expire before the longest container it authorizes.
    assert policy.expires_at - policy.issued_at > timedelta(
        seconds=settings.sandbox_workspace_absolute_ttl_seconds
    )


def test_earlier_baseline_expiry_still_clamps_the_snapshot(db: Session, tmp_path: Path):
    """A deployment-reviewed baseline keeps its own, earlier expiry."""
    policy_dir = tmp_path / "egress-policies"
    policy_dir.mkdir()
    now = datetime.now(timezone.utc)
    baseline = {
        "workspace_id": WORKSPACE,
        "approval_id": "deployment-review",
        "issuer": "deployment_review",
        "capability": "RESTRICTED_EGRESS",
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=1800)).isoformat(),
        "hosts": [{"host": "example.com", "ports": [443], "protocols": ["https"]}],
    }
    (policy_dir / f"{WORKSPACE}.json").write_text(json.dumps(baseline), encoding="utf-8")
    settings = Settings(
        sandbox_egress_policy_dir=str(policy_dir),
        sandbox_workspace_absolute_ttl_seconds=86_400,
    )

    policy = EgressApprovalService(db, WORKSPACE, settings).ensure_agent_egress_policy()

    assert policy is not None
    lifetime = _lifetime_seconds(_written_policy(policy_dir))
    assert 60 <= lifetime <= 1800
    assert lifetime < 86_400
