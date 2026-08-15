"""Docker runtime adapter for sandboxd.

Ports the hardened runner semantics of the legacy LearnGraph Docker backend
(UID 65532, read-only rootfs, drop ALL, NNP, seccomp profiles, resource
limits, network-none default, reviewed per-sandbox egress) onto named volumes
and per-sandbox internal egress networks.

Security invariants (fail closed):
- image must be an immutable sha256 digest reference;
- API callers can never express privileged / host mounts / host network /
  device / cap_add options — this adapter only builds fixed hardened specs;
- file paths are validated by the controller before reaching this layer and
  re-checked here;
- output/exec timeout kills the whole container (process tree);
- only containers/volumes/networks bearing the deployment's managed labels
  are ever touched.
"""

from __future__ import annotations

import io
import json
import logging
import re
import select
import struct
import tarfile
import time
from pathlib import Path
from typing import Any

from sandboxd.runtime.port import (
    RuntimeCapability,
    RuntimeCreateSpec,
    RuntimeExecResult,
    RuntimeFileEntry,
    RuntimeHandle,
)
from sandboxd.paths import validate_relative_path

logger = logging.getLogger(__name__)

CODE_RUNTIME_KIND = "python-node"
BROWSER_RUNTIME_KIND = "python-node-browser"
CODE_SHM_SIZE = "64m"
BROWSER_SHM_SIZE = "1g"
WORKSPACE_MOUNT = "/workspace"
RUNNER_UID = "65532:65532"
MAX_WORKSPACE_FILES = 20_000
MAX_WORKSPACE_DIRS = 5_000
_SANITIZE = re.compile(r"[^a-z0-9_.-]+")


class DockerRuntimeError(RuntimeError):
    pass


class DockerRuntimeUnavailable(DockerRuntimeError):
    pass


class DockerWorkspaceQuotaExceeded(DockerRuntimeError):
    pass


class DockerOutputLimitExceeded(DockerRuntimeError):
    pass


def image_ref_is_pinned(image_ref: str) -> bool:
    if "@sha256:" in image_ref:
        return True
    if image_ref.startswith("sha256:"):
        digest = image_ref.removeprefix("sha256:")
        return len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest)
    return False


def sanitize_name(value: str, *, max_len: int = 63) -> str:
    """Docker object name sanitization (lowercase alnum, dash, underscore, dot)."""
    cleaned = _SANITIZE.sub("-", value.casefold()).strip("-")
    return cleaned[:max_len]


def _seccomp_options(seccomp_dir: str, runtime_kind: str) -> list[str]:
    if runtime_kind == CODE_RUNTIME_KIND:
        profile_name = "seccomp_profile_code.json"
    elif runtime_kind == BROWSER_RUNTIME_KIND:
        profile_name = "seccomp_profile.json"
    else:
        raise DockerRuntimeUnavailable(f"Unsupported runtime kind: {runtime_kind}")
    profile_path = Path(seccomp_dir) / profile_name
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DockerRuntimeUnavailable(
            f"seccomp profile is unavailable for {runtime_kind}"
        ) from exc
    return [f"seccomp={json.dumps(profile, separators=(',', ':'))}", "no-new-privileges:true"]


_WALK_USAGE_SCRIPT = (
    "import json, os; "
    "root='/workspace'; total=files=dirs=0; "
    "[None for dirpath, dirnames, filenames in os.walk(root) "
    "for _ in (dirs := dirs + 1) for name in filenames "
    "if not (lambda p: (total := total + (os.path.getsize(p) if os.path.isfile(p) else 0), files := files + 1)[0])(os.path.join(dirpath, name))]; "
    "print(json.dumps({'bytes': total, 'files': files, 'dirs': dirs}))"
)


