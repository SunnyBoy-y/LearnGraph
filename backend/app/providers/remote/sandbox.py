from __future__ import annotations

import io
import json
import logging
import os
import platform as host_platform
import shutil
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path, PurePosixPath

from app.providers.ports.sandbox import (
    SandboxBackendPort,
    SandboxCapabilitySnapshot,
    SandboxCreateSpec,
    SandboxExecResult,
    SandboxSessionHandle,
    SandboxWorkspaceFile,
)


class SandboxBackendError(RuntimeError):
    pass


class SandboxBackendUnavailable(SandboxBackendError):
    pass


class SandboxCapabilityMismatch(SandboxBackendError):
    pass


class SandboxWorkspaceQuotaExceeded(SandboxBackendError):
    pass


class SandboxOutputLimitExceeded(SandboxBackendError):
    pass


class SandboxDestructiveAuthorizationRequired(SandboxBackendError):
    def __init__(self, paths: tuple[str, ...]) -> None:
        super().__init__(
            "Sandbox code attempted to delete workspace files without authorization"
        )
        self.paths = paths


def image_ref_is_pinned(image_ref: str) -> bool:
    if "@sha256:" in image_ref:
        return True
    if image_ref.startswith("sha256:"):
        digest = image_ref.removeprefix("sha256:")
        return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)
    return False


DESTRUCTIVE_COMMANDS = frozenset(
    {
        "rm",
        "rmdir",
        "unlink",
        "shred",
        "wipefs",
        "mkfs",
        "dd",
        "format",
        "diskpart",
        "del",
        "erase",
        "rd",
        "remove-item",
        "removeitem",
        "clear-content",
    }
)
AGENT_EXECUTABLES = frozenset(
    {
        "python",
        "python3",
        "python3.11",
        "python3.12",
        "node",
        "nodejs",
    }
)
MAX_AGENT_ARCHIVE_BYTES = 16 * 1024 * 1024
logger = logging.getLogger(__name__)


def sandbox_seccomp_security_options() -> list[str]:
    """Allowlist seccomp profile (Docker-default style + Chromium userns).

    Applied to every sandbox container: the unified runner image ships
    Chromium, so all containers need the userns allowances while keeping the
    default-deny posture for everything else.
    """

    profile_path = Path(__file__).resolve().parents[3] / "sandbox" / "seccomp_profile.json"
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SandboxBackendUnavailable(
            "Sandbox seccomp profile is unavailable"
        ) from exc
    return [
        f"seccomp={json.dumps(profile, separators=(',', ':'))}",
        "no-new-privileges:true",
    ]


def _safe_workspace_path(path: str) -> PurePosixPath:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path or ":" in path:
        raise SandboxCapabilityMismatch("Sandbox paths must be portable relative workspace paths")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise SandboxCapabilityMismatch("Sandbox paths must be relative and cannot escape the workspace")
    return candidate


def _safe_agent_cwd(cwd_relative: str) -> str:
    # Limiting CWD to the workspace root avoids using a model-created symlink
    # as Docker's workdir.  Agent source files can still use nested relative
    # paths, but process start never follows a caller-provided directory.
    if cwd_relative != ".":
        raise SandboxCapabilityMismatch("Agent commands must use the sandbox workspace root as cwd")
    return cwd_relative


def validate_agent_workspace_path(path: str) -> str:
    """Return a portable Agent workspace path or raise a policy mismatch."""

    return str(_safe_workspace_path(path))


def validate_agent_cwd(cwd_relative: str) -> str:
    """Validate the public Agent cwd contract without exposing backend paths."""

    return _safe_agent_cwd(cwd_relative)


