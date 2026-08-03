"""Sandbox egress (P2-C) deployment smoke check.

Verifies that reviewed outbound egress stays fail-closed without relying on a
full test harness:

- loads and validates a per-workspace reviewed policy file (missing/expired/
  malformed -> offline),
- re-classifies resolved addresses at connection time (loopback / private /
  metadata refused even for an allowlisted host),
- exercises the executable CONNECT proxy allow/deny paths,
- (when Docker is available) confirms the sandbox image carries the fixed
  ``render_component`` / ``mcp_stdio`` runner tasks.

Usage (from backend/):
    python scripts/verify_sandbox_egress.py [path-to-policy-dir]
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from datetime import timedelta

from app.services.sandbox_egress_proxy import SandboxEgressProxy
from app.services.sandbox_network_policy import (
    EgressPolicyDenied,
    authorize_connect,
    load_workspace_policy_file,
    utc_now,
    validate_egress_policy,
)

TMP = pathlib.Path(".egress-smoke")
PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [ok]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def valid_policy_data(workspace_id: str, **overrides) -> dict:
    data = {
        "workspace_id": workspace_id,
        "approval_id": "smoke-approval",
        "issuer": "smoke",
        "issued_at": (utc_now() - timedelta(days=1)).isoformat(),
        "expires_at": (utc_now() + timedelta(days=1)).isoformat(),
        "hosts": [{"host": "api.example.test", "ports": [443], "protocols": ["https"]}],
    }
    data.update(overrides)
    return data


def check_policy_layer() -> None:
    print("[policy layer]")
    # Default: missing policy -> offline.
    check("missing policy stays offline", load_workspace_policy_file(TMP, "ws-a") is None)
    # Expired policy -> offline.
    (TMP / "ws-a.json").write_text(
        json.dumps(
            valid_policy_data(
                "ws-a", expires_at=(utc_now() - timedelta(seconds=1)).isoformat()
            )
        ),
        encoding="utf-8",
    )
    check("expired policy stays offline", load_workspace_policy_file(TMP, "ws-a") is None)
    # Valid policy loads.
    (TMP / "ws-a.json").write_text(json.dumps(valid_policy_data("ws-a")), encoding="utf-8")
    policy = load_workspace_policy_file(TMP, "ws-a")
    check("valid reviewed policy loads", policy is not None)
    if policy is None:
        return
    # DNS rebinding: allowlisted host that resolves to loopback must be refused.
    try:
        authorize_connect(policy, "api.example.test", 443, resolver=lambda h: ["127.0.0.1"])
        check("loopback answer refused", False)
    except EgressPolicyDenied as exc:
        check("loopback answer refused", exc.reason == "dns_address_classified_forbidden")
    # Metadata address.
    try:
        authorize_connect(policy, "api.example.test", 443, resolver=lambda h: ["169.254.169.254"])
        check("metadata answer refused", False)
    except EgressPolicyDenied as exc:
        check("metadata answer refused", exc.reason == "dns_address_classified_forbidden")
    # Unknown host.
    try:
        authorize_connect(policy, "other.example.test", 443, resolver=lambda h: ["8.8.8.8"])
        check("non-allowlisted host refused", False)
    except EgressPolicyDenied as exc:
        check("non-allowlisted host refused", exc.reason == "host_not_in_allowlist")
    # Forbidden port.
    try:
        authorize_connect(policy, "api.example.test", 22, resolver=lambda h: ["8.8.8.8"])
        check("non-allowlisted port refused", False)
    except EgressPolicyDenied as exc:
        check("non-allowlisted port refused", exc.reason == "port_not_allowed")


def check_proxy_layer() -> None:
    print("[proxy layer]")

    async def run() -> list[dict]:
        decisions: list[dict] = []
        policy = validate_egress_policy(valid_policy_data("ws-a"))
        proxy = SandboxEgressProxy(
            policy,
            resolver=lambda host: ["10.0.0.5"],
            on_decision=decisions.append,
        )
        await proxy.start("127.0.0.1", 0)

        async def send(raw: bytes) -> bytes:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            writer.write(raw)
            await writer.drain()
            line = await reader.readline()
            writer.close()
            return line

        try:
            denied = await send(
                b"CONNECT api.example.test:443 HTTP/1.1\r\nHost: api.example.test:443\r\n\r\n"
            )
            return [denied, *decisions]
        finally:
            await proxy.close()

    result = asyncio.run(run())
    status_line, decisions = result[0], result[1:]
    check("proxy refuses DNS-rebound private address", b"403" in status_line)
    check(
        "proxy audits the denial",
        bool(decisions) and decisions[0]["decision"] == "denied",
        f"decisions={decisions}",
    )


def check_sandbox_image() -> None:
    print("[sandbox image]")
    try:
        import subprocess

        completed = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "python", "learngraph-sandbox:local", "-c",
             "import pathlib; s=pathlib.Path('/opt/learngraph/runner.py').read_text(); print('render_component' in s, 'mcp_stdio' in s)"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:
        check("docker available", False, f"({exc})")
        return
    if completed.returncode != 0 or not completed.stdout.strip():
        check("sandbox image present", False, f"(docker exited {completed.returncode})")
        return
    parts = completed.stdout.split()
    check("image has render_component task", len(parts) >= 1 and parts[0] == "True")
    check("image has mcp_stdio task", len(parts) >= 2 and parts[1] == "True")


def main() -> int:
    TMP.mkdir(exist_ok=True)
    print("P2-C sandbox egress smoke check")
    check_policy_layer()
    check_proxy_layer()
    check_sandbox_image()
    print(f"\n{FAIL} failed, {PASS} passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
