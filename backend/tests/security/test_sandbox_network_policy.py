from __future__ import annotations

from datetime import timedelta

import pytest

from app.services.sandbox_network_policy import (
    EgressPolicy,
    EgressPolicyDenied,
    EgressPolicyInvalid,
    PolicyHost,
    authorize_connect,
    classify_ip_address,
    load_workspace_policy_file,
    normalize_hostname,
    utc_now,
    validate_egress_policy,
)


def valid_policy_data(**overrides):
    data = {
        "workspace_id": "workspace-a",
        "approval_id": "approval-1",
        "issuer": "platform-admin",
        "issued_at": (utc_now() - timedelta(days=1)).isoformat(),
        "expires_at": (utc_now() + timedelta(days=7)).isoformat(),
        "hosts": [
            {
                "host": "api.example.test",
                "ports": [443],
                "protocols": ["https"],
            }
        ],
    }
    data.update(overrides)
    return data


def make_policy(**overrides) -> EgressPolicy:
    return validate_egress_policy(valid_policy_data(**overrides))


def test_normalize_hostname_canonicalizes_and_rejects() -> None:
    assert normalize_hostname("API.Example.TEST.") == "api.example.test"
    assert normalize_hostname("bücher.example") == "xn--bcher-kva.example"
    for bad in (
        "127.0.0.1",
        "2001:db8::1",
        "*.example.test",
        "example.test.*",
        "localhost",
        "single",
        "-bad.example",
        "bad-.example",
    ):
        with pytest.raises(EgressPolicyInvalid):
            normalize_hostname(bad)


def test_classify_ip_address_denies_private_and_metadata() -> None:
    public_addresses = ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "140.82.112.4")
    for address in public_addresses:
        assert classify_ip_address(address) == "public"

    denied = {
        "127.0.0.1": "loopback",
        "::1": "loopback",
        "0.0.0.0": "unspecified",
        "::": "unspecified",
        "10.0.0.5": "private",
        "172.16.0.5": "private",
        "172.31.255.254": "private",
        "192.168.1.1": "private",
        "fc00::1": "private",
        "169.254.169.254": "metadata",
        "100.100.100.200": "metadata",
        "192.0.0.192": "metadata",
        "169.254.10.10": "link_local",
        "fe80::1": "link_local",
        "224.0.0.1": "multicast",
        "ff02::1": "multicast",
        "100.64.0.1": "carrier_grade_nat",
        "255.255.255.255": "broadcast",
        "192.0.2.1": "documentation",
        "2001:db8::1": "documentation",
        "not-an-ip": "invalid",
    }
    for address, expected in denied.items():
        assert classify_ip_address(address) == expected, address


def test_validate_egress_policy_accepts_reviewed_document() -> None:
    policy = make_policy()
    assert policy.workspace_id == "workspace-a"
    assert policy.hosts[0].host == "api.example.test"
    assert policy.digest


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"expires_at": (utc_now() - timedelta(days=1)).isoformat(), "issued_at": (utc_now() - timedelta(days=3)).isoformat()}, "policy_expired"),
        ({"approval_id": ""}, "policy_missing_approval"),
        ({"workspace_id": ""}, "policy_missing_workspace"),
        ({"issuer": ""}, "policy_missing_issuer"),
        ({"hosts": []}, "policy_empty_hosts"),
        ({"hosts": [{"host": "*.example.test", "ports": [443]}]}, "hostname_label_invalid"),
        ({"hosts": [{"host": "10.0.0.1", "ports": [443]}]}, "hostname_must_be_domain"),
        ({"hosts": [{"host": "api.example.test", "ports": [443], "protocols": ["http"]}]}, "policy_non_https_protocol"),
        ({"hosts": [{"host": "api.example.test", "ports": [0]}]}, "policy_port_invalid"),
        ({"hosts": [{"host": "api.example.test", "ports": []}]}, "policy_host_no_ports"),
    ],
)
def test_validate_egress_policy_rejects_invalid(overrides, reason) -> None:
    with pytest.raises(EgressPolicyInvalid) as exc:
        validate_egress_policy(valid_policy_data(**overrides))
    assert exc.value.reason == reason