class DockerRuntimeBackend:
    """Volume-based hardened Docker runtime implementing RuntimeBackendPort."""

    def __init__(
        self,
        *,
        deployment_id: str,
        docker_host: str | None = None,
        runtime_image: str | None = None,
        egress_proxy_url: str | None = None,
        seccomp_dir: str = "",
        workspace_uid: str = RUNNER_UID,
    ) -> None:
        self.deployment_id = deployment_id
        self.docker_host = docker_host
        self.runtime_image = runtime_image or ""
        self.egress_proxy_url = egress_proxy_url
        self.seccomp_dir = seccomp_dir
        self.workspace_uid = workspace_uid
        self._managed_labels = {
            "com.learngraph.managed": "true",
            "com.learngraph.deployment_id": deployment_id,
        }

    # --- docker client -----------------------------------------------------

    def _client(self):
        try:
            import docker
        except ImportError as exc:
            raise DockerRuntimeUnavailable("docker SDK is not installed") from exc
        try:
            if self.docker_host:
                client = docker.DockerClient(base_url=self.docker_host, timeout=30)
            else:
                client = docker.from_env(timeout=30)
            client.ping()
            return client
        except Exception as exc:
            raise DockerRuntimeUnavailable(f"Docker Engine is unavailable: {type(exc).__name__}") from exc

    @staticmethod
    def _close(client: Any) -> None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    def probe(self) -> RuntimeCapability:
        if not image_ref_is_pinned(self.runtime_image):
            return RuntimeCapability(
                False, "no immutable sha256 runtime image is configured for sandboxd"
            )
        client = None
        try:
            client = self._client()
            image = client.images.get(self.runtime_image)
            if not image:
                return RuntimeCapability(False, "configured runtime image is not present in Docker Engine")
        except DockerRuntimeUnavailable as exc:
            return RuntimeCapability(False, str(exc))
        except Exception:
            return RuntimeCapability(False, "configured runtime image is not present in Docker Engine")
        finally:
            self._close(client)
        return RuntimeCapability(True)

    def capacity(self) -> tuple[int, int]:
        client = self._client()
        try:
            info = client.info()
            return int(info.get("NCPU") or 0), int(info.get("MemTotal") or 0)
        finally:
            self._close(client)

    # --- lifecycle ---------------------------------------------------------

    def _managed_labels_for(self, spec: RuntimeCreateSpec) -> dict[str, str]:
        labels = dict(self._managed_labels)
        labels.update(
            {
                "com.learngraph.sandbox_id": spec.sandbox_id,
                "com.learngraph.session_id": spec.session_id,
                "com.learngraph.runtime_kind": spec.runtime_kind,
                "com.learngraph.volume_name": spec.volume_name,
                "com.learngraph.workspace_limit_bytes": str(spec.disk_bytes),
                "com.learngraph.policy_digest": spec.policy_digest or "",
                "com.learngraph.egress_network": spec.egress_network or "",
            }
        )
        labels.update(spec.labels)
        return labels

    def _ensure_egress_network(self, client: Any, spec: RuntimeCreateSpec) -> str | None:
        if not spec.policy_digest or not spec.egress_network or not self.egress_proxy_url:
            return None
        try:
            network = client.networks.get(spec.egress_network)
            if network.labels.get("com.learngraph.managed") != "true" or network.labels.get(
                "com.learngraph.deployment_id"
            ) != self.deployment_id:
                raise DockerRuntimeUnavailable(
                    f"egress network {spec.egress_network} exists but is not managed by this deployment"
                )
            return spec.egress_network
        except DockerRuntimeUnavailable:
            raise
        except Exception:
            client.networks.create(
                spec.egress_network,
                driver="bridge",
                internal=True,
                labels={
                    "com.learngraph.managed": "true",
                    "com.learngraph.deployment_id": self.deployment_id,
                    "com.learngraph.sandbox_id": spec.sandbox_id,
                },
            )
            return spec.egress_network

    def create(self, spec: RuntimeCreateSpec) -> RuntimeHandle:
        if not image_ref_is_pinned(spec.image_ref):
            raise DockerRuntimeUnavailable("runtime image must be an immutable sha256 digest")
        client = self._client()
        container = None
        try:
            from docker.types import Mount, Ulimit

            client.images.get(spec.image_ref)
            try:
                volume = client.volumes.get(spec.volume_name)
                if volume.labels.get("com.learngraph.managed") != "true":
                    raise DockerRuntimeUnavailable(
                        f"volume {spec.volume_name} exists but is not managed by this deployment"
                    )
            except DockerRuntimeUnavailable:
                raise
            except Exception:
                client.volumes.create(
                    spec.volume_name,
                    labels={
                        "com.learngraph.managed": "true",
                        "com.learngraph.deployment_id": self.deployment_id,
                        "com.learngraph.sandbox_id": spec.sandbox_id,
                    },
                )

            egress_network = self._ensure_egress_network(client, spec)
            network_kwargs: dict[str, Any]
            container_env: list[str] = []
            if egress_network:
                network_kwargs = {"network": egress_network}
                container_env = [
                    "HTTP_PROXY=" + self.egress_proxy_url,
                    "HTTPS_PROXY=" + self.egress_proxy_url,
                    "NO_PROXY=localhost,127.0.0.1,.local",
                    "LEARNGRAPH_EGRESS_POLICY_DIGEST=" + spec.policy_digest,
                ]
            else:
                # Default posture: fully offline.
                network_kwargs = dict(network_mode="none")

            shm_size = CODE_SHM_SIZE if spec.runtime_kind == CODE_RUNTIME_KIND else BROWSER_SHM_SIZE
            container = client.containers.create(
                spec.image_ref,
                command=["sleep", "infinity"],
                detach=True,
                name=f"lg-sb-{spec.sandbox_id}",
                labels=self._managed_labels_for(spec),
                **network_kwargs,
                environment=(
                    container_env
                    + [
                        "HOME=/tmp",
                        "XDG_CONFIG_HOME=/tmp/.config",
                        "XDG_CACHE_HOME=/tmp/.cache",
                    ]
                ),
                read_only=True,
                user=self.workspace_uid,
                cap_drop=["ALL"],
                security_opt=_seccomp_options(self.seccomp_dir, spec.runtime_kind),
                mem_limit=spec.memory_bytes,
                memswap_limit=spec.memory_swap_bytes,
                pids_limit=spec.pids_max,
                nano_cpus=int(spec.cpu_count * 1_000_000_000),
                ulimits=[
                    Ulimit(name="fsize", soft=spec.disk_bytes, hard=spec.disk_bytes),
                ],
                shm_size=shm_size,
                mounts=[
                    Mount(
                        target=WORKSPACE_MOUNT,
                        source=spec.volume_name,
                        type="volume",
                        read_only=False,
                    )
                ],
                tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=67108864,mode=1777"},
            )
            container.start()
            return RuntimeHandle(spec.sandbox_id, container.id)
        except DockerRuntimeUnavailable:
            raise
        except Exception as exc:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:  # noqa: BLE001
                    pass
            raise DockerRuntimeError(f"failed to create sandbox runtime: {type(exc).__name__}") from exc
        finally:
            self._close(client)

    def _container(self, client: Any, handle: RuntimeHandle):
        if not handle.container_id:
            raise DockerRuntimeError("sandbox runtime has no container reference")
        try:
            container = client.containers.get(handle.container_id)
        except Exception as exc:
            raise DockerRuntimeError("sandbox runtime container is missing") from exc
        if container.labels.get("com.learngraph.managed") != "true" or container.labels.get(
            "com.learngraph.deployment_id"
        ) != self.deployment_id:
            raise DockerRuntimeUnavailable(
                "container is not managed by this sandboxd deployment"
            )
        return container

    # --- bootstrap ---------------------------------------------------------

    def pull_and_resolve_digest(self, image_tag: str) -> tuple[str, dict[str, str]]:
        """Pull a tag and resolve an immutable ``sha256:...`` RepoDigest.

        Returns ``(digest, image_labels)``. Tags that resolve to multiple
        repositories are rejected (the deployment pins one registry).
        """
        tag = (image_tag or "").strip()
        if not tag:
            raise DockerRuntimeError("bootstrap image tag must not be empty")
        if "@sha256:" in tag:
            # Already an immutable reference; verify presence without pulling.
            digest_ref = tag
        else:
            client = None
            try:
                client = self._client()
                try:
                    pulled = client.images.pull(tag)
                except Exception as exc:
                    raise DockerRuntimeUnavailable(
                        f"failed to pull bootstrap image {tag}: {type(exc).__name__}"
                    ) from exc
                repo_digests = [d for d in (pulled.attrs or {}).get("RepoDigests") or [] if "@sha256:" in d]
                if not repo_digests:
                    raise DockerRuntimeError(
                        f"bootstrap image {tag} has no RepoDigest; refusing to pin it"
                    )
                if len({d.split("@", 1)[1] for d in repo_digests}) != 1:
                    raise DockerRuntimeError(
                        f"bootstrap image {tag} resolves to multiple digests; refusing to pin it"
                    )
                digest_ref = repo_digests[0]
            finally:
                self._close(client)
        if not image_ref_is_pinned(digest_ref):
            raise DockerRuntimeError(f"resolved image reference is not pinned: {digest_ref}")
        client = None
        try:
            client = self._client()
            image = client.images.get(digest_ref)
            labels = dict(image.labels or {})
        except Exception as exc:
            raise DockerRuntimeUnavailable(
                f"pinned bootstrap image {digest_ref} is not present in Docker Engine"
            ) from exc
        finally:
            self._close(client)
        return digest_ref, labels

    def list_managed(self, deployment_id: str) -> list[tuple[str, str]]:
        """Return ``(sandbox_id, created_at)`` for managed containers of a deployment.

        Used by reconciliation to detect orphans (objects whose durable record
        is gone). Only containers bearing the deployment's managed labels are
        considered.
        """
        client = self._client()
        try:
            containers = client.containers.list(
                all=True,
                filters={
                    "label": [
                        "com.learngraph.managed=true",
                        f"com.learngraph.deployment_id={deployment_id}",
                    ]
                },
            )
            result: list[tuple[str, str]] = []
            for container in containers:
                sandbox_id = container.labels.get("com.learngraph.sandbox_id")
                if not sandbox_id:
                    continue
                created_at = ""
                try:
                    created_at = str((container.attrs or {}).get("Created") or "")
                except Exception:  # noqa: BLE001
                    pass
                result.append((sandbox_id, created_at))
            return result
        finally:
            self._close(client)

    def resume(self, sandbox_id: str, container_id: str | None) -> RuntimeHandle:
        client = self._client()
        try:
            container = self._container(client, RuntimeHandle(sandbox_id, container_id))
            if container.status != "running":
                container.start()
            return RuntimeHandle(sandbox_id, container.id)
        finally:
            self._close(client)

    def stop(self, handle: RuntimeHandle) -> None:
        client = self._client()
        try:
            container = self._container(client, handle)
            if container.status == "running":
                container.stop(timeout=10)
        except DockerRuntimeError:
            # Already gone / never existed — stop is best-effort idempotent.
            return
        finally:
            self._close(client)

    def delete(self, handle: RuntimeHandle) -> None:
        client = self._client()
        try:
            try:
                container = self._container(client, handle)
                container.remove(force=True, v=True)
            except DockerRuntimeError:
                # Container already gone; nothing to remove.
                pass
            except Exception:
                # Unknown container (missing) — continue with volume cleanup.
                pass
            # Remove the sandbox volume by managed label lookup (container id
            # may be stale after a recreate).
            labels = {
                "com.learngraph.managed": "true",
                "com.learngraph.deployment_id": self.deployment_id,
                "com.learngraph.sandbox_id": handle.sandbox_id,
            }
            for volume in client.volumes.list(filters={"label": _label_filter(labels)}):
                try:
                    volume.remove(force=True)
                except Exception:  # noqa: BLE001
                    logger.warning("failed to remove managed volume for %s", handle.sandbox_id)
            for network in client.networks.list(filters={"label": _label_filter(labels)}):
                try:
                    network.remove()
                except Exception:  # noqa: BLE001
                    logger.warning("failed to remove managed network for %s", handle.sandbox_id)
        finally:
            self._close(client)

    # --- exec --------------------------------------------------------------

    def _run_exec(
        self,
        client: Any,
        container: Any,
        argv: tuple[str, ...],
        *,
        workdir: str,
        timeout_seconds: int,
        output_limit: int,
    ) -> RuntimeExecResult:
        started = time.monotonic()
        environment = {
            "HOME": "/tmp",
            "XDG_CONFIG_HOME": "/tmp/.config",
            "XDG_CACHE_HOME": "/tmp/.cache",
        }
        try:
            exec_id = client.api.exec_create(
                container.id,
                list(argv),
                user=RUNNER_UID,
                workdir=workdir,
                environment=environment,
                privileged=False,
                tty=False,
            )
        except Exception as exc:
            raise DockerRuntimeError(f"failed to create exec: {type(exc).__name__}") from exc

        deadline = started + max(1, timeout_seconds)
        stdout = bytearray()
        stderr = bytearray()
        timed_out = False
        truncated = False
        try:
            socket = client.api.exec_start(exec_id, socket=True, demux=False)
            socket.settimeout(0.5)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                ready, _, _ = select.select([socket], [], [], min(remaining, 1.0))
                if not ready:
                    continue
                frame = self._read_frame(socket)
                if frame is None:
                    break
                stream_type, payload = frame
                if stream_type == 1:
                    stdout.extend(payload)
                elif stream_type == 2:
                    stderr.extend(payload)
                if len(stdout) + len(stderr) > output_limit:
                    truncated = True
                    break
        except (OSError, TimeoutError):
            # Deadline exceeded while waiting for frames.
            timed_out = True
        except Exception as exc:  # noqa: BLE001
            raise DockerRuntimeError(f"failed to stream exec output: {type(exc).__name__}") from exc
        finally:
            try:
                socket.close()
            except Exception:  # noqa: BLE001
                pass

        if timed_out or truncated:
            try:
                client.api.kill(container.id, signal="SIGKILL")
            except Exception:  # noqa: BLE001
                pass
            exit_code = -1
        else:
            try:
                inspect = client.api.exec_inspect(exec_id)
                exit_code = inspect.get("ExitCode")
            except Exception:  # noqa: BLE001
                exit_code = None
        latency_ms = int((time.monotonic() - started) * 1000)
        return RuntimeExecResult(
            exit_code=exit_code,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            timed_out=timed_out,
            truncated=truncated,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _read_frame(socket) -> tuple[int, bytes] | None:
        header = bytearray()
        while len(header) < 8:
            try:
                chunk = socket.recv(8 - len(header))
            except Exception:  # noqa: BLE001
                return None
            if not chunk:
                return None
            header.extend(chunk)
        stream_type = header[0]
        length = struct.unpack(">I", header[4:8])[0]
        if length == 0:
            return None
        payload = bytearray()
        while len(payload) < length:
            try:
                chunk = socket.recv(length - len(payload))
            except Exception:  # noqa: BLE001
                return None
            if not chunk:
                return None
            payload.extend(chunk)
        return stream_type, bytes(payload)

    def _ensure_running(self, client: Any, handle: RuntimeHandle):
        container = self._container(client, handle)
        if container.status != "running":
            container.start()
        return container

    def exec_fixed(
        self, handle: RuntimeHandle, argv: tuple[str, ...], *, timeout_seconds: int, output_limit: int
    ) -> RuntimeExecResult:
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            return self._run_exec(
                client, container, argv, workdir=WORKSPACE_MOUNT, timeout_seconds=timeout_seconds, output_limit=output_limit
            )
        finally:
            self._close(client)

    def exec_agent(
        self, handle: RuntimeHandle, argv: tuple[str, ...], *, cwd: str, timeout_seconds: int, output_limit: int
    ) -> RuntimeExecResult:
        safe_cwd = validate_relative_path(cwd or ".", allow_dot=True)
        workdir = WORKSPACE_MOUNT if safe_cwd.value == "." else f"{WORKSPACE_MOUNT}/{safe_cwd.value}"
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            return self._run_exec(
                client, container, argv, workdir=workdir, timeout_seconds=timeout_seconds, output_limit=output_limit
            )
        finally:
            self._close(client)

    # --- files -------------------------------------------------------------

    def write_file(self, handle: RuntimeHandle, path: str, data: bytes, *, mode: int = 0o644) -> None:
        safe = validate_relative_path(path)
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            usage = self._workspace_usage_locked(client, container)
            limits = self._limits_from_container(container)
            incoming = len(data)
            if usage["bytes"] + incoming > limits["disk_bytes"]:
                raise DockerWorkspaceQuotaExceeded("workspace byte quota exceeded")
            if usage["files"] + 1 > MAX_WORKSPACE_FILES:
                raise DockerWorkspaceQuotaExceeded("workspace file count quota exceeded")
            tar_stream = _tar_single_file(safe.value, data, mode=mode)
            try:
                container.put_archive(WORKSPACE_MOUNT, tar_stream)
            except Exception as exc:
                raise DockerRuntimeError(f"failed to write workspace file: {type(exc).__name__}") from exc
        finally:
            self._close(client)

    def delete_file(self, handle: RuntimeHandle, path: str) -> None:
        safe = validate_relative_path(path)
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            result = self._run_exec(
                client,
                container,
                ("rm", "-f", "--", f"{WORKSPACE_MOUNT}/{safe.value}"),
                workdir=WORKSPACE_MOUNT,
                timeout_seconds=30,
                output_limit=64 * 1024,
            )
            if result.exit_code not in (0, 1):
                raise DockerRuntimeError("failed to delete workspace file")
        finally:
            self._close(client)

    def read_file(self, handle: RuntimeHandle, path: str, limit_bytes: int) -> bytes:
        safe = validate_relative_path(path)
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            try:
                stream, _ = container.get_archive(f"{WORKSPACE_MOUNT}/{safe.value}")
            except Exception as exc:
                raise DockerRuntimeError(f"failed to read workspace file: {type(exc).__name__}") from exc
            data = bytearray()
            with tarfile.open(fileobj=_StreamReader(stream), mode="r|") as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    while True:
                        chunk = member_fileobj_read(tar, member, 64 * 1024)
                        if not chunk:
                            break
                        data.extend(chunk)
                        if len(data) > limit_bytes:
                            raise DockerRuntimeError("workspace file exceeds the read limit")
            return bytes(data)
        finally:
            self._close(client)

    def list_files(
        self, handle: RuntimeHandle, prefix: str, limit: int, cursor: str | None
    ) -> tuple[list[RuntimeFileEntry], str | None]:
        safe_prefix = validate_relative_path(prefix, allow_dot=True).value
        if safe_prefix == ".":
            safe_prefix = ""
        offset = 0
        if cursor:
            try:
                offset = max(0, int(cursor))
            except ValueError:
                raise DockerRuntimeError("invalid list cursor")
        script = (
            "import json, os, sys; "
            "root='/workspace'; prefix=sys.argv[1]; limit=int(sys.argv[2]); offset=int(sys.argv[3]); "
            "entries=[]; count=0; "
            "walk=[(os.path.join(dp, n)) for dp, dns, fns in os.walk(root) for n in sorted(dns + fns)]; "
            "[None for p in walk if os.path.isfile(p) "
            "for rel in [os.path.relpath(p, root).replace(os.sep, '/')] "
            "if (not prefix or rel.startswith(prefix)) "
            "and (count := count + 1) > offset "
            "and len(entries) < limit "
            "and entries.append([rel, os.path.getsize(p) if os.path.isfile(p) else 0])]; "
            "print(json.dumps({'entries': entries, 'next': offset + len(entries)}))"
        )
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            result = self._run_exec(
                client,
                container,
                ("python", "-c", script, safe_prefix, str(limit), str(offset)),
                workdir=WORKSPACE_MOUNT,
                timeout_seconds=60,
                output_limit=1024 * 1024,
            )
            if result.exit_code != 0 or result.timed_out or result.truncated:
                raise DockerRuntimeError("failed to list workspace files")
            try:
                payload = json.loads(result.stdout.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as exc:
                raise DockerRuntimeError("workspace listing returned invalid JSON") from exc
            entries = [
                RuntimeFileEntry(path=str(item[0]), size_bytes=int(item[1]))
                for item in payload.get("entries", [])
                if isinstance(item, list) and len(item) == 2
            ]
            return entries, str(payload.get("next") or "")
        finally:
            self._close(client)

    def workspace_usage(self, handle: RuntimeHandle) -> dict[str, int]:
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            return self._workspace_usage_locked(client, container)
        finally:
            self._close(client)

    def _workspace_usage_locked(self, client: Any, container: Any) -> dict[str, int]:
        result = self._run_exec(
            client,
            container,
            ("python", "-c", _WALK_USAGE_SCRIPT),
            workdir=WORKSPACE_MOUNT,
            timeout_seconds=60,
            output_limit=1024 * 1024,
        )
        if result.exit_code != 0 or result.timed_out or result.truncated:
            raise DockerRuntimeError("failed to compute workspace usage")
        try:
            payload = json.loads(result.stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise DockerRuntimeError("workspace usage returned invalid JSON") from exc
        return {
            "bytes": int(payload.get("bytes") or 0),
            "files": int(payload.get("files") or 0),
            "dirs": int(payload.get("dirs") or 0),
        }

    @staticmethod
    def _limits_from_container(container: Any) -> dict[str, int]:
        raw = container.labels.get("com.learngraph.workspace_limit_bytes") or "0"
        try:
            disk_bytes = int(raw)
        except (ValueError, TypeError):
            disk_bytes = 0
        return {"disk_bytes": disk_bytes}


def _label_filter(labels: dict[str, str]) -> list[str]:
    return [f"{key}={value}" for key, value in labels.items()]


class _StreamReader(io.RawIOBase):
    """Adapter exposing a Docker archive stream as a readable binary stream."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self._buffer = b""

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if self._buffer:
            chunk, self._buffer = self._buffer[:size], self._buffer[size:]
            return chunk
        try:
            chunk = next(self._stream)
        except StopIteration:
            return b""
        if size is not None and size >= 0 and len(chunk) > size:
            self._buffer = chunk[size:]
            return chunk[:size]
        return chunk


def member_fileobj_read(tar: tarfile.TarFile, member: tarfile.TarInfo, size: int) -> bytes:
    fileobj = tar.extractfile(member)
    if fileobj is None:
        return b""
    return fileobj.read(size)


def _tar_single_file(relative_path: str, data: bytes, *, mode: int) -> io.BytesIO:
    """Build a strict single-file tar archive with a validated member name."""
    info = tarfile.TarInfo(name=relative_path)
    info.size = len(data)
    info.mode = mode
    info.mtime = int(time.time())
    info.type = tarfile.REGTYPE
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        tar.addfile(info, io.BytesIO(data))
    stream.seek(0)
    return stream
