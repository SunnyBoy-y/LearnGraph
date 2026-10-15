"""Abuse regression tests for task-scoped sandbox network capabilities.

These tests stay hermetic: no Docker/network egress is required. They cover the
application policy projection, the CONNECT-time SSRF gate, and the proxy's
fail-closed behavior when a sandbox has no matching policy.
"""

from __future__ import annotations

import asyncio


import pytest

from app.services.agent_execution_profiles import (
    NetworkCapability,
    clamp_network_capability,
    network_capability_allows_tool,
    resolve_agent_execution_profile,
)
from app.services.sandbox_egress_proxy import POLICY_DIGEST_HEADER, SandboxEgressProxy
from app.services.sandbox_network_policy import (
    EgressPolicyDenied,
    authorize_connect,
    classify_ip_address,
    derive_egress_policy_for_agent,
)


def test_private_and_metadata_address_classes_are_denied() -> None:
    cases = {
        "127.0.0.1": "loopback",
        "::1": "loopback",
        "10.0.0.1": "private",
        "172.16.0.1": "private",
        "192.168.1.1": "private",
        "169.254.169.254": "metadata",
        "fe80::1": "link_local",
        "0.0.0.0": "unspecified",
        "224.0.0.1": "multicast",
        "100.64.0.1": "carrier_grade_nat",
    }
    for address, expected in cases.items():
        assert classify_ip_address(address) == expected


def test_authorize_connect_rejects_dns_rebinding_to_private() -> None:
    policy = derive_egress_policy_for_agent(
        workspace_id="ws-abuse",
        allowed_hosts=["public.example.com"],
        ttl_seconds=600,
    )
    with pytest.raises(EgressPolicyDenied) as exc:
        authorize_connect(
            policy,
            "public.example.com",
            443,
            resolver=lambda _host: ["93.184.216.34", "127.0.0.1"],
        )
    assert exc.value.reason == "dns_address_classified_forbidden"


def test_connect_time_rejects_private_port_and_unknown_host() -> None:
    policy = derive_egress_policy_for_agent(
        workspace_id="ws-abuse",
        allowed_hosts=["public.example.com"],
        ttl_seconds=600,
    )
    with pytest.raises(EgressPolicyDenied, match="port_protocol_not_allowed"):
        authorize_connect(
            policy,
            "public.example.com",
            22,
            resolver=lambda _host: ["93.184.216.34"],
        )
    with pytest.raises(EgressPolicyDenied, match="host_not_in_allowlist"):
        authorize_connect(
            policy,
            "host.docker.internal",
            443,
            resolver=lambda _host: ["93.184.216.34"],
        )


def test_task_network_capability_cannot_be_elevated_by_client_intent() -> None:
    reason = resolve_agent_execution_profile("reason", tools=None)
    research = resolve_agent_execution_profile("research", tools=None)

    assert reason.network_capability is NetworkCapability.OFFLINE
    assert research.network_capability is NetworkCapability.FETCH
    assert (
        clamp_network_capability(
            research.network_capability,
            NetworkCapability.RESTRICTED_EGRESS,
        )
        is NetworkCapability.FETCH
    )
    assert not network_capability_allows_tool(
        NetworkCapability.OFFLINE, "sandbox_download"
    )
    assert network_capability_allows_tool(
        NetworkCapability.FETCH, "sandbox_download"
    )


def test_proxy_denies_metadata_connect_and_non_connect_methods() -> None:
    async def exercise() -> tuple[bytes, bytes]:
        policy = derive_egress_policy_for_agent(
            workspace_id="ws-proxy",
            allowed_hosts=[],
            allow_all_public=True,
            ttl_seconds=600,
        )
        proxy = SandboxEgressProxy(
            policy_registry={policy.digest: policy},
            resolver=lambda _host: ["93.184.216.34"],
        )
        port = await proxy.start("127.0.0.1", 0)
        try:
            metadata_reader, metadata_writer = await asyncio.open_connection(
                "127.0.0.1", port
            )
            metadata_writer.write(
                b"CONNECT 169.254.169.254:443 HTTP/1.1\r\n"
                + POLICY_DIGEST_HEADER
                + b": "
                + policy.digest.encode("ascii")
                + b"\r\n\r\n"
            )
            await metadata_writer.drain()
            metadata = await metadata_reader.read(1024)
            metadata_writer.close()
            await metadata_writer.wait_closed()

            method_reader, method_writer = await asyncio.open_connection(
                "127.0.0.1", port
            )
            method_writer.write(
                b"POST http://public.example.com/ HTTP/1.1\r\nHost: public.example.com\r\n\r\n"
            )
            await method_writer.drain()
            method = await method_reader.read(1024)
            method_writer.close()
            await method_writer.wait_closed()
            return metadata, method
        finally:
            await proxy.close()

    metadata_response, method_response = asyncio.run(exercise())
    assert b"403 Forbidden" in metadata_response
    assert b"405 Method Not Allowed" in method_response


def test_proxy_denies_connect_without_a_registered_policy_digest() -> None:
    async def exercise() -> bytes:
        proxy = SandboxEgressProxy(policy_registry={})
        port = await proxy.start("127.0.0.1", 0)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"CONNECT public.example.com:443 HTTP/1.1\r\n\r\n")
            await writer.drain()
            response = await reader.read(1024)
            writer.close()
            await writer.wait_closed()
            return response
        finally:
            await proxy.close()

    assert b"403 Forbidden" in asyncio.run(exercise())
