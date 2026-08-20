"""Sandbox API v1 HTTP layer.

Deployment notes:
- the daemon never sets CORS and never binds a public host port;
- service-token protected; bootstrap/admin surface uses a separate token;
- file uploads/downloads are raw octet streams (no base64), with the file
  endpoint enforcing Content-Length against the configured limit;
- every sandbox operation carries the canonical ownership scope header.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from sandboxd.auth import AdminAuth, ServiceAuth, request_id_header
from sandboxd.controller import SandboxController, SandboxdError
from sandboxd.paths import scope_key
from sandboxd.protocol import (
    AgentExecRequest,
    BootstrapJobRequest,
    Capabilities,
    Capacity,
    CreateSandboxRequest,
    ErrorBody,
    ExecResult,
    FileIndex,
    FixedExecRequest,
    HealthReady,
    KernelCellRequest,
    KernelCellResult,
    KernelOpenRequest,
    KernelOpenResult,
    SandboxView,
)

logger = logging.getLogger("sandboxd.api")

_STATUS_BY_CODE = {
    "unauthorized": 401,
    "owner_mismatch": 403,
    "protocol_incompatible": 400,
    "capability_missing": 400,
    "runner_abi_mismatch": 400,
    "sandbox_not_found": 404,
    "sandbox_expired": 404,
    "invalid_state": 409,
    "invalid_path": 400,
    "file_too_large": 413,
    "workspace_quota_exceeded": 413,
    "output_limit_exceeded": 413,
    "command_rejected": 400,
    "destructive_authorization_required": 403,
    "execution_timeout": 504,
    "execution_failed": 500,
    "execution_indeterminate": 503,
    "capacity_exceeded": 503,
    "runtime_unavailable": 503,
    "docker_unavailable": 503,
    "idempotency_conflict": 409,
    "invalid_request": 400,
}


def _error_response(exc: SandboxdError, request_id: str | None) -> JSONResponse:
    body = ErrorBody(
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        request_id=request_id,
        details=exc.details,
    )
    return JSONResponse(
        status_code=_STATUS_BY_CODE.get(exc.code, 500),
        content={"error": body.model_dump(mode="json")},
    )


class SandboxAPI:
    def __init__(self, controller: SandboxController, *, service_auth: ServiceAuth, admin_auth: AdminAuth) -> None:
        self.controller = controller
        self.service_auth = service_auth
        self.admin_auth = admin_auth

    def _scope(self, request: Request, value: str | None) -> str:
        if not value:
            raise HTTPException(status_code=400, detail="X-Sandbox-Scope header is required")
        parts = value.split("|")
        if len(parts) != 2 or any(not part for part in parts):
            raise HTTPException(status_code=400, detail="X-Sandbox-Scope must be deployment|session")
        return scope_key(parts[0], parts[1])

    @staticmethod
    def _fence(request: Request) -> int | None:
        value = request.headers.get("X-Sandbox-Fence")
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="X-Sandbox-Fence must be an integer")

    def build_router(self) -> APIRouter:
        router = APIRouter(prefix="/v1")

        @router.get("/health/live")
        def live() -> dict[str, bool]:
            return {"ok": True}

        @router.get("/health/ready", dependencies=[Depends(self.service_auth)])
        def ready() -> HealthReady:
            return self.controller.health()

        @router.get("/capabilities", dependencies=[Depends(self.service_auth)])
        def capabilities() -> Capabilities:
            return self.controller.capabilities()

        @router.get("/capacity", dependencies=[Depends(self.service_auth)])
        def capacity() -> Capacity:
            return self.controller.capacity()

        @router.post("/sandboxes", dependencies=[Depends(self.service_auth)])
        def create_sandbox(body: CreateSandboxRequest, request: Request) -> SandboxView:
            try:
                return self.controller.create(body)
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))

        @router.get("/sandboxes/{sandbox_id}", dependencies=[Depends(self.service_auth)])
        def get_sandbox(
            sandbox_id: str,
            request: Request,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> SandboxView:
            try:
                return self.controller.get(sandbox_id, self._scope(request, x_sandbox_scope), fence=self._fence(request))
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))

        @router.post("/sandboxes/{sandbox_id}/resume", dependencies=[Depends(self.service_auth)])
        def resume_sandbox(
            sandbox_id: str,
            request: Request,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> SandboxView:
            try:
                return self.controller.resume(sandbox_id, self._scope(request, x_sandbox_scope), fence=self._fence(request))
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))

        @router.post("/sandboxes/{sandbox_id}/stop", dependencies=[Depends(self.service_auth)])
        def stop_sandbox(
            sandbox_id: str,
            request: Request,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> SandboxView:
            try:
                return self.controller.stop(sandbox_id, self._scope(request, x_sandbox_scope), fence=self._fence(request))
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))

        @router.delete("/sandboxes/{sandbox_id}", dependencies=[Depends(self.service_auth)])
        def delete_sandbox(
            sandbox_id: str,
            request: Request,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> Response:
            try:
                self.controller.delete(sandbox_id, self._scope(request, x_sandbox_scope))
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        # --- files ---------------------------------------------------------

        @router.put("/sandboxes/{sandbox_id}/files", dependencies=[Depends(self.service_auth)])
        async def put_file(
            sandbox_id: str,
            request: Request,
            path: str,
            mode: int = 0o644,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> Response:
            content_length = request.headers.get("Content-Length")
            if content_length is None:
                return JSONResponse(status_code=411, content={"error": {"code": "invalid_request", "message": "Content-Length is required for file uploads"}})
            try:
                size = int(content_length)
            except ValueError:
                return JSONResponse(status_code=400, content={"error": {"code": "invalid_request", "message": "invalid Content-Length"}})
            if size > self.controller.config.max_file_bytes:
                return _error_response(SandboxdError("file_too_large", "file exceeds the daemon file limit"), request_id_header(request))
            if mode not in (0o444, 0o644, 0o600):
                return JSONResponse(status_code=400, content={"error": {"code": "invalid_request", "message": "unsupported file mode"}})
            data = await request.body()
            if len(data) != size:
                return JSONResponse(status_code=400, content={"error": {"code": "invalid_request", "message": "body length mismatch"}})
            try:
                self.controller.write_file(sandbox_id, self._scope(request, x_sandbox_scope), path, data, mode=mode, fence=self._fence(request))
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @router.get("/sandboxes/{sandbox_id}/files", dependencies=[Depends(self.service_auth)])
        def get_file(
            sandbox_id: str,
            request: Request,
            path: str,
            limit: int = 64 * 1024 * 1024,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> Response:
            bounded = max(1, min(limit, self.controller.config.max_file_bytes))
            try:
                data = self.controller.read_file(sandbox_id, self._scope(request, x_sandbox_scope), path, bounded, fence=self._fence(request))
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))
            return Response(content=data, media_type="application/octet-stream")

        @router.delete("/sandboxes/{sandbox_id}/files", dependencies=[Depends(self.service_auth)])
        def delete_file(
            sandbox_id: str,
            request: Request,
            path: str,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> Response:
            try:
                self.controller.delete_file(sandbox_id, self._scope(request, x_sandbox_scope), path, fence=self._fence(request))
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @router.get("/sandboxes/{sandbox_id}/file-index", dependencies=[Depends(self.service_auth)])
        def file_index(
            sandbox_id: str,
            request: Request,
            prefix: str = "",
            limit: int = 1000,
            cursor: str | None = None,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> FileIndex:
            bounded = max(1, min(limit, 10_000))
            try:
                return self.controller.list_files(
                    sandbox_id, self._scope(request, x_sandbox_scope), prefix, bounded, cursor, fence=self._fence(request)
                )
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))

        # --- executions ----------------------------------------------------

        @router.post("/sandboxes/{sandbox_id}/executions/fixed", dependencies=[Depends(self.service_auth)])
        def exec_fixed(
            sandbox_id: str,
            body: FixedExecRequest,
            request: Request,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> ExecResult:
            try:
                outcome = self.controller.exec_fixed(sandbox_id, self._scope(request, x_sandbox_scope), body, fence=self._fence(request))
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))
            return outcome.result

        @router.post("/sandboxes/{sandbox_id}/executions/agent", dependencies=[Depends(self.service_auth)])
        def exec_agent(
            sandbox_id: str,
            body: AgentExecRequest,
            request: Request,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> ExecResult:
            try:
                outcome = self.controller.exec_agent(sandbox_id, self._scope(request, x_sandbox_scope), body, fence=self._fence(request))
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))
            return outcome.result

        @router.get("/executions/{execution_id}", dependencies=[Depends(self.service_auth)])
        def get_execution(execution_id: str, request: Request) -> dict:
            record = self.controller.store.get_execution(execution_id)
            if record is None:
                return _error_response(
                    SandboxdError("sandbox_not_found", "execution was not found"),
                    request_id_header(request),
                )
            return {
                "execution_id": record["execution_id"],
                "sandbox_id": record["sandbox_id"],
                "status": record["status"],
                "exit_code": record["exit_code"],
                "timed_out": bool(record["timed_out"]),
                "truncated": bool(record["truncated"]),
                "latency_ms": record["latency_ms"],
                "argv_digest": record["argv_digest"],
                "cancel_requested_at": record.get("cancel_requested_at"),
                "finished_reason": record.get("finished_reason"),
                "started_at": record["started_at"],
                "finished_at": record["finished_at"],
            }

        @router.post("/executions/{execution_id}/cancel", dependencies=[Depends(self.service_auth)])
        def cancel_execution(
            execution_id: str,
            request: Request,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> dict:
            try:
                return self.controller.cancel_execution(
                    execution_id, self._scope(request, x_sandbox_scope)
                )
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))

        # --- kernels (persistent in-container REPL) --------------------------

        @router.post("/sandboxes/{sandbox_id}/kernels", dependencies=[Depends(self.service_auth)])
        def kernel_open(
            sandbox_id: str,
            body: KernelOpenRequest,
            request: Request,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> KernelOpenResult:
            try:
                return self.controller.kernel_open(
                    sandbox_id, self._scope(request, x_sandbox_scope), body
                )
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))

        @router.post("/kernels/{kernel_id}/execute", dependencies=[Depends(self.service_auth)])
        def kernel_execute(
            kernel_id: str,
            body: KernelCellRequest,
            request: Request,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> KernelCellResult:
            try:
                return self.controller.kernel_execute(
                    kernel_id, self._scope(request, x_sandbox_scope), body
                )
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))

        @router.delete("/kernels/{kernel_id}", dependencies=[Depends(self.service_auth)])
        def kernel_close(
            kernel_id: str,
            request: Request,
            x_sandbox_scope: str | None = Header(default=None, alias="X-Sandbox-Scope"),
        ) -> dict:
            try:
                return self.controller.kernel_close(
                    kernel_id, self._scope(request, x_sandbox_scope)
                )
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))

        # --- admin / bootstrap surface --------------------------------------

        @router.get("/runtimes", dependencies=[Depends(self.admin_auth)])
        def runtimes() -> list[dict]:
            return self.controller.list_runtimes()

        @router.post("/bootstrap/jobs", dependencies=[Depends(self.admin_auth)])
        def create_bootstrap_job(body: BootstrapJobRequest, request: Request) -> dict:
            try:
                return self.controller.install_runtime(
                    body.runtime_kind, body.runtime_source or body.image_tag or ""
                )
            except SandboxdError as exc:
                return _error_response(exc, request_id_header(request))

        @router.get("/bootstrap/jobs/{runtime_kind}", dependencies=[Depends(self.admin_auth)])
        def get_bootstrap_job(runtime_kind: str, request: Request) -> dict:
            record = self.controller.store.get_runtime(runtime_kind)
            if record is None:
                return _error_response(
                    SandboxdError("sandbox_not_found", "runtime record was not found"),
                    request_id_header(request),
                )
            return {
                "runtime_kind": record["runtime_kind"],
                "image_digest": record["image_digest"],
                "runner_abi": record["runner_abi"],
                "source": record["source"],
                "smoke_status": record["smoke_status"],
            }

        return router
