"""Global Docker sandbox bootstrap job (build + pin + smoke + persist)."""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.file_lock import InterProcessFileLock
from app.providers.remote.sandbox import (
    DockerSandboxBackend,
    image_ref_is_pinned,
    sandbox_seccomp_security_options,
    sandbox_shm_size,
    CODE_RUNTIME_KIND,
    BROWSER_RUNTIME_KIND,
)
from app.services.sandbox_runtime import (
    load_runtime_config,
    resolve_sandbox_image,
    resolve_sandbox_image_for_runtime,
    runtime_config_path,
    save_runtime_config,
)

DEFAULT_TAG = "learngraph-sandbox:local"
SMOKE_CONTAINER_PREFIX = "learngraph-sandbox-smoke-"


@dataclass
class BootstrapJob:
    id: str
    phase: str = "queued"
    progress_percent: int = 0
    message: str = "排队中"
    status: str = "running"  # running | succeeded | failed
    image_digest: str | None = None
    browser_image_digest: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    log_lines: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    actor_id: str | None = None

    def append_log(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        self.log_lines.append(text[:500])
        if len(self.log_lines) > 80:
            self.log_lines = self.log_lines[-80:]

    def to_public(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "phase": self.phase,
            "progress_percent": self.progress_percent,
            "message": self.message,
            "status": self.status,
            "image_digest": self.image_digest,
            "browser_image_digest": self.browser_image_digest,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "log_tail": self.log_lines[-20:],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class SandboxBootstrapService:
    """Deployment-wide single-flight bootstrap."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._job: BootstrapJob | None = None
        self._thread: threading.Thread | None = None

    def status(self, settings: Settings) -> dict[str, Any]:
        docker_reachable, docker_detail = self._probe_docker()
        docker_installed = docker_reachable or shutil.which("docker") is not None
        image = resolve_sandbox_image(settings)
        image_ready = False
        browser_image_ready = False
        image_detail: str | None = None
        if image and image_ref_is_pinned(image):
            if docker_reachable:
                try:
                    client = self._docker_client()
                    try:
                        client.images.get(image)
                        image_ready = True
                        runtime_config = load_runtime_config(settings)
                        if runtime_config and runtime_config.browser_image_digest:
                            try:
                                client.images.get(runtime_config.browser_image_digest)
                                browser_image_ready = True
                            except Exception:
                                browser_image_ready = False
                    except Exception:
                        image_detail = "已配置 digest，但 Docker 中尚未找到该镜像"
                    finally:
                        client.close()
                except Exception as exc:
                    image_detail = str(exc)[:200]
            else:
                image_detail = "镜像 digest 已记录，但 Docker 当前不可达"
        elif image:
            image_detail = "配置的镜像不是不可变 sha256 digest"
        else:
            image_detail = "尚未初始化沙箱运行环境"

        with self._lock:
            job = self._job
            active = job.to_public() if job and job.status == "running" else None
            last_failed = (
                job.to_public()
                if job and job.status == "failed"
                else None
            )

        remediation: list[str] = []
        can_initialize = settings.sandbox_enabled and docker_reachable
        if not settings.sandbox_enabled:
            remediation.append("部署已关闭沙箱（LEARNGRAPH_SANDBOX_ENABLED=false）")
            can_initialize = False
        elif not docker_reachable:
            remediation.extend(
                [
                    "安装并启动 Docker Desktop（Windows/macOS）或 Docker Engine（Linux）",
                    "确认当前用户可访问 Docker（Windows 上通常需 Docker Desktop 处于 Running）",
                    docker_detail or "Docker Engine 不可达",
                ]
            )
            can_initialize = False
        elif image_ready:
            remediation.append("沙箱运行环境已就绪，可直接在会话中使用 Agent 文件与代码能力")
            can_initialize = True  # allow rebuild
        else:
            remediation.extend(
                [
                    "点击「初始化沙箱」构建并登记本地 Runner 镜像",
                    "首次构建需要从镜像仓库拉取基础层，请保持网络畅通",
                ]
            )
            if image_detail:
                remediation.append(image_detail)

        return {
            "docker_installed": docker_installed,
            "docker_reachable": docker_reachable,
            "docker_detail": docker_detail,
            "sandbox_enabled": settings.sandbox_enabled,
            "image_ready": image_ready,
            "image_digest": image if image_ref_is_pinned(image or "") else None,
            "browser_image_ready": browser_image_ready,
            "browser_image_digest": (
                load_runtime_config(settings).browser_image_digest
                if load_runtime_config(settings)
                else None
            ),
            "image_source": (
                "environment"
                if (settings.sandbox_image or "").strip()
                else ("runtime_config" if load_runtime_config(settings) else None)
            ),
            "phase": active["phase"] if active else ("ready" if image_ready else "idle"),
            "progress_percent": active["progress_percent"] if active else (100 if image_ready else 0),
            "message": (
                active["message"]
                if active
                else ("沙箱运行环境已就绪" if image_ready else "尚未初始化沙箱运行环境")
            ),
            # While a job is running, the UI should join progress rather than start another.
            "can_initialize": bool(can_initialize and active is None),
            "active_job": active,
            "last_failed_job": last_failed,
            "remediation_steps": remediation,
        }

    def start(self, settings: Settings, *, actor_id: str) -> dict[str, Any]:
        if not settings.sandbox_enabled:
            return {
                "accepted": False,
                "error_code": "sandbox_disabled",
                "error_message": "Sandbox execution is disabled by deployment configuration",
                "job": None,
                "status": self.status(settings),
            }
        docker_reachable, docker_detail = self._probe_docker()
        if not docker_reachable:
            return {
                "accepted": False,
                "error_code": "docker_unavailable",
                "error_message": docker_detail or "Docker Engine is unavailable",
                "job": None,
                "status": self.status(settings),
            }

        with self._lock:
            if self._job is not None and self._job.status == "running":
                return {
                    "accepted": True,
                    "joined_existing": True,
                    "job": self._job.to_public(),
                    "status": self.status(settings),
                }
            job = BootstrapJob(id=str(uuid.uuid4()), actor_id=actor_id)
            self._job = job
            thread = threading.Thread(
                target=self._run_job,
                args=(job, settings),
                name=f"sandbox-bootstrap-{job.id[:8]}",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return {
                "accepted": True,
                "joined_existing": False,
                "job": job.to_public(),
                "status": self.status(settings),
            }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            if self._job is None or self._job.id != job_id:
                return None
            return self._job.to_public()

    def _set_phase(self, job: BootstrapJob, phase: str, percent: int, message: str) -> None:
        with self._lock:
            job.phase = phase
            job.progress_percent = max(0, min(100, percent))
            job.message = message
            job.append_log(f"[{phase}] {message}")

    def _fail(self, job: BootstrapJob, code: str, message: str) -> None:
        with self._lock:
            job.status = "failed"
            job.phase = "failed"
            job.error_code = code
            job.error_message = message
            job.message = message
            job.finished_at = time.time()
            job.append_log(f"[failed] {code}: {message}")

    def _succeed(self, job: BootstrapJob, digest: str, browser_digest: str) -> None:
        with self._lock:
            job.status = "succeeded"
            job.phase = "ready"
            job.progress_percent = 100
            job.image_digest = digest
            job.browser_image_digest = browser_digest
            job.message = "沙箱运行环境已就绪"
            job.finished_at = time.time()
            job.append_log(f"[ready] {digest}")

    def _run_job(self, job: BootstrapJob, settings: Settings) -> None:
        lock_path = runtime_config_path(settings).with_suffix(".bootstrap.lock")
        with InterProcessFileLock(lock_path):
            # A job from another worker may have finished while this worker was
            # waiting. Join its published pair of digests instead of rebuilding.
            published = load_runtime_config(settings)
            if published and published.browser_image_digest and published.built_at:
                try:
                    published_at = datetime.fromisoformat(published.built_at).timestamp()
                except ValueError:
                    published_at = 0
                if published_at >= job.started_at:
                    job.append_log("[joined] deployment bootstrap completed by another worker")
                    self._succeed(
                        job,
                        published.image_digest,
                        published.browser_image_digest,
                    )
                    return
            self._run_job_locked(job, settings)

    def _run_job_locked(self, job: BootstrapJob, settings: Settings) -> None:
        try:
            self._set_phase(job, "detect_docker", 10, "正在检测 Docker Engine…")
            ok, detail = self._probe_docker()
            if not ok:
                self._fail(job, "docker_unavailable", detail or "Docker Engine is unavailable")
                return

            sandbox_root = self._sandbox_root()
            dockerfile = sandbox_root / "Dockerfile"
            if not dockerfile.is_file():
                self._fail(
                    job,
                    "dockerfile_missing",
                    "Sandbox Dockerfile is missing from the packaged backend sandbox directory",
                )
                return

            self._set_phase(
                job,
                "build_runner",
                40,
                "正在构建统一沙箱 Runner 镜像（含 Chromium/ffmpeg/前端工具链，可能需要几分钟）…",
            )
            buildargs: dict[str, str] = {}
            if (settings.sandbox_build_pip_index_url or "").strip():
                buildargs["PIP_INDEX_URL"] = settings.sandbox_build_pip_index_url.strip()
            if (settings.sandbox_build_npm_registry or "").strip():
                buildargs["NPM_REGISTRY"] = settings.sandbox_build_npm_registry.strip()
            client = self._docker_client()
            try:
                image, build_log = client.images.build(
                    path=str(sandbox_root),
                    tag=DEFAULT_TAG,
                    buildargs=buildargs or None,
                    rm=True,
                    forcerm=True,
                )
                for chunk in build_log:
                    if isinstance(chunk, dict):
                        stream = chunk.get("stream")
                        if isinstance(stream, str) and stream.strip():
                            job.append_log(stream.strip())
                        err = chunk.get("error")
                        if isinstance(err, str) and err.strip():
                            self._fail(job, "build_failed", err.strip())
                            return
            except Exception as exc:
                self._fail(job, "build_failed", f"Docker build failed: {exc}")
                return
            finally:
                try:
                    client.close()
                except Exception:
                    pass

            self._set_phase(job, "pin_digest", 70, "正在固定镜像 digest…")
            digest = self._image_digest(DEFAULT_TAG)
            if not digest:
                self._fail(job, "digest_missing", "Docker did not return an immutable image id")
                return
            job.image_digest = digest

            self._set_phase(
                job, "smoke_test", 85, "正在做 Python / Node / ffmpeg / Browser 冒烟检查…"
            )
            smoke_error = self._smoke_test(digest, settings)
            if smoke_error:
                self._fail(job, "smoke_failed", smoke_error)
                return
            # Publish the digest only after smoke passes.  browser_image_digest
            # mirrors the unified digest for configuration compatibility.
            self._set_phase(job, "persist_runtime", 97, "正在保存运行时配置…")
            try:
                save_runtime_config(
                    settings,
                    image_digest=digest,
                    source="bootstrap_build",
                    builder_user_id=job.actor_id,
                    tag=DEFAULT_TAG,
                    browser_image_digest=digest,
                )
            except Exception as exc:
                self._fail(job, "persist_failed", f"Failed to persist runtime config: {exc}")
                return

            self._succeed(job, digest, digest)
        except Exception as exc:  # pragma: no cover - defensive
            self._fail(job, "bootstrap_internal_error", str(exc)[:300])

    def _sandbox_root(self) -> Path:
        # backend/app/services/this_file → backend/sandbox
        return Path(__file__).resolve().parents[2] / "sandbox"

    def _probe_docker(self) -> tuple[bool, str | None]:
        try:
            client = self._docker_client()
            try:
                client.ping()
                return True, None
            finally:
                client.close()
        except Exception as exc:
            return False, str(exc)[:300]

    @staticmethod
    def _docker_client():
        try:
            import docker
        except ImportError as exc:
            raise RuntimeError(
                "Docker SDK is not installed; install the backend docker dependency"
            ) from exc
        return docker.from_env()

    def _image_digest(self, ref: str) -> str | None:
        client = self._docker_client()
        try:
            image = client.images.get(ref)
            image_id = str(image.id or "")
            if image_id.startswith("sha256:") and image_ref_is_pinned(image_id):
                return image_id
            return None
        except Exception:
            return None
        finally:
            client.close()

    def _smoke_test(self, image_digest: str, settings: Settings) -> str | None:
        """Exercise the unified image under both code-offline and browser-offline hardening.

        Creates two short-lived containers, each with its own runtime profile
        (seccomp + /dev/shm), and requires both to pass before the image is
        published.  The container options mirror ``DockerSandboxBackend.create``.
        """

        code_error = self._smoke_container(
            image_digest,
            settings,
            runtime_kind=CODE_RUNTIME_KIND,
            argv=[
                ["python", "--version"],
                ["node", "--version"],
                ["ffmpeg", "-version"],
                [
                    "python",
                    "-c",
                    "import mammoth, pypdf, openpyxl, PIL, pydub, learngraph_tasks",
                ],
                [
                    "node",
                    "-e",
                    "Promise.all(['vite','vue','react','react-dom',"
                    "'@vitejs/plugin-vue','@vitejs/plugin-react','vite-plugin-singlefile']"
                    ".map((m) => import(m)))"
                    ".then(() => process.exit(0))"
                    ".catch((e) => { console.error(e); process.exit(1); })",
                ],
            ],
            label_prefix="code",
        )
        if code_error:
            return code_error

        browser_error = self._smoke_container(
            image_digest,
            settings,
            runtime_kind=BROWSER_RUNTIME_KIND,
            argv=[
                ["python", "--version"],
                ["node", "-e",
                 "require('playwright-core'); "
                 "Promise.all(['vite','vue','react','react-dom',"
                 "'@vitejs/plugin-vue','@vitejs/plugin-react','vite-plugin-singlefile']"
                 ".map((m) => import(m)))"
                 ".then(() => process.exit(0))"
                 ".catch((e) => { console.error(e); process.exit(1); })",
                 ],
                ["node", "/opt/learngraph/browser-smoke.js"],
            ],
            label_prefix="browser",
        )
        if browser_error:
            return browser_error

        return None

    def _smoke_container(
        self,
        image_digest: str,
        settings: Settings,
        runtime_kind: str,
        argv: list[list[str]],
        label_prefix: str,
    ) -> str | None:
        """Run a set of checks inside one short-lived container with the given profile."""

        client = self._docker_client()
        name = f"{SMOKE_CONTAINER_PREFIX}{uuid.uuid4().hex[:12]}"
        container = None
        try:
            container = client.containers.create(
                image_digest,
                command=["sleep", "120"],
                name=name,
                network_mode="none",
                read_only=True,
                user="65532:65532",
                cap_drop=["ALL"],
                security_opt=sandbox_seccomp_security_options(runtime_kind),
                mem_limit=settings.sandbox_memory_bytes,
                memswap_limit=settings.sandbox_memory_swap_bytes,
                pids_limit=settings.sandbox_pids_max,
                shm_size=sandbox_shm_size(runtime_kind),
                tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=67108864,mode=1777"},
                labels={"com.learngraph.sandbox": "smoke"},
            )
            container.start()
            for argv_item in argv:
                label = f"{label_prefix}:{argv_item[0]}"
                result = container.exec_run(
                    argv_item,
                    user="65532:65532",
                    environment={"HOME": "/tmp"},
                )
                if int(result.exit_code) != 0:
                    out = (result.output or b"").decode("utf-8", errors="replace")[:500]
                    return f"Smoke check failed for {label}: {out or 'non-zero exit'}"
            return None
        except Exception as exc:
            return f"Smoke test ({label_prefix}) could not run: {exc}"
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            try:
                client.close()
            except Exception:
                pass


_bootstrap_service: SandboxBootstrapService | None = None
_bootstrap_lock = threading.Lock()


def get_bootstrap_service() -> SandboxBootstrapService:
    global _bootstrap_service
    with _bootstrap_lock:
        if _bootstrap_service is None:
            _bootstrap_service = SandboxBootstrapService()
        return _bootstrap_service


def backend_for_settings(
    settings: Settings, runtime_kind: str = "python-node"
) -> DockerSandboxBackend:
    return DockerSandboxBackend(
        enabled=settings.sandbox_enabled,
        image_ref=resolve_sandbox_image_for_runtime(settings, runtime_kind),
        runtime_kind=runtime_kind,
    )
