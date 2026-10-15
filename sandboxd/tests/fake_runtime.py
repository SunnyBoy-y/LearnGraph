"""In-memory RuntimeBackendPort fake for sandboxd unit tests."""

from __future__ import annotations

import json
import time
import uuid

from sandboxd.runtime.port import (
    RuntimeCapability,
    RuntimeCreateSpec,
    RuntimeExecResult,
    RuntimeFileEntry,
    RuntimeHandle,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.files: dict[tuple[str, str], bytes] = {}
        self.created: list[RuntimeCreateSpec] = []
        self.deleted: list[str] = []
        self.stopped: list[str] = []
        self.resumed: list[str] = []
        self.exec_calls: list[tuple[str, tuple[str, ...]]] = []
        self.cancelled_execs: list[str] = []
        self.available = True
        self.quota_exceeded = False
        self.fail_next_exec = False
        self.slow_exec = False
        self.managed: dict[str, str] = {}
        self.usage: dict[str, int] | None = None
        self.smoke_ok = True
        self.smoke_detail: str | None = None

    def artifact_present(self, runtime_digest: str) -> bool:
        return self.available and bool(runtime_digest)

    def install_runtime(self, image_tag: str) -> tuple[str, dict[str, str]]:
        if image_tag.startswith("sha256:") or "@sha256:" in image_tag:
            digest = image_tag
        else:
            import hashlib

            digest = "sha256:" + hashlib.sha256(image_tag.encode("utf-8")).hexdigest()
        return digest, {"com.learngraph.runner-abi": "1"}

    def smoke_test(self, image_ref: str, runtime_kind: str, *, timeout_seconds: int = 120) -> tuple[bool, str]:
        if not self.smoke_ok:
            return False, self.smoke_detail or "simulated smoke failure"
        return True, ""

    def probe(self) -> RuntimeCapability:
        return RuntimeCapability(True) if self.available else RuntimeCapability(False, "unavailable")

    def capacity(self) -> tuple[int, int]:
        return 8, 16 * 1024 * 1024 * 1024

    def observe(self) -> dict:
        return {
            "host_cpu_count": 8,
            "host_memory_bytes": 16 * 1024 * 1024 * 1024,
            "observed_memory_bytes": 0,
            "observed_cpu_percent": 0.0,
            "active_containers": 0,
        }

    def create(self, spec: RuntimeCreateSpec) -> RuntimeHandle:
        self.created.append(spec)
        container_id = f"ctr-{spec.sandbox_id}"
        self.managed[spec.sandbox_id] = "created"
        return RuntimeHandle(spec.sandbox_id, container_id)

    def resume(self, sandbox_id: str, container_id: str | None) -> RuntimeHandle:
        self.resumed.append(sandbox_id)
        self.managed[sandbox_id] = "running"
        return RuntimeHandle(sandbox_id, container_id or f"ctr-{sandbox_id}")

    def stop(self, handle: RuntimeHandle) -> None:
        self.stopped.append(handle.sandbox_id)
        self.managed[handle.sandbox_id] = "stopped"

    def delete(self, handle: RuntimeHandle) -> None:
        self.deleted.append(handle.sandbox_id)
        self.managed.pop(handle.sandbox_id, None)
        for key in [k for k in self.files if k[0] == handle.sandbox_id]:
            del self.files[key]

    def list_managed(self, deployment_id: str):
        return [(sid, "") for sid in self.managed if sid.startswith("sb_")]

    def write_file(self, handle: RuntimeHandle, path: str, data: bytes, *, mode: int = 0o644) -> None:
        if self.quota_exceeded:
            from sandboxd.runtime.docker import DockerWorkspaceQuotaExceeded

            raise DockerWorkspaceQuotaExceeded("quota exceeded")
        self.files[(handle.sandbox_id, path)] = data

    def delete_file(self, handle: RuntimeHandle, path: str) -> None:
        self.files.pop((handle.sandbox_id, path), None)

    def read_file(self, handle: RuntimeHandle, path: str, limit_bytes: int) -> bytes:
        data = self.files.get((handle.sandbox_id, path))
        if data is None:
            raise RuntimeError("file not found")
        return data[:limit_bytes]

    def list_files(self, handle: RuntimeHandle, prefix: str, limit: int, cursor: str | None):
        entries = []
        offset = int(cursor) if cursor else 0
        count = 0
        for (sid, path), data in sorted(self.files.items()):
            if sid != handle.sandbox_id:
                continue
            if prefix and not path.startswith(prefix):
                continue
            count += 1
            if count <= offset:
                continue
            if len(entries) >= limit:
                break
            entries.append(RuntimeFileEntry(path=path, size_bytes=len(data)))
        return entries, str(offset + len(entries))

    def workspace_usage(self, handle: RuntimeHandle) -> dict[str, int]:
        if self.usage is not None:
            return self.usage
        total = sum(len(d) for (sid, _), d in self.files.items() if sid == handle.sandbox_id)
        files = sum(1 for (sid, _) in self.files if sid == handle.sandbox_id)
        return {"bytes": total, "files": files, "dirs": 3}

    def exec_fixed(self, handle, argv, *, execution_id: str, timeout_seconds, output_limit):
        return self._exec(handle, argv)

    def exec_agent(self, handle, argv, *, execution_id: str, cwd, timeout_seconds, output_limit):
        return self._exec(handle, argv)

    def cancel_exec(self, handle: RuntimeHandle, execution_id: str) -> bool:
        self.cancelled_execs.append(execution_id)
        return True

    def _exec(self, handle, argv):
        self.exec_calls.append((handle.sandbox_id, tuple(argv)))
        if self.fail_next_exec:
            self.fail_next_exec = False
            return RuntimeExecResult(exit_code=1, stdout=b"", stderr=b"boom", timed_out=False, truncated=False, latency_ms=1)
        if self.slow_exec:
            time.sleep(0.05)
        if argv and argv[0] == "python" and "--timeout" in argv:
            return RuntimeExecResult(exit_code=-1, stdout=b"", stderr=b"", timed_out=True, truncated=False, latency_ms=1)
        joined = " ".join(argv)
        if joined.startswith("echo-ok"):
            return RuntimeExecResult(exit_code=0, stdout=b"ok\n", stderr=b"", timed_out=False, truncated=False, latency_ms=1)
        if argv == ("python", "-c", "usage") or (argv and argv[-1] == "usage"):
            payload = json.dumps({"bytes": 0, "files": 0, "dirs": 3}).encode()
            return RuntimeExecResult(exit_code=0, stdout=payload, stderr=b"", timed_out=False, truncated=False, latency_ms=1)
        return RuntimeExecResult(exit_code=0, stdout=b"done", stderr=b"", timed_out=False, truncated=False, latency_ms=1)
