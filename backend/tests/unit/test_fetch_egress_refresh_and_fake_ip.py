"""Web-fetch egress policy refresh + fake-IP classifier (SSRF gate) fixes.

Covers the two root causes behind "fetch fails even after the domain was
approved":

1. ``refresh_workspace_fetch_policy_file`` — the derived web-fetch egress
   policy has a short TTL (600s default) and the warm container pool reuses
   containers without re-deriving the envelope. Refresh on every fetch, but
   skip the disk write when the on-disk policy is still valid and unchanged.
2. ``ssrf_fake_ip_ranges`` — a Clash-style local proxy in fake-ip mode answers
   DNS with synthetic 198.18.x.x addresses that Python classifies as private,
   so the fetch SSRF gate and the egress address classifier reject every URL.
   The configured ranges are treated as trusted fake-IP space; empty keeps the
   strict public-only classifier.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import ipaddress
import pytest

from app.providers.remote.fetch import (
    UnsafeFetchURL,
    _resolve_public_host,
    require_public_http_url,
)
from app.services.sandbox_network_policy import (
    WEB_FETCH_POLICY_DEFAULT_TTL_SECONDS,
    EgressPolicyInvalid,
    classify_ip_address,
    derive_egress_policy_for_fetch,
    refresh_workspace_fetch_policy_file,
    store_workspace_fetch_policy_file,
)

WORKSPACE = "ws-web-fetch-refresh"


@pytest.fixture()
def policy_dir(tmp_path: Path) -> Path:
    return tmp_path / "egress-policies"


# ── refresh_workspace_fetch_policy_file ───────────────────────────────────────


def test_refresh_writes_on_first_call(policy_dir: Path):
    written = refresh_workspace_fetch_policy_file(
        policy_dir, WORKSPACE, ["example.com"]
    )
    assert written is True
    assert (policy_dir / f"{WORKSPACE}.web_fetch.json").exists()


def test_refresh_skips_when_valid_and_unchanged(policy_dir: Path):
    assert refresh_workspace_fetch_policy_file(
        policy_dir, WORKSPACE, ["example.com"]
    ) is True
    path = policy_dir / f"{WORKSPACE}.web_fetch.json"
    mtime = path.stat().st_mtime_ns
    # Same allowlist, policy still unexpired -> no disk write.
    assert refresh_workspace_fetch_policy_file(
        policy_dir, WORKSPACE, ["example.com"]
    ) is False
    assert path.stat().st_mtime_ns == mtime


def test_refresh_rewrites_when_allowlist_changed(policy_dir: Path):
    assert refresh_workspace_fetch_policy_file(
        policy_dir, WORKSPACE, ["example.com"]
    ) is True
    path = policy_dir / f"{WORKSPACE}.web_fetch.json"
    mtime = path.stat().st_mtime_ns
    assert refresh_workspace_fetch_policy_file(
        policy_dir, WORKSPACE, ["example.com", "example.org"]
    ) is True
    assert path.stat().st_mtime_ns != mtime


def test_refresh_rewrites_expired_policy(policy_dir: Path):
    # Issue a policy far enough in the past that it is already expired by the
    # refresh time (TTL is the 1-day max, so "past" must be older than that).
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=WEB_FETCH_POLICY_DEFAULT_TTL_SECONDS + 60
    )
    assert refresh_workspace_fetch_policy_file(
        policy_dir, WORKSPACE, ["example.com"], now=stale
    ) is True
    assert refresh_workspace_fetch_policy_file(
        policy_dir, WORKSPACE, ["example.com"]
    ) is True  # expired -> rewritten


def test_refresh_keeps_digest_stable_within_ttl(policy_dir: Path):
    # The digest is the identity warm pooled runner containers carry; rewriting
    # the file would rotate it and break every pooled container until evicted.
    # Within the TTL and with an unchanged allowlist the refresh must be a
    # no-op so the on-disk policy (and therefore its digest) stays stable.
    assert refresh_workspace_fetch_policy_file(
        policy_dir, WORKSPACE, ["example.com"]
    ) is True
    path = policy_dir / f"{WORKSPACE}.web_fetch.json"
    before = path.read_text(encoding="utf-8")
    mtime = path.stat().st_mtime_ns
    assert refresh_workspace_fetch_policy_file(
        policy_dir, WORKSPACE, ["example.com"]
    ) is False
    assert path.read_text(encoding="utf-8") == before
    assert path.stat().st_mtime_ns == mtime


def test_refresh_allow_all_idempotent(policy_dir: Path):
    assert refresh_workspace_fetch_policy_file(
        policy_dir, WORKSPACE, [], allow_all_public=True
    ) is True
    path = policy_dir / f"{WORKSPACE}.web_fetch.json"
    mtime = path.stat().st_mtime_ns
    assert refresh_workspace_fetch_policy_file(
        policy_dir, WORKSPACE, [], allow_all_public=True
    ) is False
    assert path.stat().st_mtime_ns == mtime


# ── ssrf_fake_ip_ranges: fetch SSRF gate ──────────────────────────────────────


def test_fake_ip_blocked_by_default():
    with pytest.raises(UnsafeFetchURL):
        _resolve_public_host("198.18.0.5")  # fake-ip segment, private per Python


def test_fake_ip_allowed_when_configured(monkeypatch):
    settings = type("FakeSettings", (), {"ssrf_fake_ip_ranges": "198.18.0.0/15"})()
    monkeypatch.setattr(
        "app.providers.remote.fetch.get_settings", lambda: settings
    )
    # Fake-IP addresses pass the resolver gate…
    assert _resolve_public_host("198.18.0.5") == "198.18.0.5"
    # …and the full require_public_http_url path accepts them.
    require_public_http_url("https://198.18.0.5/x", {"198.18.0.5"})


def test_fake_ip_config_does_not_weaken_private_block(monkeypatch):
    settings = type("FakeSettings", (), {"ssrf_fake_ip_ranges": "198.18.0.0/15"})()
    monkeypatch.setattr(
        "app.providers.remote.fetch.get_settings", lambda: settings
    )
    with pytest.raises(UnsafeFetchURL):
        _resolve_public_host("10.0.0.1")  # real private stays blocked
    with pytest.raises(UnsafeFetchURL):
        _resolve_public_host("192.168.1.1")


# ── ssrf_fake_ip_ranges: egress proxy address classifier ──────────────────────


def test_classify_fake_ip_public_by_default():
    # 198.18.0.0/15 is not in FORBIDDEN_FAMILIES, so it already classifies
    # as public; the fake-ip override must not change that.
    assert classify_ip_address("198.18.0.5") == "public"
    assert classify_ip_address("10.0.0.1") == "private"
    assert classify_ip_address("127.0.0.1") == "loopback"


def test_classify_fake_ip_override_keeps_private_denied(monkeypatch):
    settings = type("FakeSettings", (), {"ssrf_fake_ip_ranges": "198.18.0.0/15"})()
    monkeypatch.setattr(
        "app.services.sandbox_network_policy.get_settings", lambda: settings
    )
    assert classify_ip_address("198.18.0.5") == "public"
    assert classify_ip_address("10.0.0.1") == "private"
