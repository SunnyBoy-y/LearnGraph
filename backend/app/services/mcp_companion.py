from __future__ import annotations

"""stdio MCP Host Companion: run real-machine stdio MCP servers for Docker.

stdio MCP servers have no network port: LearnGraph normally launches them with
``npx ...`` on the host. Inside the whole-app Docker deployment the FastAPI
process cannot (and by design must not — see ``UnavailableStdioMCPAdapter``)
spawn host processes, and a TCP proxy cannot reach a process with no listener.
This companion is the host-side counterpart: it spawns a registered stdio MCP
process on the real machine, speaks JSON-RPC to it over stdin/stdout, and
exposes the same JSON-RPC surface over HTTP so the container can reach it.

Topology:

    container backend --(bridge /services/mcp-companion/rpc)--> host bridge
            --> http://127.0.0.1:34116/rpc --> companion --> stdio MCP process

The companion itself listens on loopback only (default), is protected by a
mandatory bearer token (``LEARNGRAPH_MCP_COMPANION_TOKEN``, fail closed), and
is registered in the Host Service Bridge registry as ``mcp-companion``. It is
an explicit opt-in: nothing starts unless a ``{id}.json`` launch file exists
and is enabled (default deny), mirroring the bridge registry posture.

JSON-RPC over stdio is newline-delimited; the companion matches responses by
``id`` and ignores server notifications (no id). One process is spawned per
launch file and kept alive; if it exits, the next call respawns it (log lines
are emitted on every crash so operators see the churn).
"""


import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

COMPANION_HEALTH_PATH = "/healthz"
COMPANION_RPC_PATH = "/rpc"

DEFAULT_RPC_TIMEOUT_SECONDS = 60.0


class CompanionLaunchInvalid(Exception):
    """Raised when a launch file is malformed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CompanionRPCError(Exception):
    """Raised when the stdio MCP process cannot answer a request."""

    def __init__(self, reason: str, *, status_code: int = 502) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class StdioLaunch:
    """One reviewed stdio MCP launch envelope."""

    id: str
    command: tuple[str, ...]
    env: dict[str, str]
    cwd: str | None
    timeout_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": list(self.command),
            "env": dict(self.env),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
        }


def validate_stdio_launch(raw: dict[str, Any]) -> StdioLaunch:
    if not isinstance(raw, dict):
        raise CompanionLaunchInvalid("launch entry must be a JSON object")
    launch_id = str(raw.get("id") or "").strip()
    if (
        not launch_id
        or launch_id in {".", ".."}
        or any(c in launch_id for c in ("/", "\\"))
        or any(c.isspace() for c in launch_id)
    ):
        raise CompanionLaunchInvalid("launch id must be a non-empty path-safe slug")
    command = raw.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) and c.strip() for c in command):
        raise CompanionLaunchInvalid(f"launch {launch_id!r}: command must be a non-empty list of strings")
    env = raw.get("env") or {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise CompanionLaunchInvalid(f"launch {launch_id!r}: env must be an object of strings")
    cwd = raw.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise CompanionLaunchInvalid(f"launch {launch_id!r}: cwd must be a string")
    timeout = float(raw.get("timeout_seconds") or DEFAULT_RPC_TIMEOUT_SECONDS)
    return StdioLaunch(
        id=launch_id,
        command=tuple(command),
        env=dict(env),
        cwd=cwd,
        timeout_seconds=max(1.0, timeout),
    )


class DirectoryStdioRegistry:
    """Load ``{id}.json`` launch files (fail closed, mtime-cached)."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._cache: dict[str, tuple[int, StdioLaunch]] = {}

    def refresh_into(self, registry: dict[str, StdioLaunch]) -> None:
        current: dict[str, StdioLaunch] = {}
        try:
            entries = sorted(self.directory.glob("*.json"))
        except OSError as exc:
            logger.error("stdio launch directory %s unreadable: %s", self.directory, exc)
            registry.clear()
            return
        for path in entries:
            try:
                stat = path.stat()
            except OSError:
                continue
            cached = self._cache.get(str(path))
            if cached is not None and cached[0] == stat.st_mtime_ns:
                current[cached[1].id] = cached[1]
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                launch = validate_stdio_launch(raw)
            except (OSError, ValueError, CompanionLaunchInvalid) as exc:
                logger.error("stdio launch file %s invalid; skipped: %s", path, exc)
                self._cache.pop(str(path), None)
                continue
            if launch.id != path.stem:
                logger.error("stdio launch file %s: id %r != filename; skipped", path, launch.id)
                continue
            self._cache[str(path)] = (stat.st_mtime_ns, launch)
            current[launch.id] = launch
        stale = [key for key in self._cache if key not in current]
        for key in stale:
            self._cache.pop(key, None)
        registry.clear()
        registry.update(current)


