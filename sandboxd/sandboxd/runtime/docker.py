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
import struct
import tarfile
import threading
import time
import uuid
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
    "import json, os\n"
    "root = '/workspace'\n"
    "total = files = dirs = 0\n"
    "for dp, dns, fns in os.walk(root):\n"
    "    dirs += 1\n"
    "    for name in fns:\n"
    "        p = os.path.join(dp, name)\n"
    "        total += os.path.getsize(p) if os.path.isfile(p) else 0\n"
    "        files += 1\n"
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
        egress_proxy_container: str | None = None,
        seccomp_dir: str = "",
        workspace_uid: str = RUNNER_UID,
    ) -> None:
        self.deployment_id = deployment_id
        self.docker_host = docker_host
        self.runtime_image = runtime_image or ""
        self.egress_proxy_url = egress_proxy_url
        self.egress_proxy_container = egress_proxy_container
        self.seccomp_dir = seccomp_dir
        self.workspace_uid = workspace_uid
        self._managed_labels = {
            "com.learngraph.managed": "true",
            "com.learngraph.deployment_id": deployment_id,
        }
        # execution_id -> {exec_id, pid, container_id} for task-level cancel.
        self._active_execs: dict[str, dict[str, Any]] = {}
        self._active_execs_lock = threading.Lock()

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
        """Probe Docker Engine reachability only.

        Runtime image presence is a controller/store concern (env pin or
        Bootstrap-installed record); the adapter must not fail-closed just
        because no env image was provided when a Bootstrap record exists.
        """
        client = None
        try:
            client = self._client()
            client.ping()
        except DockerRuntimeUnavailable as exc:
            return RuntimeCapability(False, str(exc))
        except Exception:
            return RuntimeCapability(False, "Docker Engine is unavailable")
        finally:
            self._close(client)
        return RuntimeCapability(True)

    def artifact_present(self, runtime_digest: str) -> bool:
        """True when the immutable runtime image still exists in Docker Engine.

        Readiness and status reporting must not trust a persisted runtime record
        alone: if the image is deleted while the daemon is running, every new
        sandbox fails at create time. Presence is a cheap metadata lookup, so
        it is safe to call on every health/status probe.
        """
        client = None
        try:
            client = self._client()
            client.images.get(runtime_digest)
            return True
        except Exception:  # noqa: BLE001 - presence probe is best-effort
            return False
        finally:
            self._close(client)

    def capacity(self) -> tuple[int, int]:
        client = self._client()
        try:
            info = client.info()
            return int(info.get("NCPU") or 0), int(info.get("MemTotal") or 0)
        finally:
            self._close(client)

    def observe(self) -> dict[str, Any]:
        """Aggregate live resource usage of this deployment's managed
        containers (memory working-set). Host totals are returned alongside so
        schedulers can compute pressure ratios for dynamic admission."""
        client = self._client()
        try:
            info = client.info()
            host_cpu = int(info.get("NCPU") or 0)
            host_memory = int(info.get("MemTotal") or 0)
            observed_memory = 0
            active_containers = 0
            try:
                containers = client.containers.list(
                    filters={
                        "label": f"com.learngraph.deployment_id={self.deployment_id}"
                    }
                )
            except Exception:  # noqa: BLE001 - probe is best-effort
                containers = []
            for container in containers:
                try:
                    stat = container.stats(stream=False)
                except Exception:  # noqa: BLE001 - one bad container must not fail the probe
                    continue
                mem = (stat.get("memory_stats") or {}).get("usage") or 0
                if mem:
                    observed_memory += int(mem)
                    active_containers += 1
            return {
                "host_cpu_count": host_cpu,
                "host_memory_bytes": host_memory,
                "observed_memory_bytes": observed_memory,
                "observed_cpu_percent": 0.0,
                "active_containers": active_containers,
            }
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
                "com.learngraph.volume_name": spec.workspace_ref,
                "com.learngraph.workspace_limit_bytes": str(spec.disk_bytes),
                "com.learngraph.policy_digest": spec.policy_digest or "",
                "com.learngraph.egress_network": spec.egress_network or "",
            }
        )
        labels.update(spec.labels)
        return labels

    def _egress_proxy_container(self, client: Any):
        """Locate the egress proxy container to attach to per-sandbox networks.

        Resolution order: configured container name/ID, then the compose
        service label. Failing to find the proxy is a hard error (fail closed)
        so a misconfigured egress never silently dead-ends runners.
        """
        name = (self.egress_proxy_container or "").strip()
        candidates: list[Any] = []
        if name:
            try:
                candidates = [client.containers.get(name)]
            except Exception:  # noqa: BLE001
                candidates = []
        if not candidates:
            try:
                candidates = client.containers.list(
                    filters={"label": "com.docker.compose.service=egress-proxy"}
                )
            except Exception:  # noqa: BLE001
                candidates = []
        if not candidates:
            raise DockerRuntimeUnavailable(
                "egress proxy container was not found (configure SANDBOXD_EGRESS_PROXY_CONTAINER)"
            )
        return candidates[0]

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
        except DockerRuntimeUnavailable:
            raise
        except Exception:
            network = client.networks.create(
                spec.egress_network,
                driver="bridge",
                internal=True,
                labels={
                    "com.learngraph.managed": "true",
                    "com.learngraph.deployment_id": self.deployment_id,
                    "com.learngraph.sandbox_id": spec.sandbox_id,
                },
            )
        # Attach the egress proxy to the per-sandbox network so the runner can
        # actually reach it, and alias it with the proxy URL hostname (e.g.
        # ``egress-proxy``) so ``HTTP(S)_PROXY=http://egress-proxy:8888``
        # resolves inside the isolated network. Idempotent: an
        # already-attached proxy is skipped.
        from urllib.parse import urlsplit

        proxy_host = ""
        try:
            proxy_host = (urlsplit(self.egress_proxy_url).hostname or "").strip()
        except Exception:  # noqa: BLE001
            proxy_host = ""
        proxy = self._egress_proxy_container(client)
        try:
            attached = [c.id for c in network.containers]
        except Exception:  # noqa: BLE001
            attached = []
        if proxy.id not in attached:
            try:
                network.connect(proxy, aliases=[proxy_host] if proxy_host else None)
            except Exception as exc:  # noqa: BLE001
                raise DockerRuntimeUnavailable(
                    f"failed to attach egress proxy to {spec.egress_network}: {type(exc).__name__}"
                ) from exc
        return spec.egress_network

    def create(self, spec: RuntimeCreateSpec) -> RuntimeHandle:
        if not image_ref_is_pinned(spec.runtime_ref):
            raise DockerRuntimeUnavailable("runtime image must be an immutable sha256 digest")
        client = self._client()
        container = None
        try:
            from docker.types import Mount, Ulimit

            client.images.get(spec.runtime_ref)
            try:
                volume = client.volumes.get(spec.workspace_ref)
                if volume.labels.get("com.learngraph.managed") != "true":
                    raise DockerRuntimeUnavailable(
                        f"volume {spec.workspace_ref} exists but is not managed by this deployment"
                    )
            except DockerRuntimeUnavailable:
                raise
            except Exception:
                client.volumes.create(
                    spec.workspace_ref,
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
                spec.runtime_ref,
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
                        source=spec.workspace_ref,
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
        if not handle.runtime_instance_id:
            raise DockerRuntimeError("sandbox runtime has no container reference")
        try:
            container = client.containers.get(handle.runtime_instance_id)
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

    def install_runtime(self, source: str) -> tuple[str, dict[str, str]]:
        """Install a runtime from an opaque source (registry tag or digest).

        Docker implementation: pull the tag and resolve an immutable
        ``sha256:...`` RepoDigest. Returns ``(runtime_digest, labels)``. Tags
        that resolve to multiple repositories are rejected (the deployment
        pins one registry).
        """
        tag = (source or "").strip()
        if not tag:
            raise DockerRuntimeError("bootstrap runtime source must not be empty")
        if "@sha256:" in tag or tag.startswith("sha256:"):
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

    def smoke_test(
        self, runtime_digest: str, runtime_kind: str, *, timeout_seconds: int = 120
    ) -> tuple[bool, str]:
        """Run a bounded offline smoke of a pinned runner image.

        Creates a short-lived hardened container (no workspace volume, network
        none, runner UID, read-only rootfs, drop ALL, NNP, seccomp, resource
        limits, tmpfs) and executes fixed interpreter probes (Python + Node).
        The container is always removed in ``finally``; a crash leaves no
        managed smoke container behind.
        """
        image_ref = runtime_digest
        if not image_ref_is_pinned(image_ref):
            return False, "image is not an immutable sha256 digest"
        client = self._client()
        container = None
        try:
            from docker.types import Ulimit

            client.images.get(image_ref)
            shm_size = (
                CODE_SHM_SIZE
                if runtime_kind == CODE_RUNTIME_KIND
                else BROWSER_SHM_SIZE
            )
            container = client.containers.create(
                image_ref,
                command=["sleep", "infinity"],
                detach=True,
                name=f"lg-sandboxd-smoke-{uuid.uuid4().hex[:8]}",
                labels={
                    "com.learngraph.managed": "true",
                    "com.learngraph.deployment_id": self.deployment_id,
                    "com.learngraph.smoke": "true",
                },
                network_mode="none",
                environment=[
                    "HOME=/tmp",
                    "XDG_CONFIG_HOME=/tmp/.config",
                    "XDG_CACHE_HOME=/tmp/.cache",
                ],
                read_only=True,
                user=self.workspace_uid,
                cap_drop=["ALL"],
                security_opt=_seccomp_options(self.seccomp_dir, runtime_kind),
                mem_limit=512 * 1024 * 1024,
                memswap_limit=512 * 1024 * 1024,
                pids_limit=128,
                nano_cpus=1_000_000_000,
                ulimits=[
                    Ulimit(
                        name="fsize",
                        soft=64 * 1024 * 1024,
                        hard=64 * 1024 * 1024,
                    )
                ],
                shm_size=shm_size,
                tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=67108864,mode=1777"},
            )
            container.start()
            probes = (
                (
                    "python",
                    "-c",
                    "import sys, json; print(json.dumps({'python': sys.version.split()[0]}))",
                ),
                ("node", "--version"),
            )
            for argv in probes:
                result = self._run_exec(
                    client,
                    container,
                    argv,
                    workdir="/tmp",
                    timeout_seconds=timeout_seconds,
                    output_limit=16 * 1024,
                )
                if result.timed_out:
                    return False, f"smoke timed out for {' '.join(argv)}"
                if result.exit_code != 0:
                    detail = result.stderr.decode("utf-8", "replace").strip()[:300]
                    return False, f"smoke failed for {' '.join(argv)}: {detail or result.exit_code}"
            return True, ""
        except DockerRuntimeUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - smoke must never crash the daemon
            return False, f"smoke failed: {type(exc).__name__}"
        finally:
            if container is not None:
                try:
                    container.remove(force=True, v=False)
                except Exception:  # noqa: BLE001
                    pass
            self._close(client)

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

    def resume(self, sandbox_id: str, runtime_instance_id: str | None) -> RuntimeHandle:
        client = self._client()
        try:
            container = self._container(client, RuntimeHandle(sandbox_id, runtime_instance_id))
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
                # The egress proxy stays attached to every per-sandbox network.
                # After ``container.remove(force=True)`` the sandbox endpoint is
                # cleaned asynchronously, so disconnecting it can transiently
                # 404 and network removal can fail while the endpoint lingers.
                # Retry with a short backoff until the network is actually gone.
                removed = False
                last_error: Exception | None = None
                for attempt in range(6):
                    try:
                        # ``networks.list()`` attrs omit the Containers map, so
                        # ``network.containers`` is empty there. Re-fetch the
                        # network fresh to enumerate real endpoints (the egress
                        # proxy stays attached until disconnected).
                        fresh = client.api.inspect_network(network.id)
                        for container_id in (fresh.get("Containers") or {}):
                            try:
                                network.disconnect(container_id)
                            except Exception:  # noqa: BLE001 - endpoint already gone
                                pass
                        network.remove()
                        removed = True
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        if attempt == 5:
                            break
                        time.sleep(0.5)
                if not removed:
                    logger.warning(
                        "failed to remove managed network %s for %s: %s",
                        network.name,
                        handle.sandbox_id,
                        last_error,
                    )
        finally:
            self._close(client)

    # --- exec --------------------------------------------------------------

    def _track_exec(self, execution_id: str, entry: dict[str, Any]) -> None:
        with self._active_execs_lock:
            self._active_execs[execution_id] = entry

    def _untrack_exec(self, execution_id: str) -> None:
        with self._active_execs_lock:
            self._active_execs.pop(execution_id, None)

    def _lookup_exec(self, execution_id: str) -> dict[str, Any] | None:
        with self._active_execs_lock:
            return dict(self._active_execs[execution_id]) if execution_id in self._active_execs else None

    def _terminate_process_group(
        self,
        client: Any,
        container: Any,
        pid: int | None,
        *,
        grace_seconds: float = 2.0,
    ) -> None:
        """TERM → grace → KILL a process group inside the container.

        Never kills the container: a sibling execution (same user, different
        chat workspace) must keep running when one task times out, is
        truncated, or is cancelled.
        """
        if not pid or pid <= 0:
            return
        for signal in ("TERM", "KILL"):
            try:
                kill_exec = client.api.exec_create(
                    container.id,
                    ["kill", "-" + signal, "--", f"-{pid}"],
                    user=RUNNER_UID,
                    privileged=False,
                    tty=False,
                )
                client.api.exec_start(kill_exec)
            except Exception:  # noqa: BLE001 - best-effort process termination
                logger.debug(
                    "process-group %s signal %s failed for pid %s",
                    signal,
                    pid,
                    container.id,
                )
            if signal == "TERM":
                time.sleep(max(0.0, grace_seconds))

    def _run_exec(
        self,
        client: Any,
        container: Any,
        argv: tuple[str, ...],
        *,
        workdir: str,
        timeout_seconds: int,
        output_limit: int,
        execution_id: str | None = None,
    ) -> RuntimeExecResult:
        started = time.monotonic()
        environment = {
            "HOME": "/tmp",
            "XDG_CONFIG_HOME": "/tmp/.config",
            "XDG_CACHE_HOME": "/tmp/.cache",
        }
        # Wrap argv in setsid so the command becomes its own process-group
        # leader (PID == PGID). On timeout/truncate/cancel we can then target
        # exactly that process group instead of the whole container.
        deadline = started + max(1, timeout_seconds)
        timed_out, truncated, exec_id, pid, stdout, stderr = self._exec_and_stream(
            client,
            container,
            ("setsid",) + tuple(argv),
            workdir=workdir,
            environment=environment,
            timeout_seconds=timeout_seconds,
            deadline=deadline,
            output_limit=output_limit,
        )
        # A wrapped setsid exec that exits 127 usually means the image has no
        # setsid binary; retry once with the plain argv so the command still
        # runs (cancel degrades to best-effort for that exec).
        if not timed_out and not truncated and pid is None:
            try:
                inspect = client.api.exec_inspect(exec_id)
                exit_127 = inspect.get("ExitCode") == 127
            except Exception:  # noqa: BLE001
                exit_127 = False
            if exit_127:
                timed_out, truncated, exec_id, pid, stdout, stderr = self._exec_and_stream(
                    client,
                    container,
                    tuple(argv),
                    workdir=workdir,
                    environment=environment,
                    timeout_seconds=timeout_seconds,
                    deadline=deadline,
                    output_limit=output_limit,
                )

        if execution_id:
            self._track_exec(
                execution_id,
                {
                    "exec_id": exec_id,
                    "pid": pid,
                    "container_id": container.id,
                    "deadline": deadline,
                },
            )
        try:
            if timed_out or truncated:
                # Task-level termination: kill only this process group.
                self._terminate_process_group(client, container, pid)
                exit_code = -1
            else:
                try:
                    inspect = client.api.exec_inspect(exec_id)
                    exit_code = inspect.get("ExitCode")
                except Exception:  # noqa: BLE001
                    exit_code = None
        finally:
            if execution_id:
                self._untrack_exec(execution_id)
        latency_ms = int((time.monotonic() - started) * 1000)
        return RuntimeExecResult(
            exit_code=exit_code,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            timed_out=timed_out,
            truncated=truncated,
            latency_ms=latency_ms,
        )

    def _exec_and_stream(
        self,
        client: Any,
        container: Any,
        argv: tuple[str, ...],
        *,
        workdir: str,
        environment: dict[str, str],
        timeout_seconds: int,
        deadline: float,
        output_limit: int,
    ) -> tuple[bool, bool, str, int | None, bytearray, bytearray]:
        """Start one exec, stream its output with a bounded reader, and return
        ``(timed_out, truncated, exec_id, pid, stdout, stderr)``."""
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

        stdout = bytearray()
        stderr = bytearray()
        timed_out = False
        truncated = False
        pid: int | None = None
        socket = None
        try:
            socket = client.api.exec_start(exec_id, socket=True, demux=False)
            # Best-effort PID capture right after start (only visible while the
            # exec is running). Falls back to None, making cancel best-effort.
            try:
                inspect = client.api.exec_inspect(exec_id)
                raw_pid = inspect.get("Pid") or inspect.get("pid")
                pid = int(raw_pid) if raw_pid else None
            except Exception:  # noqa: BLE001
                pid = None
            frames: list[tuple[int, bytes]] = []

            def _read_all() -> None:
                while True:
                    frame = self._read_frame(socket)
                    if frame is None:
                        return
                    frames.append(frame)

            reader = threading.Thread(target=_read_all, daemon=True)
            reader.start()
            reader.join(timeout=max(0.1, deadline - time.monotonic()))
            if reader.is_alive():
                timed_out = True
            else:
                for stream_type, payload in frames:
                    if stream_type == 1:
                        stdout.extend(payload)
                    elif stream_type == 2:
                        stderr.extend(payload)
                    if len(stdout) + len(stderr) > output_limit:
                        truncated = True
                        break
        except Exception as exc:  # noqa: BLE001
            raise DockerRuntimeError(f"failed to stream exec output: {type(exc).__name__}") from exc
        finally:
            if socket is not None:
                try:
                    socket.close()
                except Exception:  # noqa: BLE001
                    pass
        return timed_out, truncated, exec_id, pid, stdout, stderr

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
        self,
        handle: RuntimeHandle,
        argv: tuple[str, ...],
        *,
        execution_id: str,
        timeout_seconds: int,
        output_limit: int,
    ) -> RuntimeExecResult:
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            return self._run_exec(
                client,
                container,
                argv,
                workdir=WORKSPACE_MOUNT,
                timeout_seconds=timeout_seconds,
                output_limit=output_limit,
                execution_id=execution_id,
            )
        finally:
            self._close(client)

    def exec_agent(
        self,
        handle: RuntimeHandle,
        argv: tuple[str, ...],
        *,
        execution_id: str,
        cwd: str,
        timeout_seconds: int,
        output_limit: int,
    ) -> RuntimeExecResult:
        safe_cwd = validate_relative_path(cwd or ".", allow_dot=True)
        workdir = WORKSPACE_MOUNT if safe_cwd.value == "." else f"{WORKSPACE_MOUNT}/{safe_cwd.value}"
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            return self._run_exec(
                client,
                container,
                argv,
                workdir=workdir,
                timeout_seconds=timeout_seconds,
                output_limit=output_limit,
                execution_id=execution_id,
            )
        finally:
            self._close(client)

    # --- kernels (persistent in-container REPL) ----------------------------

    @staticmethod
    def _kernel_dir(workspace_relative: str) -> str:
        safe = validate_relative_path(workspace_relative or ".", allow_dot=True).value
        base = "" if safe == "." else f"{safe}/"
        return f"{base}.learngraph"

    def start_kernel(
        self, handle: RuntimeHandle, workspace_relative: str, interpreter: str
    ) -> str:
        from sandboxd.kernel import KERNEL_CLIENT_SOURCE, KERNEL_SERVER_SOURCE

        if interpreter != "python":
            raise DockerRuntimeError(f"unsupported kernel interpreter: {interpreter}")
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            kernel_id = f"k_{uuid.uuid4().hex[:16]}"
            kernel_dir = self._kernel_dir(workspace_relative)
            port_file = f"{WORKSPACE_MOUNT}/{kernel_dir}/kernel_{kernel_id}.port"
            server_script = f"{WORKSPACE_MOUNT}/{kernel_dir}/kernel_server.py"
            self.write_file(handle, f"{kernel_dir}/kernel_server.py", KERNEL_SERVER_SOURCE.encode("utf-8"), mode=0o644)
            self.write_file(handle, f"{kernel_dir}/kernel_client.py", KERNEL_CLIENT_SOURCE.encode("utf-8"), mode=0o644)
            exec_id = client.api.exec_create(
                container.id,
                ["python", server_script, kernel_id, port_file, workspace_relative or "."],
                user=RUNNER_UID,
                workdir=WORKSPACE_MOUNT,
                environment={"HOME": "/tmp", "XDG_CONFIG_HOME": "/tmp/.config", "XDG_CACHE_HOME": "/tmp/.cache"},
                privileged=False,
                tty=False,
            )
            client.api.exec_start(exec_id, detach=True)
            # Wait for the port file (bounded poll); the server writes it after
            # binding the listener.
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                try:
                    data = self.read_file(handle, f"{kernel_dir}/kernel_{kernel_id}.port", 1024)
                    json.loads(data.decode("utf-8"))
                    return kernel_id
                except (DockerRuntimeError, DockerRuntimeUnavailable, json.JSONDecodeError, UnicodeDecodeError, OSError):
                    time.sleep(0.3)
            raise DockerRuntimeError("kernel server did not start within the deadline")
        finally:
            self._close(client)

    def exec_kernel_cell(
        self,
        handle: RuntimeHandle,
        kernel_id: str,
        workspace_relative: str,
        code: str,
        *,
        timeout_seconds: int,
        output_limit: int,
    ) -> RuntimeExecResult:
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            kernel_dir = self._kernel_dir(workspace_relative)
            port_file = f"{WORKSPACE_MOUNT}/{kernel_dir}/kernel_{kernel_id}.port"
            cell_file = f"{kernel_dir}/cell_{kernel_id}_{uuid.uuid4().hex[:8]}.py"
            client_script = f"{WORKSPACE_MOUNT}/{kernel_dir}/kernel_client.py"
            self.write_file(handle, cell_file, code.encode("utf-8"), mode=0o644)
            argv = ("python", client_script, port_file, f"{WORKSPACE_MOUNT}/{cell_file}", f"cell_{uuid.uuid4().hex[:8]}")
            return self._run_exec(
                client,
                container,
                argv,
                workdir=WORKSPACE_MOUNT,
                timeout_seconds=timeout_seconds,
                output_limit=output_limit,
            )
        finally:
            self._close(client)

    def stop_kernel(
        self, handle: RuntimeHandle, kernel_id: str, workspace_relative: str
    ) -> None:
        client = self._client()
        try:
            container = self._ensure_running(client, handle)
            kernel_dir = self._kernel_dir(workspace_relative)
            port_file = f"{WORKSPACE_MOUNT}/{kernel_dir}/kernel_{kernel_id}.port"
            kill_script = (
                "import json,os,signal;"
                f"d=json.load(open({port_file!r}));"
                "os.kill(d['pid'],signal.SIGTERM)"
            )
            try:
                self._run_exec(
                    client,
                    container,
                    ("python", "-c", kill_script),
                    workdir=WORKSPACE_MOUNT,
                    timeout_seconds=30,
                    output_limit=64 * 1024,
                )
            except Exception:  # noqa: BLE001 - best-effort kernel teardown
                pass
            try:
                self.delete_file(handle, f"{kernel_dir}/kernel_{kernel_id}.port")
            except Exception:  # noqa: BLE001 - watchdog cleanup is best-effort
                pass
        finally:
            self._close(client)

    def cancel_exec(self, handle: RuntimeHandle, execution_id: str) -> bool:
        """Terminate the process group of a live execution (best-effort).

        Returns True when the execution was tracked (and termination was
        attempted); False when it already finished or is unknown.
        """
        entry = self._lookup_exec(execution_id)
        if entry is None:
            return False
        client = self._client()
        try:
            container = self._container(client, handle)
            self._terminate_process_group(client, container, entry.get("pid"))
        except Exception:  # noqa: BLE001 - best-effort cancel
            logger.exception("cancel failed for execution %s", execution_id)
        finally:
            self._close(client)
        return True

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
                    # extractfile once per member: a streaming tar cannot seek
                    # backwards, so re-extracting the same member raises
                    # tarfile.StreamError.
                    fileobj = tar.extractfile(member)
                    if fileobj is None:
                        continue
                    while True:
                        chunk = fileobj.read(64 * 1024)
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
            "walk=[os.path.join(dp, n) for dp, dns, fns in os.walk(root) for n in sorted(dns + fns)]; "
            "items=[(os.path.relpath(p, root).replace(os.sep, '/'), os.path.getsize(p)) "
            "for p in walk if os.path.isfile(p) "
            "and (not prefix or os.path.relpath(p, root).replace(os.sep, '/').startswith(prefix))]; "
            "selected=items[offset:offset + limit]; "
            "print(json.dumps({'entries': [[rel, sz] for rel, sz in selected], "
            "'next': offset + len(selected)}))"
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


def _tar_single_file(relative_path: str, data: bytes, *, mode: int) -> io.BytesIO:
    """Build a strict single-file tar archive with a validated member name.

    uid/gid are pinned to the runner user (65532) so the Docker daemon's
    ``put_archive`` extraction chowns the file to the exec user; otherwise the
    archive lands as root-owned and the hardened runner cannot read it.
    """
    info = tarfile.TarInfo(name=relative_path)
    info.size = len(data)
    info.mode = mode
    info.uid = 65532
    info.gid = 65532
    info.uname = "learngraph"
    info.gname = "learngraph"
    info.mtime = int(time.time())
    info.type = tarfile.REGTYPE
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        tar.addfile(info, io.BytesIO(data))
    stream.seek(0)
    return stream