def validate_agent_argv(argv: tuple[str, ...], *, max_args: int = 32) -> tuple[str, ...]:
    """Validate the narrow, shell-free command contract for Agent workspaces.

    The Docker container remains the security boundary.  This policy prevents
    the model from smuggling shell syntax or host-style destructive utilities
    through the command tool, and makes code execution auditable: code must be
    materialized as a workspace ``.py`` file before it can run.
    """

    if not argv or len(argv) > max_args:
        raise SandboxCapabilityMismatch("Agent command argv has an invalid number of arguments")
    normalized: list[str] = []
    for value in argv:
        if not isinstance(value, str) or not value or len(value) > 4_096:
            raise SandboxCapabilityMismatch("Agent command arguments must be non-empty bounded strings")
        if "\x00" in value or "\r" in value or "\n" in value:
            raise SandboxCapabilityMismatch("Agent command arguments cannot contain control characters")
        normalized.append(value)
    executable = PurePosixPath(normalized[0].replace("\\", "/")).name.casefold()
    if executable in DESTRUCTIVE_COMMANDS:
        # Destructive utilities are not shell-free "agent runners".  They are
        # classified here and must be authorized (or hard-blocked) by the service
        # layer before Docker is invoked.  Host-like absolute paths stay invalid.
        if len(normalized) == 1:
            raise SandboxCapabilityMismatch(
                "Destructive commands require an explicit relative workspace path"
            )
        for item in normalized[1:]:
            if item.startswith("-"):
                continue
            if "\\" in item or ":" in item or item.startswith("/") or item.startswith(".."):
                raise SandboxCapabilityMismatch(
                    "Destructive filesystem commands cannot target host or absolute paths"
                )
            if ".." in PurePosixPath(item).parts:
                raise SandboxCapabilityMismatch(
                    "Destructive filesystem commands cannot escape the workspace"
                )
        return tuple(normalized)
    if executable not in AGENT_EXECUTABLES:
        raise SandboxCapabilityMismatch(
            "Agent commands may only invoke the sandbox Python or Node runner; "
            "shell and host utilities are blocked"
        )
    if len(normalized) == 1:
        raise SandboxCapabilityMismatch(
            "Agent execution requires a workspace script file or a version probe flag"
        )
    if executable in {"node", "nodejs"}:
        if normalized[1] in {"-e", "--eval", "-p", "--print", "-i", "--interactive", "-"}:
            raise SandboxCapabilityMismatch(
                "Inline Node execution is blocked; write a workspace .js or .mjs file"
            )
        if normalized[1] not in {"--version", "-v", "-V"}:
            source = _safe_workspace_path(normalized[1])
            if source.suffix.casefold() not in {".js", ".mjs", ".cjs"}:
                raise SandboxCapabilityMismatch(
                    "Agent Node execution requires a relative .js/.mjs/.cjs workspace file"
                )
        return tuple(normalized)
    if normalized[1] in {"-c", "-m", "-i", "--interactive", "-"}:
        raise SandboxCapabilityMismatch(
            "Inline and module Python execution are blocked; write a workspace .py file"
        )
    if normalized[1] not in {"--version", "-V"}:
        source = _safe_workspace_path(normalized[1])
        if source.suffix.casefold() != ".py":
            raise SandboxCapabilityMismatch(
                "Agent Python execution requires a relative .py workspace file"
            )
    return tuple(normalized)


