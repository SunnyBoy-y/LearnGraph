"""Per-request metadata and injection-safe HTTP routing for modern MCP."""
from __future__ import annotations
import base64
import re
from typing import Any
from app.providers.ports.mcp import MCPProtocolFailure

MODERN_PROTOCOL_VERSION = "2026-07-28"
META_PREFIX = "io.modelcontextprotocol/"


def request_params(params: dict[str, Any], version: str) -> dict[str, Any]:
    return {**params, "_meta": {META_PREFIX+"protocolVersion": version,
        META_PREFIX+"clientInfo": {"name":"LearnGraph", "version":"0.1.0"},
        META_PREFIX+"clientCapabilities": {}}}


def header_value(value: str | int | bool) -> str:
    if type(value) is bool:
        value = "true" if value else "false"
    elif type(value) is int:
        if not -(2**53-1) <= value <= 2**53-1:
            raise MCPProtocolFailure("MCP header integer is outside the safe integer range")
        value = str(value)
    elif not isinstance(value, str):
        raise MCPProtocolFailure("MCP header values must be string, integer, or boolean")
    safe = value == value.strip() and all(c == "\t" or 32 <= ord(c) <= 126 for c in value)
    if safe and not (value.startswith("=?base64?") and value.endswith("?=")):
        return value
    try:
        return "=?base64?"+base64.b64encode(value.encode("utf-8")).decode("ascii")+"?="
    except UnicodeEncodeError as exc:
        raise MCPProtocolFailure("MCP header contains an invalid Unicode value") from exc


def header_paths(schema: dict[str, Any]) -> list[tuple[tuple[str, ...], str, str]]:
    found = []
    names = set()
    def walk(node, path=(), reachable=True):
        if isinstance(node, list):
            for item in node: walk(item, path, False)
        elif isinstance(node, dict):
            if "x-mcp-header" in node:
                name, kind = node["x-mcp-header"], node.get("type")
                if not reachable or not path or not isinstance(kind,str) or kind not in {"string","integer","boolean"} or not isinstance(name,str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+",name) or name.lower() in names:
                    raise MCPProtocolFailure("Invalid or duplicate x-mcp-header annotation")
                names.add(name.lower()); found.append((path,name,kind))
            for key,value in node.items():
                if key == "properties" and isinstance(value,dict):
                    for prop,child in value.items(): walk(child,(*path,prop),reachable)
                elif key not in {"properties","x-mcp-header"} and isinstance(value,(dict,list)):
                    walk(value,path,False)
    walk(schema)
    return found


def routing_headers(method: str, params: dict[str, Any], schema=None) -> dict[str,str]:
    headers = {"Mcp-Method": method}
    if method in {"tools/call","prompts/get","resources/read"}:
        name = params.get("uri" if method == "resources/read" else "name")
        if not isinstance(name,str): raise MCPProtocolFailure("MCP request name must be a string")
        headers["Mcp-Name"] = header_value(name)
    if method == "tools/call" and schema is not None:
        for path,name,kind in header_paths(schema):
            value = params.get("arguments",{})
            for part in path:
                value = value.get(part) if isinstance(value,dict) else None
            if value is None: continue
            if (kind == "string" and not isinstance(value,str)) or (kind == "integer" and type(value) is not int) or (kind == "boolean" and type(value) is not bool):
                raise MCPProtocolFailure("MCP parameter header does not match its declared type")
            headers["Mcp-Param-"+name] = header_value(value)
    return headers
