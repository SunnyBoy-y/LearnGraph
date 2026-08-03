from __future__ import annotations

import asyncio
from datetime import timedelta

from app.services import sandbox_egress_proxy as proxy_module
from app.services import sandbox_network_policy as policy_module
from app.services.sandbox_egress_proxy import SandboxEgressProxy
from app.services.sandbox_network_policy import utc_now, validate_egress_policy


def make_policy():
    return validate_egress_policy(
        {
            "workspace_id": "workspace-a",
            "approval_id": "approval-1",
            "issuer": "platform-admin",
            "issued_at": (utc_now() - timedelta(days=1)).isoformat(),
            "expires_at": (utc_now() + timedelta(days=7)).isoformat(),
            "hosts": [
                {"host": "api.example.test", "ports": [443], "protocols": ["https"]}
            ],
        }
    )


def policy_allowing_port(port: int):
    return validate_egress_policy(
        {
            "workspace_id": "workspace-a",
            "approval_id": "approval-1",
            "issuer": "platform-admin",
            "issued_at": (utc_now() - timedelta(days=1)).isoformat(),
            "expires_at": (utc_now() + timedelta(days=7)).isoformat(),
            "hosts": [
                {
                    "host": "api.example.test",
                    "ports": [443, port],
                    "protocols": ["https"],
                }
            ],
        }
    )


async def _echo_server():
    async def handle(reader, writer):
        data = await reader.readline()
        writer.write(data)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def _send(proxy_port: int, raw_request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(raw_request)
    await writer.drain()
    status_line = await reader.readline()
    writer.close()
    return status_line


def test_proxy_forwards_allowed_reviewed_target(monkeypatch) -> None:
    real_classify = policy_module.classify_ip_address
    monkeypatch.setattr(
        policy_module,
        "classify_ip_address",
        lambda value: "public" if value == "127.0.0.1" else real_classify(value),
    )
    decisions: list[dict] = []

    async def scenario():
        echo_server, echo_port = await _echo_server()
        proxy = SandboxEgressProxy(
            policy_allowing_port(echo_port),
            resolver=lambda host: ["127.0.0.1"],
            on_decision=decisions.append,
        )
        await proxy.start("127.0.0.1", 0)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            writer.write(
                f"CONNECT api.example.test:{echo_port} HTTP/1.1\r\n"
                f"Host: api.example.test:{echo_port}\r\n\r\n".encode()
            )
            await writer.drain()
            status_line = await reader.readuntil(b"\r\n\r\n")
            payload = b"hello-egress\n"
            writer.write(payload)
            await writer.drain()
            echoed = await reader.readline()
            writer.close()
            return status_line, echoed
        finally:
            await proxy.close()
            echo_server.close()
            await echo_server.wait_closed()

    status_line, echoed = asyncio.run(scenario())
    assert b"200 Connection Established" in status_line
    assert echoed == b"hello-egress\n"
    assert decisions[0]["decision"] == "allowed"
    assert decisions[0]["resolved_ip"] == "127.0.0.1"
    assert decisions[0]["host"] == "api.example.test"


def test_proxy_denies_private_address_after_resolution() -> None:
    decisions: list[dict] = []

    async def scenario():
        proxy = SandboxEgressProxy(
            make_policy(),
            resolver=lambda host: ["10.0.0.5"],
            on_decision=decisions.append,
        )
        await proxy.start("127.0.0.1", 0)
        try:
            return await _send(
                proxy.port,
                b"CONNECT api.example.test:443 HTTP/1.1\r\nHost: api.example.test:443\r\n\r\n",
            )
        finally:
            await proxy.close()

    status_line = asyncio.run(scenario())
    assert b"403" in status_line
    assert decisions[0]["decision"] == "denied"
    assert decisions[0]["reason"] == "dns_address_classified_forbidden"


def test_proxy_denies_host_outside_allowlist() -> None:
    decisions: list[dict] = []

    async def scenario():
        proxy = SandboxEgressProxy(
            make_policy(),
            resolver=lambda host: ["8.8.8.8"],
            on_decision=decisions.append,
        )
        await proxy.start("127.0.0.1", 0)
        try:
            return await _send(
                proxy.port,
                b"CONNECT other.example.test:443 HTTP/1.1\r\nHost: other.example.test:443\r\n\r\n",
            )
        finally:
            await proxy.close()

    status_line = asyncio.run(scenario())
    assert b"403" in status_line
    assert decisions[0]["decision"] == "denied"
    assert decisions[0]["reason"] == "host_not_in_allowlist"


def test_proxy_rejects_non_connect_method() -> None:
    decisions: list[dict] = []

    async def scenario():
        proxy = SandboxEgressProxy(
            make_policy(),
            resolver=lambda host: ["8.8.8.8"],
            on_decision=decisions.append,
        )
        await proxy.start("127.0.0.1", 0)
        try:
            return await _send(
                proxy.port,
                b"GET http://api.example.test/ HTTP/1.1\r\nHost: api.example.test\r\n\r\n",
            )
        finally:
            await proxy.close()

    status_line = asyncio.run(scenario())
    assert b"405" in status_line
    assert decisions[0]["decision"] == "denied"
    assert decisions[0]["reason"] == "non_connect_method"


def test_proxy_denies_ip_literal_target() -> None:
    decisions: list[dict] = []

    async def scenario():
        proxy = SandboxEgressProxy(
            make_policy(),
            resolver=lambda host: ["8.8.8.8"],
            on_decision=decisions.append,
        )
        await proxy.start("127.0.0.1", 0)
        try:
            return await _send(
                proxy.port,
                b"CONNECT 93.184.216.34:443 HTTP/1.1\r\nHost: 93.184.216.34:443\r\n\r\n",
            )
        finally:
            await proxy.close()

    status_line = asyncio.run(scenario())
    assert b"403" in status_line
    assert decisions[0]["reason"] == "host_not_normalizable"


def test_proxy_audit_marks_allow_and_deny(monkeypatch) -> None:
    real_classify = policy_module.classify_ip_address
    monkeypatch.setattr(
        policy_module,
        "classify_ip_address",
        lambda value: "public" if value == "127.0.0.1" else real_classify(value),
    )
    decisions: list[dict] = []

    async def scenario():
        echo_server, echo_port = await _echo_server()
        proxy = SandboxEgressProxy(
            policy_allowing_port(echo_port),
            resolver=lambda host: ["127.0.0.1"],
            on_decision=decisions.append,
        )
        await proxy.start("127.0.0.1", 0)
        try:
            await _send(
                proxy.port,
                f"CONNECT api.example.test:{echo_port} HTTP/1.1\r\nHost: api.example.test:{echo_port}\r\n\r\n".encode(),
            )
            await _send(
                proxy.port,
                b"CONNECT denied.example.test:443 HTTP/1.1\r\nHost: denied.example.test:443\r\n\r\n",
            )
        finally:
            await proxy.close()
            echo_server.close()
            await echo_server.wait_closed()

    asyncio.run(scenario())
    assert [event["decision"] for event in decisions] == ["allowed", "denied"]
    assert all("policy_digest" in event for event in decisions)
    assert decisions[0]["approval_id"] == "approval-1"
    assert decisions[1]["reason"] == "host_not_in_allowlist"
