"""Stdio MCP Host Companion tests (stage 4).

锁定：launch 文件校验、目录注册表、StdioProcess 的 JSON-RPC id 匹配往返、
HTTP /rpc 端点鉴权与调用、健康检查。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.mcp_companion import (
    CompanionLaunchInvalid,
    DirectoryStdioRegistry,
    StdioCompanion,
    StdioLaunch,
    StdioProcess,
    validate_stdio_launch,
)

TOKEN = "companion-test-token-0123456789"

# A minimal stdio MCP server: reads newline-delimited JSON-RPC from stdin,
# echoes params back for any request carrying an id (UTF-8 via raw buffers so
# Windows pipe encodings cannot corrupt the protocol).
FAKE_MCP_SERVER = r"""
import sys, json
for line in sys.stdin.buffer:
    try:
        msg = json.loads(line.decode("utf-8"))
    except Exception:
        continue
    if isinstance(msg, dict) and "id" in msg:
        out = json.dumps({"id": msg["id"], "result": {"echo": msg.get("params")}}).encode("utf-8") + b"\n"
        sys.stdout.buffer.write(out)
        sys.stdout.buffer.flush()
"""


def _fake_launch(launch_id: str = "fake") -> StdioLaunch:
    return StdioLaunch(
        id=launch_id,
        command=(sys.executable, "-u", "-c", FAKE_MCP_SERVER),
        env={},
        cwd=None,
        timeout_seconds=15.0,
    )


class TestValidateLaunch:
    def test_valid(self) -> None:
        launch = validate_stdio_launch(
            {"id": "fs", "command": ["npx", "-y", "x"], "env": {"A": "1"}, "cwd": "/tmp", "timeout_seconds": 30}
        )
        assert launch.id == "fs"
        assert launch.command == ("npx", "-y", "x")
        assert launch.env == {"A": "1"}
        assert launch.cwd == "/tmp"

    def test_invalid_ids(self) -> None:
        for bad in ["", "a/b", "a b", ".."]:
            with pytest.raises(CompanionLaunchInvalid):
                validate_stdio_launch({"id": bad, "command": ["x"]})

    def test_command_required(self) -> None:
        with pytest.raises(CompanionLaunchInvalid):
            validate_stdio_launch({"id": "x", "command": []})


class TestDirectoryStdioRegistry:
    def test_loads_valid_skips_invalid(self, tmp_path: Path) -> None:
        (tmp_path / "fake.json").write_text(
            json.dumps({"id": "fake", "command": ["python"]}), encoding="utf-8"
        )
        (tmp_path / "bad.json").write_text("{oops", encoding="utf-8")
        (tmp_path / "mismatch.json").write_text(
            json.dumps({"id": "other", "command": ["x"]}), encoding="utf-8"
        )
        registry: dict = {}
        DirectoryStdioRegistry(tmp_path).refresh_into(registry)
        assert set(registry) == {"fake"}


class TestStdioProcess:
    def test_request_roundtrip(self) -> None:
        process = StdioProcess(_fake_launch())

        async def run() -> dict:
            try:
                return await process.request(
                    {"id": 42, "method": "tools/call", "params": {"tool": "echo", "x": 1}}
                )
            finally:
                await process.shutdown()

        resp = asyncio.run(run())
        assert resp["id"] == 42
        assert resp["result"]["echo"] == {"tool": "echo", "x": 1}


class TestCompanionHTTP:
    def _client(self) -> TestClient:
        companion = StdioCompanion(token=TOKEN, registry={"fake": _fake_launch()})
        return TestClient(companion.app())

    def test_healthz_requires_token(self) -> None:
        client = self._client()
        assert client.get("/healthz").status_code == 403

    def test_healthz_ok(self) -> None:
        client = self._client()
        resp = client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200
        assert resp.json()["processes"]["fake"] == "stopped"

    def test_rpc_roundtrip(self) -> None:
        client = self._client()
        resp = client.post(
            "/rpc",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "launch_id": "fake",
                "message": {"id": 7, "method": "tools/call", "params": {"tool": "echo"}},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == 7
        assert resp.json()["result"]["echo"] == {"tool": "echo"}

    def test_unknown_launch_404(self) -> None:
        client = self._client()
        resp = client.post(
            "/rpc",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"launch_id": "ghost", "message": {"id": 1, "method": "x"}},
        )
        assert resp.status_code == 404

    def test_requires_launch_id(self) -> None:
        client = self._client()
        resp = client.post(
            "/rpc",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"message": {"id": 1, "method": "x"}},
        )
        assert resp.status_code == 400
