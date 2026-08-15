"""HTTP client for the sandboxd control plane (Phase 2).

The client speaks the versioned, authenticated Sandbox API v1 defined in
``sandboxd/sandboxd/protocol.py``. Transport failures map to
``SandboxdUnavailable``; stable protocol envelopes map to
``SandboxdProtocolError`` with the daemon's stable error code. Secrets (the
service token) never appear in exceptions, reprs, or logs.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import httpx

from app.core.config import Settings


class SandboxdUnavailable(RuntimeError):
    """sandboxd is unreachable or returned a transport-level failure."""


class SandboxdProtocolError(RuntimeError):
    """sandboxd returned a stable protocol error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.request_id = request_id
        self.details = details or {}

    def __repr__(self) -> str:
        return f"SandboxdProtocolError(code={self.code!r}, retryable={self.retryable})"


_client_cache: dict[str, httpx.Client] = {}
_client_cache_lock = threading.Lock()


def _shared_client(url: str, connect_timeout: float, request_timeout: float) -> httpx.Client:
    """Process-wide shared connection pool per daemon URL."""
    key = f"{url}|{connect_timeout}|{request_timeout}"
    with _client_cache_lock:
        client = _client_cache.get(key)
        if client is None:
            client = httpx.Client(
                base_url=url,
                timeout=httpx.Timeout(
                    connect=connect_timeout,
                    read=request_timeout,
                    write=request_timeout,
                    pool=connect_timeout,
                ),
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            )
            _client_cache[key] = client
        return client


class SandboxdClient:
    """Authenticated Sandbox API v1 client (synchronous, thread-safe)."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        deployment_id: str,
        connect_timeout: float = 3.0,
        request_timeout: float = 190.0,
        protocol_min: str = "1.0",
        protocol_max: str = "1.0",
        client: httpx.Client | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self._token = token
        self.deployment_id = deployment_id
        self._connect_timeout = connect_timeout
        self._request_timeout = request_timeout
        self.protocol_min = protocol_min
        self.protocol_max = protocol_max
        self._client = client  # injected for tests

    # --- transport ---------------------------------------------------------

    def _session(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return _shared_client(self.url, self._connect_timeout, self._request_timeout)

    @staticmethod
    def _scope(deployment_id: str, session_id: str) -> str:
        return f"{deployment_id}|{session_id}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        session_id: str | None = None,
        scope_deployment: str | None = None,
        json_body: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        request_headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Request-Id": uuid.uuid4().hex[:16],
        }
        if session_id is not None:
            request_headers["X-Sandbox-Scope"] = self._scope(
                scope_deployment or self.deployment_id, session_id
            )
        if headers:
            request_headers.update(headers)
        try:
            response = self._session().request(
                method,
                path,
                headers=request_headers,
                json=json_body,
                content=content,
                params=params,
            )
        except httpx.TimeoutException as exc:
            raise SandboxdUnavailable(f"sandboxd request timed out: {type(exc).__name__}") from exc
        except httpx.TransportError as exc:
            raise SandboxdUnavailable(f"sandboxd is unreachable: {type(exc).__name__}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            self._raise_error(response)
        return response

    @staticmethod
    def _raise_error(response: httpx.Response) -> None:
        request_id = response.headers.get("X-Request-Id")
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                raise SandboxdProtocolError(
                    code=error["code"],
                    message=str(error.get("message") or "sandboxd error"),
                    retryable=bool(error.get("retryable")),
                    request_id=error.get("request_id") or request_id,
                    details=error.get("details") or {},
                )
        except ValueError:
            pass
        raise SandboxdProtocolError(
            code="unknown_error",
            message=f"sandboxd returned HTTP {response.status_code}",
            request_id=request_id,
        )

    # --- health / capability ----------------------------------------------

    def get_capabilities(self) -> dict[str, Any]:
        response = self._request("GET", "/v1/capabilities")
        return response.json()

    def get_ready(self) -> dict[str, Any]:
        response = self._request("GET", "/v1/health/ready")
        return response.json()

    def capacity(self) -> tuple[int, int]:
        response = self._request("GET", "/v1/capacity")
        body = response.json()
        return int(body.get("cpu_count") or 0), int(body.get("memory_bytes") or 0)

    # --- sandbox lifecycle -------------------------------------------------

    def create_sandbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._request("POST", "/v1/sandboxes", json_body=payload)
        return response.json()

    def get_sandbox(self, sandbox_id: str, session_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/v1/sandboxes/{sandbox_id}", session_id=session_id)
        return response.json()

    def resume_sandbox(self, sandbox_id: str, session_id: str) -> dict[str, Any]:
        response = self._request(
            "POST", f"/v1/sandboxes/{sandbox_id}/resume", session_id=session_id
        )
        return response.json()

    def stop_sandbox(self, sandbox_id: str, session_id: str) -> dict[str, Any]:
        response = self._request(
            "POST", f"/v1/sandboxes/{sandbox_id}/stop", session_id=session_id
        )
        return response.json()

    def delete_sandbox(self, sandbox_id: str, session_id: str) -> None:
        self._request("DELETE", f"/v1/sandboxes/{sandbox_id}", session_id=session_id)

    # --- files -------------------------------------------------------------

    def put_file(
        self,
        sandbox_id: str,
        session_id: str,
        path: str,
        data: bytes,
        *,
        mode: int = 0o644,
    ) -> None:
        self._request(
            "PUT",
            f"/v1/sandboxes/{sandbox_id}/files",
            session_id=session_id,
            content=data,
            headers={"Content-Length": str(len(data))},
            params={"path": path, "mode": mode},
        )

    def get_file(self, sandbox_id: str, session_id: str, path: str, limit_bytes: int) -> bytes:
        response = self._request(
            "GET",
            f"/v1/sandboxes/{sandbox_id}/files",
            session_id=session_id,
            params={"path": path, "limit": limit_bytes},
        )
        return response.content

    def delete_file(self, sandbox_id: str, session_id: str, path: str) -> None:
        self._request(
            "DELETE",
            f"/v1/sandboxes/{sandbox_id}/files",
            session_id=session_id,
            params={"path": path},
        )

    def file_index(
        self, sandbox_id: str, session_id: str, prefix: str, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"prefix": prefix, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = self._request(
            "GET",
            f"/v1/sandboxes/{sandbox_id}/file-index",
            session_id=session_id,
            params=params,
        )
        return response.json()

    # --- executions --------------------------------------------------------

    def exec_fixed(self, sandbox_id: str, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/v1/sandboxes/{sandbox_id}/executions/fixed",
            session_id=session_id,
            json_body=body,
        )
        return response.json()

    def exec_agent(self, sandbox_id: str, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/v1/sandboxes/{sandbox_id}/executions/agent",
            session_id=session_id,
            json_body=body,
        )
        return response.json()

    def get_execution(self, execution_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/v1/executions/{execution_id}")
        return response.json()
