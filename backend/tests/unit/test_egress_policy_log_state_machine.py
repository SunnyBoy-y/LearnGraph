"""Egress proxy policy-directory logging is state driven, not per refresh.

An expired snapshot is the steady state of an idle workspace — the API only
rewrites the file when a sandbox container is created or a fetch runs — so
reporting it on every refresh (default: every 5s) used to produce one log line
per file per cycle forever. The registry now reports each file once per revision
and per state transition, and offers a low-frequency summary, without changing
what is registered (fail-closed behaviour is identical).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services import egress_proxy_main
from app.services.egress_proxy_main import DirectoryPolicyRegistry
from app.services.sandbox_network_policy import (
    derive_egress_policy_for_agent,
)

LOGGER_NAME = "app.services.egress_proxy_main"
WORKSPACE = "ws-log-state-machine"


def _write_agent_policy(
    path: Path,
    *,
    issued_at: datetime,
    ttl_seconds: int = 86_400,
    hosts: tuple[str, ...] = ("example.com",),
) -> None:
    policy = derive_egress_policy_for_agent(
        workspace_id=WORKSPACE,
        allowed_hosts=hosts,
        ttl_seconds=ttl_seconds,
        now=issued_at,
    )
    path.write_text(json.dumps(policy.raw), encoding="utf-8")


def _touch_mtime(path: Path) -> None:
    """Force a distinct mtime so a rewrite is never mistaken for a re-read."""
    stamp = path.stat().st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(stamp, stamp))


def _messages(caplog, level: str, needle: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelname == level and needle in record.getMessage()
    ]


def _expired_policy_dir(tmp_path: Path, *, age_days: int = 3) -> tuple[Path, Path]:
    policy_dir = tmp_path / "egress-policies"
    policy_dir.mkdir()
    path = policy_dir / f"{WORKSPACE}.json"
    _write_agent_policy(
        path, issued_at=datetime.now(timezone.utc) - timedelta(days=age_days)
    )
    return policy_dir, path


def test_expired_snapshot_is_reported_once_per_revision(tmp_path, caplog):
    policy_dir, _path = _expired_policy_dir(tmp_path)
    registry = DirectoryPolicyRegistry(policy_dir)
    registered: dict = {}

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        for _ in range(4):
            registry.refresh_into(registered)

    warnings = _messages(caplog, "WARNING", "policy_expired")
    assert len(warnings) == 1
    # Fail-closed behaviour is unchanged: an expired file is never registered.
    assert registered == {}
    # An expected expiry must not be reported as an error any more.
    assert _messages(caplog, "ERROR", "is invalid") == []


def test_rewritten_expired_snapshot_is_reported_again(tmp_path, caplog):
    policy_dir, path = _expired_policy_dir(tmp_path)
    registry = DirectoryPolicyRegistry(policy_dir)
    registered: dict = {}

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        registry.refresh_into(registered)
        assert len(_messages(caplog, "WARNING", "policy_expired")) == 1
        # A new revision (new mtime) is new information -> one more line.
        _touch_mtime(path)
        registry.refresh_into(registered)
        assert len(_messages(caplog, "WARNING", "policy_expired")) == 2
        # Unchanged revision -> quiet again.
        registry.refresh_into(registered)
        assert len(_messages(caplog, "WARNING", "policy_expired")) == 2


def test_recovery_is_reported_once(tmp_path, caplog):
    policy_dir, path = _expired_policy_dir(tmp_path)
    registry = DirectoryPolicyRegistry(policy_dir)
    registered: dict = {}

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        registry.refresh_into(registered)
        assert registered == {}

        _write_agent_policy(path, issued_at=datetime.now(timezone.utc))
        _touch_mtime(path)
        registry.refresh_into(registered)

        assert len(registered) == 1
        recovered = _messages(caplog, "INFO", "valid again")
        assert len(recovered) == 1
        # A valid, unchanged file stays silent on every later refresh.
        registry.refresh_into(registered)
        assert len(_messages(caplog, "INFO", "valid again")) == 1


def test_malformed_snapshot_is_reported_once_as_error(tmp_path, caplog):
    policy_dir = tmp_path / "egress-policies"
    policy_dir.mkdir()
    path = policy_dir / f"{WORKSPACE}.json"
    # Unexpired but structurally invalid: the validation error, not the clock,
    # is what gets reported (and it must not become a per-refresh error either).
    _write_agent_policy(path, issued_at=datetime.now(timezone.utc))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["hosts"][0]["ports"] = [8443]
    raw["hosts"][0]["protocols"] = ["https"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    registry = DirectoryPolicyRegistry(policy_dir)
    registered: dict = {}

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        for _ in range(3):
            registry.refresh_into(registered)

    errors = _messages(caplog, "ERROR", "policy_port_must_be_http_https")
    assert len(errors) == 1
    assert _messages(caplog, "WARNING", "policy_expired") == []


def test_summary_is_silent_while_everything_is_healthy(tmp_path):
    policy_dir = tmp_path / "egress-policies"
    policy_dir.mkdir()
    _write_agent_policy(
        policy_dir / f"{WORKSPACE}.json", issued_at=datetime.now(timezone.utc)
    )
    registry = DirectoryPolicyRegistry(policy_dir)
    registered: dict = {}
    registry.refresh_into(registered)

    assert registry.summary(registered) is None


def test_summary_reports_expired_and_invalid_snapshots(tmp_path):
    policy_dir, _expired = _expired_policy_dir(tmp_path)
    broken = policy_dir / "ws-broken-policy.json"
    broken.write_text("{ not json", encoding="utf-8")
    registry = DirectoryPolicyRegistry(policy_dir)
    registered: dict = {}
    registry.refresh_into(registered)

    summary = registry.summary(registered)
    assert summary is not None
    assert "policy_files=2" in summary
    assert "expired_on_disk=1" in summary
    assert "invalid=1" in summary
    assert WORKSPACE in summary
    assert "ws-broken-policy" in summary


def test_cached_snapshot_that_expires_later_is_surfaced_by_the_summary(tmp_path, monkeypatch):
    """A snapshot cached while valid stays registered after it expires.

    That is deliberate (the digest keeps resolving so the deny reason stays
    ``policy_expired``, and ``authorize_connect`` re-checks expiry at CONNECT
    time), but it means no per-file line appears at the moment it expires — the
    summary is what keeps it visible.
    """

    frozen = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    policy_dir = tmp_path / "egress-policies"
    policy_dir.mkdir()
    _write_agent_policy(
        policy_dir / f"{WORKSPACE}.json", issued_at=frozen, ttl_seconds=3600
    )
    registry = DirectoryPolicyRegistry(policy_dir)
    registered: dict = {}

    monkeypatch.setattr(egress_proxy_main, "utc_now", lambda: frozen)
    registry.refresh_into(registered)
    assert len(registered) == 1
    assert registry.summary(registered) is None

    later = frozen + timedelta(hours=2)
    monkeypatch.setattr(egress_proxy_main, "utc_now", lambda: later)
    registry.refresh_into(registered)

    assert len(registered) == 1  # mtime cache hit: still registered
    summary = registry.summary(registered)
    assert summary is not None
    assert "registered_expired=1" in summary
    assert "expired_on_disk=0" in summary
