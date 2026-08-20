"""OciRuntimeBackend — runtime-neutral sandbox execution on a Linux host via
runc (no Docker daemon). This is the desktop/WSL runtime adapter.

MVP scope (Phase 3, see doc/Windows_EXE_桌面化_Phase0_测量报告_v1.0.md):
- bundle = a verified local directory with ``rootfs/`` + ``config.json`` +
  ``manifest.json`` (signed/immutable delivery comes later);
- per-instance overlay (read-only base rootfs + per-sandbox upperdir);
- per-sandbox workspace directory bind-mounted at /workspace;
- network namespace with NO interfaces by default (offline posture);
- cgroup v2 memory/cpu/pids limits, seccomp profile per runtime kind,
  capabilities dropped, no-new-privileges, runner UID 65532:65532;
- egress is NOT implemented yet (policy digest requests fail closed);
- kernels are stateless (each cell runs ``python -c`` via exec).

Requires: runc binary + root privileges (WSL distro root or native Linux).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sandboxd.config import SandboxdConfig
from sandboxd.runtime.port import (
    RuntimeCapability,
    RuntimeCreateSpec,
    RuntimeExecResult,
    RuntimeFileEntry,
    RuntimeHandle,
)

WORKSPACE_MOUNT = "/workspace"
RUNNER_UID = "65532"
RUNNER_GID = "65532"
_SLEEP_ARGS = ["sleep", "infinity"]

_SECCOMP_FILES = {
    "python-node": "seccomp_profile_code.json",
    "python-node-browser": "seccomp_profile.json",
}


class OciRuntimeError(RuntimeError):
    """Raised when the OCI runtime fails in a non-retryable way."""


class OciRuntimeUnavailable(RuntimeError):
    """Raised when runc or the host environment is unavailable."""


def _run(
    argv: list[str],
    *,
    timeout_seconds: int = 60,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout_seconds,
            input=input_bytes,
        )
    except subprocess.TimeoutExpired as exc:
        raise OciRuntimeUnavailable(f"command timed out: {' '.join(argv)}") from exc
    except OSError as exc:
        raise OciRuntimeUnavailable(f"cannot execute {' '.join(argv)}: {exc}") from exc


def _safe_relative(path: str) -> Path:
    """Validate a workspace-relative path (no escape, no absolute)."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise OciRuntimeError("invalid workspace path")
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise OciRuntimeError("workspace path escapes the workspace root")
    return candidate


def _parse_uid_gid(value: str) -> tuple[str, str]:
    parts = value.split(":", 1)
    if len(parts) != 2:
        return RUNNER_UID, RUNNER_GID
    return parts[0], parts[1]


