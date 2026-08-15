"""sandboxd ASGI entrypoint and application factory."""

from __future__ import annotations

import logging
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sandboxd.api import SandboxAPI
from sandboxd.auth import AdminAuth, ServiceAuth, request_id_middleware
from sandboxd.config import SandboxdConfig, SandboxdConfigError
from sandboxd.controller import SandboxController
from sandboxd.runtime.docker import DockerRuntimeBackend
from sandboxd.runtime.port import RuntimeBackendPort
from sandboxd.store import SandboxdStore

logger = logging.getLogger("sandboxd.main")


def create_app(
    config: SandboxdConfig,
    store: SandboxdStore | None = None,
    runtime: RuntimeBackendPort | None = None,
    *,
    start_workers: bool = True,
) -> FastAPI:
    """Build the ASGI application.

    ``runtime`` may be injected (tests use a fake); the default is the Docker
    runtime adapter bound to this deployment.
    """
    if store is None:
        store = SandboxdStore(config.state_path)
    if runtime is None:
        runtime = DockerRuntimeBackend(
            deployment_id=config.deployment_id,
            docker_host=config.docker_host,
            runtime_image=config.runtime_image,
            egress_proxy_url=config.egress_proxy_url,
            seccomp_dir=config.seccomp_dir,
            workspace_uid=config.workspace_uid,
        )
    controller = SandboxController(config, store, runtime)
    api = SandboxAPI(
        controller,
        service_auth=ServiceAuth(config.token),
        admin_auth=AdminAuth(config.admin_token),
    )

    app = FastAPI(title="sandboxd", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.controller = controller
    app.state.store = store
    app.state.runtime = runtime
    app.middleware("http")(request_id_middleware)
    app.include_router(api.build_router())

    sweep_thread: threading.Thread | None = None
    stop_event = threading.Event()

    @app.middleware("http")
    async def _error_envelope(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 - never leak daemon internals
            logger.exception("unhandled sandboxd error on %s", request.url.path)
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "execution_failed",
                        "message": "internal sandboxd error",
                        "retryable": False,
                        "request_id": getattr(request.state, "request_id", ""),
                        "details": {},
                    }
                },
            )

    @app.on_event("startup")
    def _startup() -> None:
        store.init()
        if config.reconcile_on_start:
            try:
                controller.reconcile()
            except Exception:  # noqa: BLE001
                logger.exception("startup reconciliation failed")
        if start_workers:
            nonlocal stop_event, sweep_thread
            stop_event = threading.Event()
            sweep_thread = threading.Thread(
                target=_ttl_sweep_loop,
                args=(controller, config.ttl_sweep_interval_seconds, stop_event),
                name="sandboxd-ttl-sweep",
                daemon=True,
            )
            sweep_thread.start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        stop_event.set()

    return app


def _ttl_sweep_loop(controller: SandboxController, interval_seconds: int, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        stop_event.wait(interval_seconds)
        if stop_event.is_set():
            break
        try:
            deleted = controller.sweep_expired()
            if deleted:
                logger.info("TTL sweep deleted %s expired sandboxes", deleted)
        except Exception:  # noqa: BLE001
            logger.exception("TTL sweep failed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = SandboxdConfig.from_env()
    except SandboxdConfigError as exc:
        raise SystemExit(f"sandboxd configuration error: {exc}") from exc
    app = create_app(config)
    import uvicorn

    uvicorn.run(app, host=config.listen_host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