class DockerSandboxBackend(SandboxBackendPort):
    backend_id = "docker"
    platform = "linux_container"

    def __init__(
        self,
        *,
        enabled: bool,
        image_ref: str | None,
        runtime_kind: str = "python-node",
    ) -> None:
        self.enabled = enabled
        self.image_ref = (image_ref or "").strip()
        self.runtime_kind = runtime_kind
        # A Linux container is the runtime in every supported host setup, but
        # surfacing the host makes Windows/macOS deployment diagnostics honest.
        self.platform = f"{host_platform.system().casefold()}_host_linux_container"

    @staticmethod
    def _client():
        try:
            import docker
        except ImportError as exc:
            raise SandboxBackendUnavailable(
                "Docker sandbox support is not installed; install the backend sandbox extra"
            ) from exc
        try:
            client = docker.from_env()
            client.ping()
            return client
        except Exception as exc:
            raise SandboxBackendUnavailable("Docker Engine is unavailable") from exc

    def probe(self) -> SandboxCapabilitySnapshot:
        if not self.enabled:
            return SandboxCapabilitySnapshot(
                available=False,
                backend_id=self.backend_id,
                platform=self.platform,
                capabilities=(),
                reason="Sandbox execution is disabled by deployment configuration",
            )
        if not image_ref_is_pinned(self.image_ref):
            return SandboxCapabilitySnapshot(
                available=False,
                backend_id=self.backend_id,
                platform=self.platform,
                capabilities=(),
                reason="Sandbox image must be configured with an immutable sha256 digest",
            )
        image_labels: dict[str, str] = {}
        try:
            client = self._client()
            image = client.images.get(self.image_ref)
            image_labels = dict(image.labels or {})
        except SandboxBackendUnavailable as exc:
            return SandboxCapabilitySnapshot(False, self.backend_id, self.platform, (), str(exc))
        except Exception:
            return SandboxCapabilitySnapshot(
                False,
                self.backend_id,
                self.platform,
                (),
                "The configured sandbox image is not present in Docker Engine",
            )
        finally:
            if "client" in locals():
                client.close()
        capabilities = [
            "isolated_workspace",
            "network_none",
            "fixed_runner",
            "agent_argv",
            "agent_workspace_files",
            "destructive_command_blocking",
            "process_tree_kill",
            "resource_limits",
            "cold_resume",
            "browser",
            "playwright",
            "chromium",
            "headless",
            "ffmpeg",
            "cjk_fonts",
            "frontend_toolchain",
            "doc_convert",
        ]
        if image_labels.get("com.learngraph.legacy-doc-extract") == "true":
            capabilities.append("legacy_doc_extract")
        return SandboxCapabilitySnapshot(
            available=True,
            backend_id=self.backend_id,
            platform=self.platform,
            capabilities=tuple(capabilities),
        )

    def host_capacity(self) -> tuple[int, int]:
        """Return Docker Engine CPU count and memory bytes."""

        client = self._client()
        try:
            info = client.info()
            return int(info.get("NCPU") or 0), int(info.get("MemTotal") or 0)
        finally:
            client.close()

    def create(self, spec: SandboxCreateSpec) -> SandboxSessionHandle:
        capability = self.probe()
        if not capability.available:
            raise SandboxBackendUnavailable(capability.reason or "Sandbox backend is unavailable")
        client = self._client()
        container = None
        try:
            from docker.types import Mount, Ulimit

            workspace_path = Path(spec.workspace_path).expanduser().resolve()
            workspace_path.mkdir(parents=True, exist_ok=True)
            container = client.containers.create(
                spec.image_ref,
                command=["sleep", "infinity"],
                detach=True,
                name=f"learngraph-sandbox-{spec.session_id}",
                labels={
                    "com.learngraph.sandbox": "true",
                    "com.learngraph.session_id": spec.session_id,
                    "com.learngraph.workspace_limit_bytes": str(spec.disk_bytes),
                },
                network_mode="none",
                read_only=True,
                user="65532:65532",
                cap_drop=["ALL"],
                # One image, one hardened posture: the allowlist seccomp
                # profile (with Chromium userns allowances) applies to every
                # container because Chromium ships in the unified image.
                security_opt=sandbox_seccomp_security_options(),
                mem_limit=spec.memory_bytes,
                memswap_limit=spec.memory_swap_bytes,
                pids_limit=spec.pids_max,
                nano_cpus=int(spec.cpu_count * 1_000_000_000),
                ulimits=[
                    Ulimit(
                        name="fsize",
                        soft=spec.disk_bytes,
                        hard=spec.disk_bytes,
                    )
                ],
                # Chromium renders through /dev/shm; tmpfs costs memory only
                # when actually used, so the browser-grade size is safe for
                # pure code sessions too.
                shm_size="1g",
                mounts=[
                    Mount(
                        target="/workspace",
                        source=str(workspace_path),
                        type="bind",
                        read_only=False,
                    )
                ],
                tmpfs={
                    "/tmp": "rw,noexec,nosuid,nodev,size=67108864,mode=1777",
                },
            )
            container.start()
            return SandboxSessionHandle(spec.session_id, container.id)
        except Exception as exc:
            if container is not None:
                try:
                    container.remove(force=True, v=True)
                except Exception:
                    pass
            raise SandboxBackendError("Docker sandbox session creation failed") from exc
        finally:
            client.close()

    def _container(self, backend_ref: str):
        container_ref = backend_ref.split("|", 1)[0]
        client = self._client()
        try:
            container = client.containers.get(container_ref)
            container.reload()
            if container.status != "running":
                raise SandboxBackendError("Sandbox container is not running")
            return client, container
        except Exception:
            client.close()
            raise

    @staticmethod
    def _workspace_source(container) -> str:
        container.reload()
        if container.labels.get("com.learngraph.sandbox") != "true":
            raise SandboxBackendError("Container is not a managed LearnGraph sandbox")
        for mount in container.attrs.get("Mounts") or []:
            if mount.get("Destination") == "/workspace" and mount.get("Type") == "bind":
                source = os.path.realpath(str(mount.get("Source") or ""))
                if source and os.path.isdir(source):
                    return source
        raise SandboxBackendError("Managed sandbox workspace mount is unavailable")

    @classmethod
    def _workspace_usage(cls, container) -> int:
        source = cls._workspace_source(container)
        total = 0
        for root, directories, files in os.walk(source, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if not os.path.islink(os.path.join(root, name))
            ]
            for name in files:
                candidate = os.path.join(root, name)
                try:
                    if not os.path.islink(candidate):
                        total += os.path.getsize(candidate)
                except FileNotFoundError:
                    continue
        return total

    @classmethod
    def _ensure_workspace_quota(cls, container, *, incoming_bytes: int = 0) -> None:
        raw_limit = container.labels.get("com.learngraph.workspace_limit_bytes")
        try:
            limit = int(raw_limit or 0)
        except ValueError as exc:
            raise SandboxBackendError("Sandbox workspace quota label is invalid") from exc
        if limit <= 0 or cls._workspace_usage(container) + incoming_bytes > limit:
            raise SandboxWorkspaceQuotaExceeded(
                "Sandbox workspace aggregate disk quota has been exceeded"
            )

    @classmethod
    def _snapshot_workspace(
        cls, container
    ) -> tuple[Path, Path, dict[str, tuple[int, int]]]:
        source = Path(cls._workspace_source(container))
        snapshot = Path(
            tempfile.mkdtemp(prefix=".learngraph-command-snapshot-", dir=source.parent)
        )
        identities: dict[str, tuple[int, int]] = {}
        try:
            for root, directories, files in os.walk(source, followlinks=False):
                directories[:] = [
                    name
                    for name in directories
                    if not os.path.islink(os.path.join(root, name))
                ]
                relative_root = Path(root).relative_to(source)
                for name in files:
                    original = Path(root) / name
                    if original.is_symlink():
                        continue
                    relative = (relative_root / name).as_posix()
                    stat = original.stat(follow_symlinks=False)
                    identities[relative] = (stat.st_dev, stat.st_ino)
                    target = snapshot / relative_root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target, follow_symlinks=False)
            return source, snapshot, identities
        except Exception:
            shutil.rmtree(snapshot, ignore_errors=True)
            raise

    @staticmethod
    def _deletion_is_granted(
        relative: str, destructive_path_prefixes: tuple[str, ...]
    ) -> bool:
        return any(
            relative == prefix or relative.startswith(prefix.rstrip("/") + "/")
            for prefix in destructive_path_prefixes
        )

    @classmethod
    def _restore_unauthorized_deletions(
        cls,
        source: Path,
        snapshot: Path,
        identities: dict[str, tuple[int, int]],
        destructive_path_prefixes: tuple[str, ...],
    ) -> tuple[str, ...]:
        restored: list[str] = []
        for original in snapshot.rglob("*"):
            if not original.is_file() or original.is_symlink():
                continue
            relative_path = original.relative_to(snapshot)
            relative = relative_path.as_posix()
            if cls._deletion_is_granted(relative, destructive_path_prefixes):
                continue
            destination = source / relative_path
            current = source
            for part in relative_path.parts[:-1]:
                current = current / part
                if current.is_symlink():
                    current.unlink()
                current.mkdir(exist_ok=True)
            if destination.is_symlink():
                destination.unlink()
            destination_identity: tuple[int, int] | None = None
            if destination.exists():
                stat = destination.stat(follow_symlinks=False)
                destination_identity = (stat.st_dev, stat.st_ino)
            if destination_identity != identities.get(relative):
                if destination.exists():
                    destination.unlink()
                shutil.copy2(original, destination, follow_symlinks=False)
                restored.append(relative)
        return tuple(restored)

    @classmethod
    def _stream_exec(
        cls,
        client,
        container,
        *,
        argv: tuple[str, ...],
        workdir: str,
        user: str,
        environment: dict[str, str] | None,
        timeout_seconds: int,
        output_limit: int,
        started: float,
    ) -> SandboxExecResult:
        """Consume Docker exec output incrementally and bound host memory."""

        exec_id = client.api.exec_create(
            container.id,
            list(argv),
            stdout=True,
            stderr=True,
            stdin=False,
            tty=False,
            privileged=False,
            user=user,
            environment=environment,
            workdir=workdir,
        )["Id"]
        stream = client.api.exec_start(exec_id, stream=True, demux=True, tty=False)
        deadline = started + timeout_seconds
        stdout = bytearray()
        stderr = bytearray()
        truncated = False
        timed_out = False
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(next, stream, None)
        try:
            while True:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    timed_out = True
                    container.kill()
                    break
                try:
                    frame = future.result(timeout=min(0.1, remaining_time))
                except FutureTimeout:
                    cls._ensure_workspace_quota(container)
                    continue
                if frame is None:
                    break
                future = pool.submit(next, stream, None)
                cls._ensure_workspace_quota(container)
                out_chunk, err_chunk = frame if isinstance(frame, tuple) else (frame, None)
                for chunk, target in ((out_chunk, stdout), (err_chunk, stderr)):
                    if not chunk:
                        continue
                    remaining = max(0, output_limit - len(stdout) - len(stderr))
                    if len(chunk) > remaining:
                        target.extend(chunk[:remaining])
                        truncated = True
                        container.kill()
                        break
                    target.extend(chunk)
                if truncated:
                    break
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
            pool.shutdown(wait=False, cancel_futures=True)

        if timed_out or truncated:
            exit_code = -1
        else:
            inspection = client.api.exec_inspect(exec_id)
            exit_code = int(inspection.get("ExitCode") or 0)
        return SandboxExecResult(
            exit_code=exit_code,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            timed_out=timed_out,
            latency_ms=int((time.monotonic() - started) * 1_000),
            truncated=truncated,
        )

    def resume(self, session_id: str, backend_ref: str) -> SandboxSessionHandle:
        client, _ = self._container(backend_ref)
        client.close()
        return SandboxSessionHandle(session_id, backend_ref)

    def write(self, session: SandboxSessionHandle, path: str, data: bytes) -> None:
        self._put_workspace_file(session, path, data, mode=0o444)

    def write_agent_file(self, session: SandboxSessionHandle, path: str, data: bytes) -> None:
        """Materialize an Agent-authored file owned by the sandbox user only."""

        self._put_workspace_file(session, path, data, mode=0o600)

    def _put_workspace_file(
        self,
        session: SandboxSessionHandle,
        path: str,
        data: bytes,
        *,
        mode: int,
    ) -> None:
        candidate = _safe_workspace_path(path)
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as bundle:
            directories: set[PurePosixPath] = set()
            parent = candidate.parent
            while str(parent) not in {"", "."}:
                directories.add(parent)
                parent = parent.parent
            for directory in sorted(directories, key=lambda item: len(item.parts)):
                info = tarfile.TarInfo(str(directory))
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.uid = 65532
                info.gid = 65532
                bundle.addfile(info)
            info = tarfile.TarInfo(str(candidate))
            info.size = len(data)
            info.mode = mode
            info.uid = 65532
            info.gid = 65532
            bundle.addfile(info, io.BytesIO(data))
        archive.seek(0)
        client, container = self._container(session.backend_ref)
        try:
            self._ensure_workspace_quota(container, incoming_bytes=len(data))
            if not container.put_archive("/workspace", archive.getvalue()):
                raise SandboxBackendError("Sandbox input materialization was rejected")
            self._ensure_workspace_quota(container)
        except SandboxBackendError:
            raise
        except Exception as exc:
            raise SandboxBackendError("Sandbox input materialization failed") from exc
        finally:
            client.close()

    def exec_fixed(
        self,
        session: SandboxSessionHandle,
        argv: tuple[str, ...],
        *,
        timeout_seconds: int,
        output_limit: int,
    ) -> SandboxExecResult:
        allowed_prefix = ("python", "/opt/learngraph/runner.py")
        if argv[:2] != allowed_prefix:
            raise SandboxCapabilityMismatch("Only the fixed LearnGraph sandbox runner may execute")
        client, container = self._container(session.backend_ref)
        started = time.monotonic()

        try:
            return self._stream_exec(
                client,
                container,
                argv=argv,
                workdir="/workspace",
                user="65532:65532",
                environment=None,
                timeout_seconds=timeout_seconds,
                output_limit=output_limit,
                started=started,
            )
        except Exception as exc:
            raise SandboxBackendError("Sandbox runner execution failed") from exc
        finally:
            client.close()

    def exec_agent(
        self,
        session: SandboxSessionHandle,
        argv: tuple[str, ...],
        *,
        cwd_relative: str,
        timeout_seconds: int,
        output_limit: int,
        destructive_path_prefixes: tuple[str, ...] = (),
    ) -> SandboxExecResult:
        argv = validate_agent_argv(argv)
        _safe_agent_cwd(cwd_relative)
        # Do not let a user image's site-packages or a generated pycache alter
        # the command environment.  The runner image has no injected secrets,
        # and the container itself has no network or host bind mount.
        executable = PurePosixPath(argv[0].replace("\\", "/")).name.casefold()
        if executable in {"node", "nodejs"}:
            effective_argv = argv
        else:
            effective_argv = (
                argv
                if argv[1] in {"--version", "-V"}
                else (argv[0], "-I", "-B", *argv[1:])
            )
        client, container = self._container(session.backend_ref)
        try:
            source, snapshot, identities = self._snapshot_workspace(container)
        except Exception as exc:
            client.close()
            raise SandboxBackendError(
                "Sandbox workspace safety snapshot failed"
            ) from exc
        started = time.monotonic()

        environment = {
            "HOME": "/workspace",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_NO_INDEX": "1",
            "http_proxy": "",
            "https_proxy": "",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "NODE_PATH": "/usr/local/lib/node_modules",
        }
        try:
            try:
                result = self._stream_exec(
                    client,
                    container,
                    argv=effective_argv,
                    workdir="/workspace",
                    user="65532:65532",
                    environment=environment,
                    timeout_seconds=timeout_seconds,
                    output_limit=output_limit,
                    started=started,
                )
            except (SandboxCapabilityMismatch, SandboxWorkspaceQuotaExceeded):
                try:
                    self._restore_unauthorized_deletions(
                        source,
                        snapshot,
                        identities,
                        destructive_path_prefixes,
                    )
                except Exception:
                    logger.exception(
                        "Workspace restoration also failed after sandbox execution failure"
                    )
                raise
            except Exception as exc:
                try:
                    self._restore_unauthorized_deletions(
                        source,
                        snapshot,
                        identities,
                        destructive_path_prefixes,
                    )
                except Exception:
                    logger.exception(
                        "Workspace restoration also failed after sandbox execution failure"
                    )
                raise SandboxBackendError(
                    "Sandbox Agent command execution failed"
                ) from exc
            try:
                restored = self._restore_unauthorized_deletions(
                    source,
                    snapshot,
                    identities,
                    destructive_path_prefixes,
                )
            except Exception as exc:
                if result.timed_out or result.exit_code != 0:
                    logger.exception(
                        "Workspace restoration failed after unsuccessful sandbox command"
                    )
                    return result
                raise SandboxBackendError(
                    "Sandbox workspace restoration failed"
                ) from exc
            if restored:
                raise SandboxDestructiveAuthorizationRequired(restored)
            return result
        finally:
            shutil.rmtree(snapshot, ignore_errors=True)
            client.close()

    def read(self, session: SandboxSessionHandle, path: str, limit_bytes: int) -> bytes:
        candidate = _safe_workspace_path(path)
        client, container = self._container(session.backend_ref)
        try:
            stream, _ = container.get_archive(f"/workspace/{candidate}")
            archive_bytes = bytearray()
            archive_limit = min(
                MAX_AGENT_ARCHIVE_BYTES,
                limit_bytes + 1024 * 1024,
            )
            for chunk in stream:
                archive_bytes.extend(chunk)
                if len(archive_bytes) > archive_limit:
                    raise SandboxBackendError(
                        "Sandbox output archive exceeds the configured limit"
                    )
            archive = io.BytesIO(archive_bytes)
            with tarfile.open(fileobj=archive, mode="r:*") as bundle:
                members = bundle.getmembers()
                if len(members) != 1 or not members[0].isfile():
                    raise SandboxBackendError("Sandbox output archive is invalid")
                if members[0].size > limit_bytes:
                    raise SandboxBackendError("Sandbox output exceeds the configured limit")
                extracted = bundle.extractfile(members[0])
                if extracted is None:
                    raise SandboxBackendError("Sandbox output cannot be read")
                return extracted.read(limit_bytes + 1)
        except SandboxBackendError:
            raise
        except Exception as exc:
            raise SandboxBackendError("Sandbox output retrieval failed") from exc
        finally:
            client.close()

    def list_files(
        self, session: SandboxSessionHandle, limit_entries: int
    ) -> list[SandboxWorkspaceFile]:
        if limit_entries < 1:
            raise SandboxCapabilityMismatch("Sandbox file listing requires a positive entry limit")
        client, container = self._container(session.backend_ref)
        try:
            stream, _ = container.get_archive("/workspace")
            archive_bytes = bytearray()
            for chunk in stream:
                archive_bytes.extend(chunk)
                if len(archive_bytes) > MAX_AGENT_ARCHIVE_BYTES:
                    raise SandboxBackendError("Sandbox workspace listing exceeds the archive safety limit")
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as bundle:
                files: list[SandboxWorkspaceFile] = []
                for member in bundle.getmembers():
                    if not member.isfile():
                        # Symlinks and device files are intentionally omitted:
                        # they are never valid Agent workspace artifacts.
                        continue
                    candidate = PurePosixPath(member.name)
                    parts = candidate.parts
                    if parts and parts[0] in {"workspace", "."}:
                        parts = parts[1:]
                    if not parts:
                        continue
                    relative = str(PurePosixPath(*parts))
                    try:
                        safe = _safe_workspace_path(relative)
                    except SandboxCapabilityMismatch:
                        continue
                    files.append(SandboxWorkspaceFile(path=str(safe), size_bytes=member.size))
                    if len(files) > limit_entries:
                        raise SandboxBackendError("Sandbox workspace file count exceeds the policy limit")
                return sorted(files, key=lambda item: item.path)
        except SandboxBackendError:
            raise
        except Exception as exc:
            raise SandboxBackendError("Sandbox workspace listing failed") from exc
        finally:
            client.close()

    def stop(self, session: SandboxSessionHandle) -> None:
        client = self._client()
        try:
            container = client.containers.get(session.backend_ref.split("|", 1)[0])
            container.kill()
        except Exception as exc:
            raise SandboxBackendError("Sandbox stop failed") from exc
        finally:
            client.close()

    def delete(self, session: SandboxSessionHandle) -> None:
        client = self._client()
        container_ref = session.backend_ref.split("|", 1)[0]
        try:
            try:
                container = client.containers.get(container_ref)
            except Exception as exc:
                if "not found" in str(exc).casefold():
                    container = None
                else:
                    raise
            if container is not None:
                container.remove(force=True, v=True)
        except Exception as exc:
            raise SandboxBackendError("Sandbox cleanup failed") from exc
        finally:
            client.close()
