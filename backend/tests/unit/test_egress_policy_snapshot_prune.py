"""Derived egress-policy snapshots are reclaimed only when provably dead *and* unused.

``/data/egress-policies`` is a derived cache: ``{workspace_id}.json`` and
``{workspace_id}.web_fetch.json`` are written by the API and read by the egress
proxy. Nothing else owned their lifecycle, so snapshots of withdrawn
authorizations (and of deleted workspaces) stayed behind forever.

These tests pin the gates that make the sweep safe to run against real user
workspaces: an expired snapshot that is still authorized is never touched, a
snapshot that might still be held by a live sandbox container is never touched,
a deployment-reviewed baseline (foreign issuer) is never touched, and the file
is re-read immediately before unlinking so a concurrent rewrite survives.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain import models as m
from app.providers.factory import ACCESS_ALLOWLIST_SETTING_KEY, SANDBOX_EGRESS_SETTING_KEY
from app.services.egress_policy_maintenance import (
    DEFAULT_PRUNE_GRACE_SECONDS,
    _delete_if_unchanged,
    prune_derived_egress_policies,
)
from app.services.sandbox_network_policy import (
    AGENT_EGRESS_POLICY_ISSUER,
    WEB_FETCH_POLICY_ISSUER,
    derive_egress_policy_for_agent,
    derive_egress_policy_for_fetch,
)

WORKSPACE = "ws-prune-policy-snapshots"


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


@pytest.fixture()
def policy_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "egress-policies"
    directory.mkdir()
    return directory


def _settings(policy_dir: Path) -> Settings:
    return Settings(sandbox_egress_policy_dir=str(policy_dir))


def _add_workspace(db: Session, workspace_id: str = WORKSPACE) -> None:
    db.add(
        m.Workspace(id=workspace_id, owner_user_id="user-owner", name="real workspace")
    )
    db.commit()


def _set_setting(db: Session, key: str, value: dict, workspace_id: str = WORKSPACE) -> None:
    db.add(m.WorkspaceSetting(workspace_id=workspace_id, key=key, value=value))
    db.commit()


def _write_expired_agent_snapshot(
    policy_dir: Path,
    *,
    age_days: int = 3,
    workspace_id: str = WORKSPACE,
    ttl_seconds: int = 86_400,
) -> Path:
    policy = derive_egress_policy_for_agent(
        workspace_id=workspace_id,
        allowed_hosts=("example.com",),
        ttl_seconds=ttl_seconds,
        now=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    path = policy_dir / f"{workspace_id}.json"
    path.write_text(json.dumps(policy.raw), encoding="utf-8")
    return path


def _write_expired_fetch_snapshot(policy_dir: Path, *, age_days: int = 3) -> Path:
    policy = derive_egress_policy_for_fetch(
        workspace_id=WORKSPACE,
        allowed_domains=["example.com"],
        now=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    path = policy_dir / f"{WORKSPACE}.web_fetch.json"
    path.write_text(json.dumps(policy.raw), encoding="utf-8")
    return path


# ── gates ─────────────────────────────────────────────────────────────────────


def test_keeps_expired_snapshot_while_authorization_remains(db: Session, policy_dir: Path):
    """`allow_all` is still on -> re-deriving would produce a live snapshot."""
    _add_workspace(db)
    _set_setting(db, ACCESS_ALLOWLIST_SETTING_KEY, {"allow_all": True})
    path = _write_expired_agent_snapshot(policy_dir)

    totals = prune_derived_egress_policies(db, _settings(policy_dir))

    assert totals["pruned"] == 0
    assert totals["kept"] == 1
    assert path.exists()


def test_keeps_expired_snapshot_while_allowlist_domains_remain(db: Session, policy_dir: Path):
    _add_workspace(db)
    _set_setting(
        db, ACCESS_ALLOWLIST_SETTING_KEY, {"allow_all": False, "allowed_domains": ["ok.com"]}
    )
    path = _write_expired_agent_snapshot(policy_dir)

    totals = prune_derived_egress_policies(db, _settings(policy_dir))

    assert totals["pruned"] == 0
    assert path.exists()


def test_keeps_expired_snapshot_while_sandbox_public_network_switch_is_on(
    db: Session, policy_dir: Path
):
    _add_workspace(db)
    _set_setting(db, SANDBOX_EGRESS_SETTING_KEY, {"allow_public_network": True})
    path = _write_expired_agent_snapshot(policy_dir)

    totals = prune_derived_egress_policies(db, _settings(policy_dir))

    assert totals["pruned"] == 0
    assert path.exists()


def test_prunes_expired_snapshot_once_every_gate_is_clear(db: Session, policy_dir: Path, caplog):
    _add_workspace(db)  # workspace exists, but no authorization of any kind
    path = _write_expired_agent_snapshot(policy_dir)

    with caplog.at_level(logging.INFO, logger="app.services.egress_policy_maintenance"):
        totals = prune_derived_egress_policies(db, _settings(policy_dir))

    assert totals["pruned"] == 1
    assert not path.exists()
    assert [item.reason for item in totals["pruned_files"]] == ["authorization_gone"]
    assert "Pruned derived egress policy snapshot" in caplog.text


def test_prunes_expired_fetch_snapshot_when_the_allowlist_is_gone(db: Session, policy_dir: Path):
    _add_workspace(db)
    path = _write_expired_fetch_snapshot(policy_dir)

    totals = prune_derived_egress_policies(db, _settings(policy_dir))

    assert totals["pruned"] == 1
    assert not path.exists()


def test_keeps_recently_expired_snapshot_until_the_grace_period_passes(
    db: Session, policy_dir: Path
):
    _add_workspace(db)
    path = _write_expired_agent_snapshot(policy_dir, age_days=1, ttl_seconds=86_400)

    totals = prune_derived_egress_policies(db, _settings(policy_dir))

    # Expired ~0 minutes ago -> inside the default 1h grace.
    assert totals["pruned"] == 0
    assert totals["kept"] == 1
    assert path.exists()

    # The same file is reclaimable once the grace period has passed.
    later = datetime.now(timezone.utc) + timedelta(hours=3)
    totals = prune_derived_egress_policies(
        db, _settings(policy_dir), now=later, grace_seconds=DEFAULT_PRUNE_GRACE_SECONDS
    )
    assert totals["pruned"] == 1
    assert not path.exists()


def test_prunes_snapshot_of_a_missing_workspace(db: Session, policy_dir: Path):
    path = _write_expired_agent_snapshot(policy_dir)  # no Workspace row at all

    totals = prune_derived_egress_policies(db, _settings(policy_dir))

    assert totals["pruned"] == 1
    assert [item.reason for item in totals["pruned_files"]] == ["workspace_missing"]
    assert not path.exists()


def test_keeps_snapshot_while_a_sandbox_session_is_live(db: Session, policy_dir: Path):
    _add_workspace(db)
    db.add(
        m.ChatSession(workspace_id=WORKSPACE, id="chat-prune-live", title="prune live")
    )
    db.add(
        m.SandboxSession(
            workspace_id=WORKSPACE,
            owner_user_id="user-owner",
            chat_session_id="chat-prune-live",
            manifest_hash="manifest",
            lifecycle_state="RUNNING",
            status="ready",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db.commit()
    path = _write_expired_agent_snapshot(policy_dir)

    totals = prune_derived_egress_policies(db, _settings(policy_dir))

    assert totals["pruned"] == 0
    assert totals["kept"] == 1
    assert path.exists()


def test_never_prunes_a_deployment_reviewed_baseline(db: Session, policy_dir: Path):
    """A reviewed baseline carries a different issuer: it is not ours to remove."""
    _add_workspace(db)
    baseline = {
        "workspace_id": WORKSPACE,
        "approval_id": "deployment-review",
        "issuer": "deployment_review",
        "capability": "RESTRICTED_EGRESS",
        "issued_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        "expires_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
        "hosts": [{"host": "example.com", "ports": [443], "protocols": ["https"]}],
    }
    path = policy_dir / f"{WORKSPACE}.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")

    totals = prune_derived_egress_policies(db, _settings(policy_dir))

    assert totals["pruned"] == 0
    assert totals["skipped"] == 1
    assert path.exists()


def test_never_prunes_a_snapshot_whose_document_does_not_match_its_name(
    db: Session, policy_dir: Path
):
    """A copy of another workspace's snapshot must not be deleted by name alone."""
    _add_workspace(db)
    policy = derive_egress_policy_for_agent(
        workspace_id="some-other-workspace",
        allowed_hosts=("example.com",),
        now=datetime.now(timezone.utc) - timedelta(days=3),
    )
    path = policy_dir / f"{WORKSPACE}.json"
    path.write_text(json.dumps(policy.raw), encoding="utf-8")

    totals = prune_derived_egress_policies(db, _settings(policy_dir))

    assert totals["pruned"] == 0
    assert path.exists()


