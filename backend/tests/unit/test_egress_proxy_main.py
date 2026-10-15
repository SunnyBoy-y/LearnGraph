"""Standalone sandbox egress proxy entrypoint (egress_proxy_main)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.egress_proxy_main import DirectoryPolicyRegistry, EgressProxySettings
from app.services.sandbox_network_policy import validate_egress_policy


def _policy_document(
    workspace_id: str,
    host: str,
    *,
    issuer: str = "agent_egress_authorization",
    expires_in_seconds: int = 3600,
    allow_all_public: bool = False,
) -> dict:
    now = datetime.now(timezone.utc)
    document: dict = {
        "workspace_id": workspace_id,
        "approval_id": "test-approval",
        "issuer": issuer,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=expires_in_seconds)).isoformat(),
        "hosts": [{"host": host, "ports": [443], "protocols": ["https"]}],
    }
    if allow_all_public:
        document["allow_all_public"] = True
    return document


def _write_policy(directory: Path, filename: str, document: dict) -> Path:
    path = directory / filename
    path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_registry_loads_generic_and_fetch_policies(tmp_path: Path) -> None:
    generic = _write_policy(
        tmp_path, "ws-a.json", _policy_document("ws-a", "api.example.com")
    )
    fetch = _write_policy(
        tmp_path,
        "ws-a.web_fetch.json",
        _policy_document("ws-a", "fetch.example.com", issuer="web_fetch_policy"),
    )
    registry: dict = {}
    DirectoryPolicyRegistry(tmp_path).refresh_into(registry)

    assert len(registry) == 2
    for path in (generic, fetch):
        raw = json.loads(path.read_text(encoding="utf-8"))
        policy = validate_egress_policy(raw)
        assert registry[policy.digest].digest == policy.digest
        assert registry[policy.digest].workspace_id == policy.workspace_id


def test_registry_reuses_unchanged_files(tmp_path: Path) -> None:
    _write_policy(tmp_path, "ws-a.json", _policy_document("ws-a", "api.example.com"))
    source = DirectoryPolicyRegistry(tmp_path)
    first: dict = {}
    second: dict = {}
    source.refresh_into(first)
    source.refresh_into(second)

    first_policy = next(iter(first.values()))
    second_policy = next(iter(second.values()))
    assert first_policy is second_policy  # mtime cache returned the same object


def test_registry_picks_up_modified_policy(tmp_path: Path) -> None:
    path = _write_policy(tmp_path, "ws-a.json", _policy_document("ws-a", "api.example.com"))
    source = DirectoryPolicyRegistry(tmp_path)
    first: dict = {}
    source.refresh_into(first)
    original_digest = next(iter(first))
    previous_stat = path.stat()

    path.write_text(
        json.dumps(_policy_document("ws-a", "other.example.com"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # Rapid writes can share one filesystem clock tick. This case exercises
    # changed-mtime invalidation, so advance it explicitly without sleeping.
    os.utime(path, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns + 1_000_000_000))
    second: dict = {}
    source.refresh_into(second)

    assert original_digest not in second
    assert len(second) == 1
    assert next(iter(second.values())).hosts[0].host == "other.example.com"


def test_registry_drops_deleted_policy(tmp_path: Path) -> None:
    path = _write_policy(tmp_path, "ws-a.json", _policy_document("ws-a", "api.example.com"))
    source = DirectoryPolicyRegistry(tmp_path)
    first: dict = {}
    source.refresh_into(first)
    assert len(first) == 1

    path.unlink()
    second: dict = {"stale": "sentinel"}
    source.refresh_into(second)
    assert second == {}


def test_registry_skips_invalid_and_expired_files(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "expired.json").write_text(
        json.dumps(
            _policy_document("ws-expired", "api.example.com", expires_in_seconds=-60)
        ),
        encoding="utf-8",
    )
    _write_policy(tmp_path, "ws-good.json", _policy_document("ws-good", "api.example.com"))

    registry: dict = {}
    DirectoryPolicyRegistry(tmp_path).refresh_into(registry)
    assert len(registry) == 1
    assert next(iter(registry.values())).workspace_id == "ws-good"


def test_registry_handles_missing_directory(tmp_path: Path) -> None:
    registry: dict = {"stale": "sentinel"}
    DirectoryPolicyRegistry(tmp_path / "does-not-exist").refresh_into(registry)
    assert registry == {}


def test_settings_defaults() -> None:
    settings = EgressProxySettings()
    assert settings.host == "0.0.0.0"
    assert settings.port == 8888
    assert settings.refresh_seconds == 5.0
    assert settings.audit_log is None


def test_build_parser_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEARNGRAPH_SANDBOX_EGRESS_PROXY_PORT", "9999")
    monkeypatch.setenv("LEARNGRAPH_SANDBOX_EGRESS_POLICY_DIR", "/tmp/policies")

    from app.services.egress_proxy_main import build_parser

    args = build_parser().parse_args([])
    assert args.port == 9999
    assert args.policy_dir == "/tmp/policies"

    args = build_parser().parse_args(["--port", "7777"])
    assert args.port == 7777


def test_empty_registry_is_constructible_and_fails_closed() -> None:
    """A started proxy with no policies yet must refuse every CONNECT (403)."""
    from app.services.sandbox_egress_proxy import SandboxEgressProxy

    proxy = SandboxEgressProxy(policy_registry={})
    assert proxy._resolve_policy("any-digest") is None
    assert proxy._resolve_policy(None) is None