def test_authorize_connect_allows_reviewed_public_target() -> None:
    policy = make_policy()
    target_ip, audit = authorize_connect(
        policy,
        "api.example.test",
        443,
        resolver=lambda host: ["93.184.216.34"],
    )
    assert target_ip == "93.184.216.34"
    assert audit["policy_digest"] == policy.digest
    assert audit["approval_id"] == "approval-1"
    assert audit["host"] == "api.example.test"


def test_authorize_connect_denies_unknown_host_and_port() -> None:
    policy = make_policy()
    with pytest.raises(EgressPolicyDenied) as exc:
        authorize_connect(policy, "other.example.test", 443, resolver=lambda host: ["8.8.8.8"])
    assert exc.value.reason == "host_not_in_allowlist"
    with pytest.raises(EgressPolicyDenied) as exc:
        authorize_connect(policy, "api.example.test", 22, resolver=lambda host: ["93.184.216.34"])
    assert exc.value.reason == "port_not_allowed"


def test_authorize_connect_fails_closed_on_dns_rebinding() -> None:
    policy = make_policy()
    # Resolver now returns a loopback address: the connection must be refused
    # even though the hostname is allowlisted.
    with pytest.raises(EgressPolicyDenied) as exc:
        authorize_connect(policy, "api.example.test", 443, resolver=lambda host: ["127.0.0.1"])
    assert exc.value.reason == "dns_address_classified_forbidden"

    # Mixed public + private answers are refused outright.
    with pytest.raises(EgressPolicyDenied) as exc:
        authorize_connect(
            policy,
            "api.example.test",
            443,
            resolver=lambda host: ["93.184.216.34", "10.0.0.5"],
        )
    assert exc.value.reason == "dns_address_classified_forbidden"

    # No answers is a hard denial.
    with pytest.raises(EgressPolicyDenied) as exc:
        authorize_connect(policy, "api.example.test", 443, resolver=lambda host: [])
    assert exc.value.reason == "dns_no_addresses"


def test_authorize_connect_denies_expired_policy_and_ip_literal() -> None:
    expired = EgressPolicy(
        workspace_id="workspace-a",
        approval_id="approval-1",
        issued_at=utc_now() - timedelta(days=2),
        expires_at=utc_now() - timedelta(days=1),
        hosts=(PolicyHost(host="api.example.test", ports=(443,)),),
        issuer="platform-admin",
        digest="d" * 64,
        raw={},
    )
    with pytest.raises(EgressPolicyDenied) as exc:
        authorize_connect(expired, "api.example.test", 443, resolver=lambda host: ["8.8.8.8"])
    assert exc.value.reason == "policy_expired"

    with pytest.raises(EgressPolicyDenied) as exc:
        authorize_connect(make_policy(), "10.0.0.1", 443, resolver=lambda host: [])
    assert exc.value.reason == "host_not_normalizable"


def test_load_workspace_policy_file_is_fail_closed(tmp_path) -> None:
    import json

    # Missing policy -> offline.
    assert load_workspace_policy_file(tmp_path, "workspace-a") is None

    # Malformed policy -> offline with an error log.
    (tmp_path / "workspace-a.json").write_text("{not json", encoding="utf-8")
    assert load_workspace_policy_file(tmp_path, "workspace-a") is None

    # Expired policy -> offline.
    (tmp_path / "workspace-a.json").write_text(
        json.dumps(valid_policy_data(expires_at=(utc_now() - timedelta(days=1)).isoformat())),
        encoding="utf-8",
    )
    assert load_workspace_policy_file(tmp_path, "workspace-a") is None

    # Valid reviewed policy -> usable.
    (tmp_path / "workspace-a.json").write_text(
        json.dumps(valid_policy_data()),
        encoding="utf-8",
    )
    policy = load_workspace_policy_file(tmp_path, "workspace-a")
    assert policy is not None
    assert policy.workspace_id == "workspace-a"
