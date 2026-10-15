"""Abuse regressions for the sandbox network broker and egress classifier."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import sandbox_egress_proxy as proxy_module
from app.services.external_acquisition import ExternalAcquisitionService
from app.services.sandbox_egress_proxy import SandboxEgressProxy
from app.services.sandbox_network_policy import (
    EgressPolicyDenied,
    EgressPolicyInvalid,
    NetworkCapability,
    authorize_connect,
    classify_ip_address,
    validate_egress_policy,
)


def _policy(
    *,
    hosts: list[dict] | None = None,
    capability: NetworkCapability = NetworkCapability.FETCH,
    max_requests: int | None = None,
    max_bytes: int | None = None,
    max_concurrency: int | None = None,
):
    now = datetime.now(timezone.utc)
    document = {
        "workspace_id": "ws-test",
        "approval_id": "approval-test",
        "issuer": "test",
        "capability": capability.value,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "hosts": hosts
        or [
            {
                "host": "example.com",
                "ports": [80, 443],
                "protocols": ["http", "https"],
            }
        ],
    }
    for name, value in (
        ("max_requests", max_requests),
        ("max_bytes", max_bytes),
        ("max_concurrency", max_concurrency),
    ):
        if value is not None:
            document[name] = value
    return validate_egress_policy(document)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("::ffff:127.0.0.1", "loopback"),
        ("::ffff:10.0.0.1", "private"),
        ("10.0.0.1", "private"),
        ("172.16.0.1", "private"),
        ("192.168.1.1", "private"),
        ("169.254.169.254", "metadata"),
        ("fe80::1", "link_local"),
        ("240.0.0.1", "reserved"),
        ("198.18.0.5", "benchmarking"),
    ],
)
def test_ip_classifier_blocks_non_public_and_mapped_addresses(
    monkeypatch: pytest.MonkeyPatch, address: str, expected: str
) -> None:
    monkeypatch.setattr(
        "app.services.sandbox_network_policy.get_settings",
        lambda: SimpleNamespace(sandbox_deny_cidrs="", ssrf_fake_ip_ranges=""),
    )
    assert classify_ip_address(address) == expected


def test_fake_ip_range_cannot_override_private_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.sandbox_network_policy.get_settings",
        lambda: SimpleNamespace(
            sandbox_deny_cidrs="",
            ssrf_fake_ip_ranges="10.0.0.0/8,198.18.0.0/15",
        ),
    )
    assert classify_ip_address("10.1.2.3") == "private"
    assert classify_ip_address("198.18.0.5") == "public"


def test_deployment_deny_cidrs_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.sandbox_network_policy.get_settings",
        lambda: SimpleNamespace(
            sandbox_deny_cidrs="203.0.113.0/24,2001:db8:ffff::/48",
            ssrf_fake_ip_ranges="",
        ),
    )
    assert classify_ip_address("203.0.113.9") == "deployment_denied"
    assert classify_ip_address("2001:db8:ffff::1") == "deployment_denied"


@pytest.mark.parametrize("host", ["127.0.0.1", "0177.0.0.1", "2130706433"])
def test_authorize_connect_rejects_direct_or_legacy_ip_spellings(host: str) -> None:
    policy = _policy()
    with pytest.raises(EgressPolicyDenied):
        authorize_connect(
            policy,
            host,
            80,
            protocol="http",
            resolver=lambda _host: ["127.0.0.1"],
        )


def test_authorize_connect_rechecks_every_resolved_address() -> None:
    policy = _policy()
    with pytest.raises(EgressPolicyDenied) as exc:
        authorize_connect(
            policy,
            "example.com",
            443,
            resolver=lambda _host: ["93.184.216.34", "10.0.0.10"],
        )
    assert exc.value.reason == "dns_address_classified_forbidden"
    assert exc.value.details["forbidden_answers"] == [("10.0.0.10", "private")]


def test_policy_rejects_ssh_smtp_and_protocol_port_mismatch() -> None:
    with pytest.raises(EgressPolicyInvalid):
        _policy(
            hosts=[
                {
                    "host": "example.com",
                    "ports": [22],
                    "protocols": ["https"],
                }
            ]
        )
    with pytest.raises(EgressPolicyInvalid):
        _policy(
            hosts=[
                {
                    "host": "example.com",
                    "ports": [80],
                    "protocols": ["https"],
                }
            ]
        )


def test_connect_never_accepts_non_https_port() -> None:
    policy = _policy()
    with pytest.raises(EgressPolicyDenied) as exc:
        authorize_connect(
            policy,
            "example.com",
            22,
            resolver=lambda _host: ["93.184.216.34"],
        )
    assert exc.value.reason == "port_protocol_not_allowed"


def test_research_profile_can_download_files_without_direct_sandbox_egress() -> None:
    from app.services.agent_execution_profiles import resolve_agent_execution_profile

    profile = resolve_agent_execution_profile("research", tools=None)
    assert "sandbox_download" in profile.requested_tools()
    assert profile.network_capability is NetworkCapability.FETCH


def test_policy_quota_reservations_fail_closed_and_release() -> None:
    policy = _policy(max_requests=3, max_bytes=10, max_concurrency=1)
    proxy = SandboxEgressProxy(policy=policy)

    assert proxy._reserve_policy_attempt(policy) == (None, 10)
    assert proxy._reserve_policy_attempt(policy) == (
        "concurrency_quota_exceeded",
        None,
    )
    proxy._finish_policy_attempt(policy, bytes_in=4, bytes_out=3)
    assert proxy._reserve_policy_attempt(policy) == (None, 3)
    proxy._finish_policy_attempt(policy, bytes_in=2, bytes_out=1)
    assert proxy._reserve_policy_attempt(policy) == (
        "byte_quota_exceeded",
        None,
    )


def test_proxy_enforces_request_budget_and_forwards_http_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        upstream_requests: list[bytes] = []

        async def upstream(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            request = await reader.readuntil(b"\r\n\r\n")
            upstream_requests.append(request)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = int(upstream_server.sockets[0].getsockname()[1])
        original_open_connection = asyncio.open_connection

        async def fake_open_connection(host: str, port: int):
            assert host == "93.184.216.34"
            assert port in {80, 443}
            return await original_open_connection("127.0.0.1", upstream_port)

        monkeypatch.setattr(
            proxy_module.asyncio, "open_connection", fake_open_connection
        )
        audit_events: list[dict] = []
        policy = _policy(max_requests=2, max_bytes=1024)
        proxy = SandboxEgressProxy(
            policy=policy,
            resolver=lambda _host: ["93.184.216.34"],
            on_decision=audit_events.append,
            max_idle_seconds=2,
        )
        await proxy.start()
        try:
            async def request(uri: str) -> bytes:
                reader, writer = await original_open_connection(
                    "127.0.0.1", proxy.port
                )
                writer.write(
                    f"GET {uri} HTTP/1.1\r\nHost: example.com\r\n\r\n".encode(
                        "ascii"
                    )
                )
                await writer.drain()
                response = await reader.read()
                writer.close()
                await writer.wait_closed()
                return response

            first = await request("http://example.com/a?secret=do-not-log")
            second = await request("http://example.com/b")
            third = await request("http://example.com/c")
            assert b"200 OK" in first
            assert b"200 OK" in second
            assert b"429 Too Many Requests" in third
            assert len(upstream_requests) == 2
            assert upstream_requests[0].startswith(b"GET /a?secret=")
            serialized = str(audit_events)
            assert "?secret=" not in serialized
            assert any(
                event.get("reason") == "request_quota_exceeded"
                for event in audit_events
            )
        finally:
            await proxy.close()
            upstream_server.close()
            await upstream_server.wait_closed()

    asyncio.run(scenario())


def test_proxy_rejects_non_get_head_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        policy = _policy()
        proxy = SandboxEgressProxy(
            policy=policy,
            resolver=lambda _host: ["93.184.216.34"],
        )
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            writer.write(
                b"POST http://example.com/upload HTTP/1.1\r\n"
                b"Host: example.com\r\nContent-Length: 1\r\n\r\nx"
            )
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            assert b"405 Method Not Allowed" in response
        finally:
            await proxy.close()

    asyncio.run(scenario())


def test_proxy_connect_tunnel_is_digest_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def upstream(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            payload = await reader.readexactly(4)
            writer.write(payload[::-1])
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = int(upstream_server.sockets[0].getsockname()[1])
        original_open_connection = asyncio.open_connection

        async def fake_open_connection(host: str, port: int):
            assert host == "93.184.216.34"
            assert port == 443
            return await original_open_connection("127.0.0.1", upstream_port)

        monkeypatch.setattr(
            proxy_module.asyncio, "open_connection", fake_open_connection
        )
        policy = _policy(max_bytes=1024)
        proxy = SandboxEgressProxy(
            policy_registry={policy.digest: policy},
            resolver=lambda _host: ["93.184.216.34"],
            max_idle_seconds=2,
        )
        await proxy.start()
        try:
            reader, writer = await original_open_connection("127.0.0.1", proxy.port)
            writer.write(
                (
                    "CONNECT example.com:443 HTTP/1.1\r\n"
                    f"X-LearnGraph-Policy-Digest: {policy.digest}\r\n\r\n"
                ).encode("ascii")
            )
            await writer.drain()
            response = await reader.readuntil(b"\r\n\r\n")
            assert response.startswith(b"HTTP/1.1 200 Connection Established")
            writer.write(b"ping")
            await writer.drain()
            writer.write_eof()
            assert await reader.read() == b"gnip"
            writer.close()
            await writer.wait_closed()
        finally:
            await proxy.close()
            upstream_server.close()
            await upstream_server.wait_closed()

    asyncio.run(scenario())


def test_download_filename_sanitization_blocks_traversal_and_device_names() -> None:
    assert ExternalAcquisitionService._safe_download_filename(
        "https://example.com/../../evil.pdf",
        'attachment; filename="../../evil.pdf"',
        "application/pdf",
    ) == "evil.pdf"
    assert ExternalAcquisitionService._safe_download_filename(
        "https://example.com/CON.pdf",
        None,
        "application/pdf",
    ) == "file_CON.pdf"


def test_web_fetch_runner_rejects_non_http_schemes_and_metadata_literals() -> None:
    from sandbox.runner import WebFetchError, _validated_fetch_url

    allowed = frozenset({"example.com"})
    for url in (
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/",
        "javascript:alert(1)",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pass@example.com/private",
    ):
        with pytest.raises(WebFetchError):
            _validated_fetch_url(url, allowed)
    assert _validated_fetch_url("http://example.com/a", allowed) == "http://example.com/a"
    assert _validated_fetch_url("https://example.com/a", allowed) == "https://example.com/a"