def test_never_prunes_an_unparsable_file(db: Session, policy_dir: Path):
    _add_workspace(db)
    path = policy_dir / f"{WORKSPACE}.json"
    path.write_text("{ not json", encoding="utf-8")

    totals = prune_derived_egress_policies(db, _settings(policy_dir))

    assert totals["pruned"] == 0
    assert path.exists()


def test_ignores_files_it_does_not_own(db: Session, policy_dir: Path):
    _add_workspace(db)
    # Leading dot: not a ``{workspace_id}.json`` name, so it is never a candidate.
    foreign = policy_dir / ".hidden.json"
    foreign.write_text("{}", encoding="utf-8")

    totals = prune_derived_egress_policies(db, _settings(policy_dir))

    assert totals["scanned"] == 0
    assert totals["skipped"] == 1
    assert foreign.exists()


def test_missing_directory_is_a_noop(db: Session, tmp_path: Path):
    totals = prune_derived_egress_policies(db, _settings(tmp_path / "absent"))

    assert totals["pruned"] == 0
    assert totals["scanned"] == 0


def test_compare_and_delete_refuses_a_file_that_changed(policy_dir: Path):
    path = policy_dir / f"{WORKSPACE}.json"
    path.write_text("original", encoding="utf-8")

    assert _delete_if_unchanged(path, b"something else") is False
    assert path.exists()

    assert _delete_if_unchanged(path, b"original") is True
    assert not path.exists()


def test_owned_issuers_are_the_derived_ones():
    """Guard the two constants the sweep trusts (a typo here would delete nothing)."""
    assert AGENT_EGRESS_POLICY_ISSUER == "agent_egress_authorization"
    assert WEB_FETCH_POLICY_ISSUER == "web_fetch_policy"
