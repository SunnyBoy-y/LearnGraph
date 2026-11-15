"""Database-backed MCP slots shared by API, model and scheduled callers."""
from app.core.process_lock import acquire_advisory_lock, release_advisory_lock
from app.providers.ports.mcp import MCPRunnerResourceExceeded

class MCPCapacityUnavailable(MCPRunnerResourceExceeded):
    code = "mcp_concurrency_limit"


def execution_timeout_ms(manager, server):
    import time
    from app.providers.ports.mcp import MCPTransportTimeout
    timeout = min(server.timeout_ms, getattr(manager, "execution_timeout_ms", server.timeout_ms))
    deadline = getattr(manager, "execution_deadline", None)
    if deadline is not None:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise MCPTransportTimeout("MCP execution exceeded its total deadline")
        timeout = min(timeout, remaining)
    return max(1, timeout)


def guarded_mcp_call(manager, server, callback):
    ttl = max(60, int(server.timeout_ms / 1000) * 4,
              int(getattr(manager.settings, "mcp_stdio_timeout_seconds", 60)) + 30)
    for index in range(server.max_concurrency):
        name = f"mcp-call:{manager.workspace_id}:{server.id}:{index}"
        token = acquire_advisory_lock(manager.db, name, ttl_seconds=ttl)
        if token:
            try:
                return callback()
            finally:
                release_advisory_lock(manager.db, name, token)
    raise MCPCapacityUnavailable("MCP server reached its cross-process execution concurrency limit")
