"""Bounded MCP stdio sessions. This module executes ONLY inside the sandbox."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from uuid import uuid4

VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26"}
MODERN_VERSION = "2026-07-28"
META_PREFIX = "io.modelcontextprotocol/"

class RpcError(ValueError):
    def __init__(self, code, data=None):
        super().__init__(f"MCP JSON-RPC error {code}")
        self.code, self.data = code, data


def execute_session(command, request, *, timeout=60, max_bytes=1048576, credential=None):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or "id" not in request:
        raise ValueError("Invalid MCP JSON-RPC request")
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "HOME", "LANG", "LC_ALL", "NODE_PATH", "PYTHONPATH", "SYSTEMROOT", "TEMP", "TMP"}}
    if credential:
        if credential.get("audience") != "mcp-stdio-runner" or not isinstance(credential.get("access_token"), str):
            raise ValueError("Invalid runner credential envelope")
        env["MCP_ACCESS_TOKEN"] = credential["access_token"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=env, bufsize=0)
    messages = queue.Queue(maxsize=128)
    deadline = time.monotonic() + min(max(float(timeout), 0.1), 600)
    exceeded = threading.Event()
    byte_count = [0]
    lock = threading.Lock()

    def consume(stream, is_stdout):
        try:
            while True:
                line = stream.readline(max_bytes + 1)
                if not line:
                    break
                with lock:
                    byte_count[0] += len(line)
                    if byte_count[0] > max_bytes:
                        exceeded.set()
                        process.kill()
                        break
                if is_stdout:
                    try:
                        messages.put_nowait(line)
                    except queue.Full:
                        exceeded.set()
                        process.kill()
                        break
        finally:
            if is_stdout:
                try:
                    messages.put_nowait(None)
                except queue.Full:
                    pass

    threads = [threading.Thread(target=consume, args=(process.stdout, True), daemon=True),
               threading.Thread(target=consume, args=(process.stderr, False), daemon=True)]
    for thread in threads:
        thread.start()

    def send(message):
        process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        process.stdin.flush()

    active_version = [None]
    ignored = set()

    def rpc(method, params, *, probe=False):
        identifier = uuid4().hex
        if active_version[0] == MODERN_VERSION:
            params = {**params, "_meta": {META_PREFIX+"protocolVersion": MODERN_VERSION,
                      META_PREFIX+"clientInfo": {"name":"LearnGraph", "version":"0.1.0"},
                      META_PREFIX+"clientCapabilities": {}}}
        send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        call_deadline = min(deadline, time.monotonic()+1.0) if probe else deadline
        while True:
            if exceeded.is_set():
                raise ValueError("MCP output exceeded its size limit")
            remaining = call_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("MCP session exceeded its total deadline")
            try:
                line = messages.get(timeout=remaining)
            except queue.Empty as exc:
                ignored.add(identifier)
                raise TimeoutError("MCP session exceeded its request deadline") from exc
            if line is None:
                raise ValueError("MCP server closed before responding")
            message = json.loads(line.decode("utf-8", errors="strict"))
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ValueError("Invalid MCP stdio JSON-RPC message")
            if "method" in message:
                if "id" in message:
                    if message["method"] == "ping":
                        send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
                    else:
                        send({"jsonrpc": "2.0", "id": message["id"], "error": {
                            "code": -32601, "message": "Client capability not supported"}})
                continue  # notifications are not responses
            if message.get("id") in ignored:
                continue
            if message.get("id") != identifier:
                raise ValueError("MCP response id did not match its request")
            if ("result" in message) == ("error" in message):
                raise ValueError("MCP response must contain exactly one result or error")
            if "error" in message:
                # Third-party error messages may contain injected credentials.
                error = message["error"]
                raise RpcError(error.get("code") if isinstance(error,dict) else None,
                               error.get("data") if isinstance(error,dict) else None)
            if not isinstance(message["result"], dict):
                raise ValueError("MCP result must be an object")
            return message["result"]

    def list_all(method, key):
        values, seen, cursor = [], set(), None
        for _ in range(10):
            page = rpc(method, {"cursor": cursor} if cursor is not None else {})
            items = page.get(key)
            if not isinstance(items, list) or not all(isinstance(value, dict) for value in items):
                raise ValueError("Invalid MCP capability list")
            values.extend(items)
            if len(values) > 500:
                raise ValueError("MCP list exceeds 500 items")
            cursor = page.get("nextCursor")
            if cursor is None:
                return values
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError("Invalid or repeated MCP pagination cursor")
            seen.add(cursor)
        raise ValueError("MCP list exceeds 10 pages")

    try:
        active_version[0] = MODERN_VERSION
        modern = None
        try:
            modern = rpc("server/discover", {}, probe=True)
        except RpcError as exc:
            if exc.code in {-32020,-32021,-32022}:
                raise
        except TimeoutError:
            if time.monotonic() >= deadline:
                raise
        if modern is not None:
            if MODERN_VERSION not in modern.get("supportedVersions", []):
                raise ValueError("No mutually supported modern protocol version")
            initialize = {"protocolVersion":MODERN_VERSION, "capabilities":modern.get("capabilities"),
                          "serverInfo":(modern.get("_meta") or {}).get(META_PREFIX+"serverInfo", {})}
        else:
            active_version[0] = None
            initialize = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                            "clientInfo": {"name": "LearnGraph", "version": "0.1.0"}})
            if initialize.get("protocolVersion") not in VERSIONS:
                raise ValueError("Unsupported MCP protocol version")
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        capabilities = initialize.get("capabilities")
        if not isinstance(capabilities, dict) or not isinstance(initialize.get("serverInfo"), dict):
            raise ValueError("Invalid MCP discovery result")
        if request.get("method") == "learngraph/discover":
            result = {"protocol_version": initialize["protocolVersion"],
                      "server_identity": initialize["serverInfo"], "capabilities": capabilities,
                      "tools": list_all("tools/list", "tools") if "tools" in capabilities else [],
                      "resources": list_all("resources/list", "resources") if "resources" in capabilities else [],
                      "prompts": list_all("prompts/list", "prompts") if "prompts" in capabilities else []}
            if "resources" in capabilities:
                try:
                    templates = list_all("resources/templates/list", "resourceTemplates")
                except RpcError as exc:
                    if exc.code != -32601:
                        raise
                else:
                    result["resources"].extend({**item,"uri":item["uriTemplate"]} for item in templates if isinstance(item.get("uriTemplate"),str))
        else:
            if request.get("method") not in {"tools/call", "resources/read", "prompts/get"}:
                raise ValueError("Unsupported MCP invocation method")
            result = rpc(request["method"], request.get("params", {}))
            if result.get("resultType") not in {None,"complete"}:
                raise ValueError("MCP requested an unadvertised client interaction; execution was not replayed")
        return {"jsonrpc": "2.0", "id": request["id"], "result": result}
    finally:
        if process.stdin:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=1)
        for stream in (process.stdout, process.stderr):
            stream.close()