class StdioProcess:
    """One spawned stdio MCP process with JSON-RPC id-matched dispatch."""

    def __init__(self, launch: StdioLaunch) -> None:
        self.launch = launch
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[Any, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def ensure(self) -> None:
        if self.running:
            return
        env = dict(os.environ)
        env.update(self.launch.env)
        self._proc = await asyncio.create_subprocess_exec(
            *self.launch.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=self.launch.cwd,
        )
        self._reader_task = asyncio.get_running_loop().create_task(self._read_loop())
        logger.info("spawned stdio MCP %r: %s", self.launch.id, " ".join(self.launch.command))

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            async for raw_line in self._proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    logger.warning("stdio MCP %r: non-JSON stdout line ignored", self.launch.id)
                    continue
                if not isinstance(message, dict) or "id" not in message:
                    # server notification or malformed line: not a response
                    continue
                future = self._pending.pop(message["id"], None)
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("stdio MCP %r read loop failed: %s", self.launch.id, exc)
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(CompanionRPCError("stdio MCP process exited"))
            self._pending.clear()

    async def request(self, payload: dict[str, Any], timeout_seconds: float | None = None) -> dict[str, Any]:
        await self.ensure()
        assert self._proc is not None and self._proc.stdin is not None
        message_id = payload.get("id")
        if message_id is None:
            raise CompanionRPCError("JSON-RPC request must carry an id")
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            line = json.dumps(payload, ensure_ascii=False) + "\n"
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()
            try:
                response = await asyncio.wait_for(
                    future, timeout=timeout_seconds or self.launch.timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                self._pending.pop(message_id, None)
                raise CompanionRPCError(
                    f"stdio MCP {self.launch.id!r} timed out after "
                    f"{timeout_seconds or self.launch.timeout_seconds}s",
                    status_code=504,
                ) from exc
            return response
        except BrokenPipeError as exc:
            raise CompanionRPCError(f"stdio MCP {self.launch.id!r} stdin closed") from exc

    async def shutdown(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass
        self._proc = None


class StdioCompanion:
    """FastAPI app bridging HTTP JSON-RPC to registered stdio MCP processes."""

    def __init__(
        self,
        *,
        token: str,
        registry: dict[str, StdioLaunch] | None = None,
        source: DirectoryStdioRegistry | None = None,
        refresh_seconds: float = 5.0,
    ) -> None:
        if not token:
            raise ValueError("Stdio MCP Companion requires a non-empty bearer token (fail closed)")
        self.token = token
        self.registry = registry if registry is not None else {}
        self.source = source
        self.refresh_seconds = max(1.0, refresh_seconds)
        self._processes: dict[str, StdioProcess] = {}
        self._watchdog: asyncio.Task | None = None
        self._app = self._build_app()

    def app(self) -> FastAPI:
        return self._app

    def _launch_for(self, launch_id: str) -> StdioLaunch | None:
        launch = self.registry.get(launch_id)
        return launch

    def _require_token(self, request: Request) -> None:
        if request.headers.get("authorization", "") != f"Bearer {self.token}":
            raise CompanionRPCError("missing or invalid bearer token", status_code=403)

    def _build_app(self) -> FastAPI:
        @asynccontextmanager
        async def lifespan(_: FastAPI) -> Any:
            if self.source is not None:
                self.source.refresh_into(self.registry)
                self._watchdog = asyncio.get_running_loop().create_task(self._watchdog_loop())
            try:
                yield
            finally:
                if self._watchdog is not None:
                    self._watchdog.cancel()
                    try:
                        await self._watchdog
                    except asyncio.CancelledError:
                        pass
                    self._watchdog = None
                for process in list(self._processes.values()):
                    await process.shutdown()
                self._processes.clear()

        app = FastAPI(title="LearnGraph stdio MCP Host Companion", docs_url=None, redoc_url=None, lifespan=lifespan)

        @app.get(COMPANION_HEALTH_PATH)
        async def healthz(request: Request) -> Response:
            try:
                self._require_token(request)
            except CompanionRPCError as exc:
                return JSONResponse(status_code=exc.status_code, content={"error": exc.reason})
            states = {
                launch_id: "running" if (proc := self._processes.get(launch_id)) is not None and proc.running else "stopped"
                for launch_id in self.registry
            }
            return JSONResponse(status_code=200, content={"status": "ok", "processes": states})

        @app.post(COMPANION_RPC_PATH)
        async def rpc(request: Request) -> Response:
            try:
                self._require_token(request)
                body = await request.json()
            except CompanionRPCError as exc:
                return JSONResponse(status_code=exc.status_code, content={"error": exc.reason})
            except ValueError:
                return JSONResponse(status_code=400, content={"error": "request body must be JSON"})
            if not isinstance(body, dict):
                return JSONResponse(status_code=400, content={"error": "request body must be an object"})
            launch_id = body.get("launch_id") or body.get("server_id")
            if not isinstance(launch_id, str) or not launch_id:
                return JSONResponse(status_code=400, content={"error": "launch_id is required"})
            launch = self._launch_for(launch_id)
            if launch is None:
                return JSONResponse(status_code=404, content={"error": f"unknown launch {launch_id!r}"})
            rpc_payload = body.get("message")
            if not isinstance(rpc_payload, dict) or "method" not in rpc_payload:
                return JSONResponse(status_code=400, content={"error": "message must be a JSON-RPC object"})
            process = self._processes.get(launch_id)
            if process is None:
                process = StdioProcess(launch)
                self._processes[launch_id] = process
            try:
                response = await process.request(rpc_payload)
            except CompanionRPCError as exc:
                return JSONResponse(status_code=exc.status_code, content={"error": exc.reason})
            return JSONResponse(status_code=200, content=response)

        @app.post("/shutdown/{launch_id}")
        async def shutdown(launch_id: str, request: Request) -> Response:
            try:
                self._require_token(request)
            except CompanionRPCError as exc:
                return JSONResponse(status_code=exc.status_code, content={"error": exc.reason})
            process = self._processes.pop(launch_id, None)
            if process is not None:
                await process.shutdown()
            return JSONResponse(status_code=200, content={"ok": True})

        return app

    async def _watchdog_loop(self) -> None:
        assert self.source is not None
        try:
            while True:
                await asyncio.sleep(self.refresh_seconds)
                try:
                    self.source.refresh_into(self.registry)
                except Exception:
                    logger.exception("stdio launch registry refresh failed; previous launches remain in force")
        except asyncio.CancelledError:
            pass
