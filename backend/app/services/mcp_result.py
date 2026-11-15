"""Credential-safe projections of third-party MCP results."""
from typing import Any
from app.services.mcp_skills import _redact_sensitive_keys


def redact_result(manager, server, result: Any):
    result = _redact_sensitive_keys(result)
    secret = manager._credential_secret(server) if server.auth_reference else None
    if not secret:
        return result
    def walk(value):
        if isinstance(value,str): return value.replace(secret,"[REDACTED]")
        if isinstance(value,list): return [walk(item) for item in value]
        if isinstance(value,dict): return {walk(str(key)):walk(item) for key,item in value.items()}
        return value
    return walk(result)