class OciRuntimeBackend:
    """runc-backed runtime adapter (root required; designed for WSL guest)."""

    backend_id = "wsl_oci"

    def __init__(self, config: SandboxdConfig) -> None:
        self.config = config
        self.runc = os.environ.get("SANDBOXD_RUNC", "runc")
        root = Path(os.environ.get("SANDBOXD_RUNTIME_ROOT") or "/var/lib/learngraph")
        self.runtime_dir = root / "runtimes"
        self.instances_dir = root / "instances"
        self.workspaces_dir = root / "workspaces"
        self.seccomp_dir = Path(config.seccomp_dir)

    # --- helpers ---------------------------------------------------------

    def _runc(self, *args: str, timeout_seconds: int = 60) -> subprocess.CompletedProcess[bytes]:
        proc = _run([self.runc, *args], timeout_seconds=timeout_seconds)
        if proc.returncode != 0:
            detail = (proc.stderr or b"").decode(errors="replace").strip() or "runc failed"
            raise OciRuntimeUnavailable(f"runc {args[0] if args else ''}: {detail}")
        return proc

    def _manifest(self) -> dict[str, Any]:
        path = self.runtime_dir / "manifest.json"
        if not path.is_file():
            raise OciRuntimeUnavailable("runtime bundle is not installed (no manifest.json)")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OciRuntimeUnavailable(f"runtime manifest is unreadable: {exc}") from exc
        if not isinstance(data, dict):
            raise OciRuntimeUnavailable("runtime manifest is malformed")
        return data

    def _instance_dir(self, sandbox_id: str) -> Path:
        return self.instances_dir / sandbox_id

    def _workspace_dir(self, sandbox_id: str) -> Path:
        return self.workspaces_dir / sandbox_id

    def _seccomp_profile(self, runtime_kind: str) -> dict[str, Any]:
        filename = _SECCOMP_FILES.get(runtime_kind)
        if filename is None:
            raise OciRuntimeError(f"unsupported runtime kind: {runtime_kind}")
        path = self.seccomp_dir / filename
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OciRuntimeUnavailable(f"seccomp profile {path} is unreadable") from exc

    @staticmethod
    def _overlay_mount_bash(base_rootfs: Path, upper: Path, work: Path, merged: Path) -> list[str]:
        """Overlay mount issued through bash (WSL2/runc deadlock workaround)."""
        options = f"lowerdir={base_rootfs},upperdir={upper},workdir={work}"
        return [
            "bash",
            "-c",
            f"mount -t overlay overlay -o {options} {merged}",
        ]

    @staticmethod
    def _base_config() -> dict[str, Any]:
        return {
            "ociVersion": "1.0.2",
            "process": {
                "terminal": False,
                "user": {"uid": int(RUNNER_UID), "gid": int(RUNNER_GID)},
                "args": _SLEEP_ARGS,
                "env": [
                    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                    "HOME=/tmp",
                    "XDG_CONFIG_HOME=/tmp/.config",
                    "XDG_CACHE_HOME=/tmp/.cache",
                ],
                "cwd": "/workspace",
                "noNewPrivileges": True,
                "capabilities": {
                    "bounding": [],
                    "effective": [],
                    "inheritable": [],
                    "permitted": [],
                    "ambient": [],
                },
                "rlimits": [
                    {"type": "RLIMIT_NOFILE", "hard": 1024, "soft": 1024},
                    {"type": "RLIMIT_FSIZE", "hard": 8 * 1024 * 1024 * 1024, "soft": 8 * 1024 * 1024 * 1024},
                ],
            },
            "root": {"path": "rootfs", "readonly": True},
            "hostname": "learngraph-sandbox",
            "mounts": [
                {"destination": "/proc", "type": "proc", "source": "proc"},
                {
                    "destination": "/dev",
                    "type": "tmpfs",
                    "source": "tmpfs",
                    "options": ["nosuid", "strictatime", "mode=755", "size=65536k"],
                },
                {
                    "destination": "/dev/pts",
                    "type": "devpts",
                    "source": "devpts",
                    "options": ["nosuid", "noexec", "newinstance", "ptmxmode=0666", "mode=0620"],
                },
                {
                    "destination": "/dev/shm",
                    "type": "tmpfs",
                    "source": "shm",
                    "options": ["nosuid", "noexec", "nodev", "mode=1777", "size=67108864"],
                },
                {
                    "destination": "/tmp",
                    "type": "tmpfs",
                    "source": "tmpfs",
                    "options": ["rw", "noexec", "nosuid", "nodev", "size=67108864", "mode=1777"],
                },
                {
                    "destination": "/sys",
                    "type": "sysfs",
                    "source": "sysfs",
                    "options": ["nosuid", "noexec", "nodev", "ro"],
                },
            ],
            "linux": {
                "resources": {
                    "memory": {"limit": 2 * 1024 * 1024 * 1024, "swap": 2 * 1024 * 1024 * 1024},
                    "cpu": {"shares": 2048},
                    "pids": {"limit": 512},
                },
                "namespaces": [
                    {"type": "pid"},
                    {"type": "network"},
                    {"type": "ipc"},
                    {"type": "uts"},
                    {"type": "mount"},
                ],
                "maskedPaths": [
                    "/proc/acpi",
                    "/proc/asound",
                    "/proc/kcore",
                    "/proc/keys",
                    "/proc/latency_stats",
                    "/proc/timer_list",
                    "/proc/timer_stats",
                    "/proc/sched_debug",
                    "/proc/scsi",
                    "/sys/firmware",
                ],
                "readonlyPaths": [
                    "/proc/bus",
                    "/proc/fs",
                    "/proc/irq",
                    "/proc/sys",
                    "/proc/sysrq-trigger",
                ],
            },
        }

    # --- capability / capacity ---------------------------------------------

    def probe(self) -> RuntimeCapability:
        if os.geteuid() != 0:
            return RuntimeCapability(False, "OciRuntimeBackend requires root (WSL guest root)")
        if shutil.which(self.runc) is None:
            return RuntimeCapability(False, f"runc binary '{self.runc}' not found")
        try:
            self._runc("--version", timeout_seconds=10)
        except OciRuntimeUnavailable as exc:
            return RuntimeCapability(False, str(exc))
        return RuntimeCapability(True)

    def capacity(self) -> tuple[int, int]:
        cpu_count = os.cpu_count() or 1
        mem_bytes = 0
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    mem_bytes = int(line.split()[1]) * 1024
                    break
        except OSError:
            mem_bytes = 0
        return cpu_count, mem_bytes

    # --- bootstrap ---------------------------------------------------------

    def install_runtime(self, source: str) -> tuple[str, dict[str, str]]:
        """Install a runtime bundle from a local directory (Phase 3 MVP).

        ``source`` must point at a directory containing ``rootfs/``,
        ``config.json`` and ``manifest.json`` with a pinned ``runtime_digest``.
        The bundle is copied into the managed runtime dir, then smoke-tested by
        the controller (smoke_test is called separately by the controller).
        """
        src = Path(source)
        if not src.is_dir() or not (src / "rootfs").is_dir() or not (src / "config.json").is_file():
            raise OciRuntimeUnavailable(
                f"runtime source {source} is not a bundle (need rootfs/ + config.json)"
            )
        manifest_path = src / "manifest.json"
        if not manifest_path.is_file():
            raise OciRuntimeUnavailable("runtime bundle has no manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OciRuntimeUnavailable(f"runtime manifest is unreadable: {exc}") from exc
        digest = str(manifest.get("runtime_digest") or "")
        if not digest.startswith("sha256:"):
            raise OciRuntimeUnavailable("runtime manifest must pin an immutable sha256 digest")
        labels = manifest.get("labels") or {}
        if not isinstance(labels, dict):
            labels = {}

        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        # Stage OUTSIDE the runtime dir: a rootfs that contains the runtime
        # dir would otherwise be copied into itself (infinite recursion).
        staging = self.runtime_dir.parent / f".staging-{uuid.uuid4().hex[:8]}"
        shutil.copytree(src, staging, dirs_exist_ok=True, symlinks=True)
        for name in ("rootfs", "config.json", "manifest.json"):
            if not (staging / name).exists():
                shutil.rmtree(staging, ignore_errors=True)
                raise OciRuntimeUnavailable(f"runtime bundle staging lost {name}")
        # rootfs is large; move into place then drop the old one. Clean any
        # stale rootfs.old first (rename to a non-empty dir fails with
        # ENOTEMPTY on retries).
        old_rootfs = target / "rootfs.old"
        shutil.rmtree(old_rootfs, ignore_errors=True)
        if (target / "rootfs").exists():
            os.replace(target / "rootfs", old_rootfs)
        os.replace(staging / "rootfs", target / "rootfs")
        os.replace(staging / "config.json", target / "config.json")
        os.replace(staging / "manifest.json", target / "manifest.json")
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(old_rootfs, ignore_errors=True)
        return digest, dict(labels)

    def artifact_present(self, runtime_digest: str) -> bool:
        try:
            manifest = self._manifest()
        except OciRuntimeUnavailable:
            return False
        return str(manifest.get("runtime_digest") or "") == runtime_digest and (
            self.runtime_dir / "rootfs"
        ).is_dir()

    def smoke_test(
        self, runtime_digest: str, runtime_kind: str, *, timeout_seconds: int = 120
    ) -> tuple[bool, str]:
        if not self.artifact_present(runtime_digest):
            return False, "runtime bundle is not installed"
        sandbox_id = f"smoke-{uuid.uuid4().hex[:8]}"
        spec = RuntimeCreateSpec(
            sandbox_id=sandbox_id,
            session_id="smoke",
            workspace_key="smoke",
            runtime_kind=runtime_kind,
            runtime_ref=runtime_digest,
            workspace_ref="smoke",
            deployment_id=self.config.deployment_id,
            memory_bytes=512 * 1024 * 1024,
            memory_swap_bytes=512 * 1024 * 1024,
            cpu_count=1.0,
            pids_max=128,
            disk_bytes=64 * 1024 * 1024,
        )
        handle = None
        try:
            handle = self.create(spec)
            result = self.exec_agent(
                handle,
                ("python", "-c", "import sys; print(sys.version.split()[0])"),
                execution_id="smoke-probe",
                cwd=".",
                timeout_seconds=timeout_seconds,
                output_limit=4096,
            )
            ok = result.exit_code == 0 and b"3." in result.stdout
            detail = result.stdout.decode(errors="replace").strip() or result.stderr.decode(
                errors="replace"
            ).strip()
            return ok, detail
        except Exception as exc:  # noqa: BLE001 - smoke must never crash the daemon
            return False, f"{type(exc).__name__}: {exc}"
        finally:
            if handle is not None:
                try:
                    self.stop(handle)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.delete(handle)
                except Exception:  # noqa: BLE001
                    pass

    # --- lifecycle ---------------------------------------------------------

    def create(self, spec: RuntimeCreateSpec) -> RuntimeHandle:
        if spec.egress_network or spec.policy_digest:
            raise OciRuntimeError("egress is not supported by the OCI runtime MVP yet")
        manifest = self._manifest()
        if str(manifest.get("runtime_digest") or "") != spec.runtime_ref:
            raise OciRuntimeUnavailable(
                f"runtime digest mismatch: bundle has {manifest.get('runtime_digest')!r}, "
                f"spec wants {spec.runtime_ref!r}"
            )
        uid, gid = _parse_uid_gid(self.config.workspace_uid)

        instance = self._instance_dir(spec.sandbox_id)
        # WSL2/runc quirk, empirically pinned down: a FIRST-time overlay mount
        # at a fresh path deadlocks runc create (init blocks in
        # wait_for_partner forever). The reliable sequence is: mount once,
        # lazy-umount (leaves a mount-table record), delete the directory,
        # recreate fresh inodes, mount again — only this second mount works
        # with runc. All earlier variants (plain first mount, bind rootfs,
        # mount+umount+mount on the same dir) hang.
        upper = instance / "upper"
        work = instance / "work"
        merged = instance / "merged"
        workspace = self._workspace_dir(spec.sandbox_id)
        base_rootfs = self.runtime_dir / "rootfs"
        overlay_argv = [
            "mount",
            "-t",
            "overlay",
            "overlay",
            "-o",
            f"lowerdir={base_rootfs},upperdir={upper},workdir={work}",
            str(merged),
        ]
        instance.mkdir(parents=True, exist_ok=True)
        for path in (upper, work, merged, workspace):
            path.mkdir(parents=True, exist_ok=True)
        try:
            config = self._base_config()
            # Absolute root path (relative "rootfs" hung runc under WSL).
            config["root"] = {"path": str(merged), "readonly": False}
            config["process"]["user"] = {"uid": int(uid), "gid": int(gid)}
            config["process"]["cwd"] = WORKSPACE_MOUNT
            config["linux"]["resources"] = {
                "memory": {"limit": spec.memory_bytes, "swap": spec.memory_swap_bytes},
                "cpu": {"shares": max(2, int(spec.cpu_count * 1024))},
                "pids": {"limit": spec.pids_max},
            }
            # Relative cgroup path (cgroup v2 cgroupfs style); absolute paths
            # hang runc under WSL2/systemd.
            config["linux"]["cgroupsPath"] = f"learngraph/{spec.sandbox_id}"
            config["linux"]["seccomp"] = self._seccomp_profile(spec.runtime_kind)
            shm_size = "67108864"
            if spec.runtime_kind == "python-node-browser":
                shm_size = "1073741824"
            for mount in config["mounts"]:
                if mount["destination"] == "/dev/shm":
                    options = [o for o in mount["options"] if not o.startswith("size=")]
                    mount["options"] = options + [f"size={shm_size}"]
            # Workspace bind mount (host path inside the WSL distro).
            config["mounts"].append(
                {
                    "destination": WORKSPACE_MOUNT,
                    "type": "bind",
                    "source": str(workspace),
                    "options": ["rbind", "rw"],
                }
            )
            (instance / "config.json").write_text(
                json.dumps(config, separators=(",", ":")), encoding="utf-8"
            )
            if os.environ.get("SANDBOXD_OCI_DEBUG"):
                (Path("/tmp") / f"oci-debug-{spec.sandbox_id}.json").write_text(
                    json.dumps(config, indent=2), encoding="utf-8"
                )
        except Exception:
            shutil.rmtree(instance, ignore_errors=True)
            raise

        # Mount + create execute inside ONE bash process. WSL2 quirk observed
        # throughout the Phase 3 investigation: runc/crun create deadlocks
        # (init blocks in wait_for_partner) whenever the overlay was mounted
        # from a different process tree than the one invoking create. All
        # successful runs had mount and create issued from the same bash
        # process (t4/t8/t10 patterns). python-subprocess mounts and the
        # mount+umount+mount warming variants all hung.
        import shlex

        options = f"lowerdir={base_rootfs},upperdir={upper},workdir={work}"
        script = (
            f"mount -t overlay overlay -o {options} {shlex.quote(str(merged))} && "
            f"{self.runc} create --no-pivot -b {shlex.quote(str(instance))} "
            f"{shlex.quote(spec.sandbox_id)}"
        )
        proc = _run(["bash", "-c", script], timeout_seconds=90)
        if proc.returncode != 0:
            detail = (proc.stderr or b"").decode(errors="replace").strip()
            _run(["umount", "-l", str(merged)], timeout_seconds=10)
            shutil.rmtree(instance, ignore_errors=True)
            raise OciRuntimeUnavailable(f"mount+create failed: {detail}")
        try:
            self._runc("start", spec.sandbox_id)
            return RuntimeHandle(spec.sandbox_id, spec.sandbox_id)
        except Exception:
            _run(["umount", "-l", str(merged)], timeout_seconds=10)
            shutil.rmtree(instance, ignore_errors=True)
            raise

    def resume(self, sandbox_id: str, runtime_instance_id: str | None) -> RuntimeHandle:
        instance_id = runtime_instance_id or sandbox_id
        try:
            self._runc("state", instance_id, timeout_seconds=10)
        except OciRuntimeUnavailable as exc:
            raise OciRuntimeUnavailable(
                f"sandbox runtime {instance_id} is missing; recreate from record"
            ) from exc
        return RuntimeHandle(sandbox_id, instance_id)

    def stop(self, handle: RuntimeHandle) -> None:
        instance_id = handle.runtime_instance_id or handle.sandbox_id
        try:
            self._runc("kill", instance_id, "KILL", timeout_seconds=15)
        except OciRuntimeUnavailable:
            pass  # already stopped

    def delete(self, handle: RuntimeHandle) -> None:
        instance_id = handle.runtime_instance_id or handle.sandbox_id
        try:
            self._runc("delete", "-f", instance_id, timeout_seconds=15)
        except OciRuntimeUnavailable:
            pass
        _run(["umount", "-l", str(self._instance_dir(handle.sandbox_id) / "merged")], timeout_seconds=10)
        shutil.rmtree(self._instance_dir(handle.sandbox_id), ignore_errors=True)
        shutil.rmtree(self._workspace_dir(handle.sandbox_id), ignore_errors=True)

    # --- files -------------------------------------------------------------

    def _workspace_path(self, handle: RuntimeHandle, path: str) -> Path:
        return self._workspace_dir(handle.sandbox_id) / _safe_relative(path)

    def write_file(self, handle: RuntimeHandle, path: str, data: bytes, *, mode: int = 0o644) -> None:
        target = self._workspace_path(handle, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, mode)
        os.chown(target, int(RUNNER_UID), int(RUNNER_GID))

    def delete_file(self, handle: RuntimeHandle, path: str) -> None:
        target = self._workspace_path(handle, path)
        if target.is_file() or target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)

    def read_file(self, handle: RuntimeHandle, path: str, limit_bytes: int) -> bytes:
        target = self._workspace_path(handle, path)
        if not target.is_file():
            raise OciRuntimeError(f"workspace file not found: {path}")
        return target.read_bytes()[:limit_bytes]

    def list_files(
        self, handle: RuntimeHandle, prefix: str, limit: int, cursor: str | None
    ) -> tuple[list[RuntimeFileEntry], str | None]:
        root = self._workspace_dir(handle.sandbox_id)
        base = root / _safe_relative(prefix) if prefix else root
        if not base.is_dir():
            return [], None
        entries: list[RuntimeFileEntry] = []
        for child in sorted(base.rglob("*")):
            if child.is_file():
                entries.append(
                    RuntimeFileEntry(
                        path=str(child.relative_to(root)).replace("\\", "/"),
                        size_bytes=child.stat().st_size,
                    )
                )
            if len(entries) >= limit:
                break
        return entries, None

    def workspace_usage(self, handle: RuntimeHandle) -> dict[str, int]:
        workspace = self._workspace_dir(handle.sandbox_id)
        total = 0
        if workspace.is_dir():
            for path in workspace.rglob("*"):
                if path.is_file():
                    try:
                        total += path.stat().st_size
                    except OSError:
                        pass
        return {"workspace_bytes": total}

    # --- exec --------------------------------------------------------------

    def _exec(
        self,
        handle: RuntimeHandle,
        argv: tuple[str, ...],
        *,
        cwd: str,
        timeout_seconds: int,
        output_limit: int,
    ) -> RuntimeExecResult:
        import time

        instance_id = handle.runtime_instance_id or handle.sandbox_id
        started = time.monotonic()
        proc = _run(
            [
                self.runc,
                "exec",
                instance_id,
                "--cwd",
                WORKSPACE_MOUNT if cwd in ("", ".") else f"{WORKSPACE_MOUNT}/{cwd.lstrip('/')}",
                "--user",
                self.config.workspace_uid,
                "--",
                *argv,
            ],
            timeout_seconds=max(1, timeout_seconds),
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        stdout = proc.stdout[:output_limit]
        stderr = proc.stderr[:output_limit]
        truncated = len(proc.stdout) > output_limit or len(proc.stderr) > output_limit
        return RuntimeExecResult(
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            truncated=truncated,
            latency_ms=latency_ms,
        )

    def exec_fixed(
        self,
        handle: RuntimeHandle,
        argv: tuple[str, ...],
        *,
        execution_id: str,
        timeout_seconds: int,
        output_limit: int,
    ) -> RuntimeExecResult:
        return self._exec(
            handle, argv, cwd=".", timeout_seconds=timeout_seconds, output_limit=output_limit
        )

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
        return self._exec(
            handle, argv, cwd=cwd, timeout_seconds=timeout_seconds, output_limit=output_limit
        )

    def cancel_exec(self, handle: RuntimeHandle, execution_id: str) -> bool:
        # MVP: runc exec sessions cannot be cancelled individually; the caller
        # falls back to stopping the sandbox.
        return False

    # --- kernels (MVP: stateless) -------------------------------------------

    def start_kernel(self, handle: RuntimeHandle, workspace_relative: str, interpreter: str) -> str:
        if interpreter not in {"python", "python3"}:
            raise OciRuntimeError(f"unsupported interpreter: {interpreter}")
        return f"stateless-{handle.sandbox_id}"

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
        return self._exec(
            handle,
            ("python", "-c", code),
            cwd=workspace_relative,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
        )

    def stop_kernel(self, handle: RuntimeHandle, kernel_id: str, workspace_relative: str) -> None:
        return None
