"""Global Docker sandbox bootstrap job (build + pin + smoke + persist)."""

from __future__ import annotations

import math
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

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
    effective_bootstrap_source,
    effective_member_bootstrap_allowed,
    load_bootstrap_policy,
    load_runtime_config,
    resolve_sandbox_image,
    resolve_sandbox_image_for_runtime,
    runtime_config_path,
    save_runtime_config,
)

DEFAULT_TAG = "learngraph-sandbox:local"
SMOKE_CONTAINER_PREFIX = "learngraph-sandbox-smoke-"

# Display ceilings per phase: the real docker signals already cover the phase
# range (e.g. build_runner 40→68), so the lazy smoothing only bridges gaps and
# never overshoots the phase's true end. Unknown phases cap at 90.
_PHASE_PERCENT_CAP = {
    "queued": 5,
    "detect_docker": 15,
    "pull_runner": 68,
    "build_runner": 68,
    "resolve_digest": 72,
    "pin_digest": 72,
    "smoke_test": 95,
    "persist_runtime": 98,
}
# Slowest guaranteed motion (percent per second): the bar never freezes.
_BOOTSTRAP_MIN_CREEP = 0.08
# Extra motion at the very start of a phase (decays with phase age).
_BOOTSTRAP_EARLY_CREEP = 0.55
# Time constant (seconds) for the early-phase speed boost.
_BOOTSTRAP_CREEP_DECAY_S = 70.0
# Each freshly appended log line since the last read pushes the percent this much.
_BOOTSTRAP_LOG_BOOST_PER_LINE = 0.35
# Per-read cap so a log burst cannot blow past the phase ceiling.
_BOOTSTRAP_MAX_LOG_BOOST = 6.0
_URL_CREDENTIALS_RE = re.compile(r"(https?://)([^/@\s]+)@", re.IGNORECASE)
_URL_QUERY_OR_FRAGMENT_RE = re.compile(r"(https?://[^\s?#]+)[?#][^\s]*", re.IGNORECASE)
_TOKEN_ASSIGNMENT_RE = re.compile(
    r"\b(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)


@dataclass
class BootstrapJob:
    id: str
    phase: str = "queued"
    progress_percent: int = 0
    message: str = "排队中"
    detail: str | None = None
    status: str = "running"  # running | succeeded | failed
    mode: str = "auto"  # auto | prebuilt | build
    image_digest: str | None = None
    browser_image_digest: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    log_lines: list[str] = field(default_factory=list)
    # Monotonically increasing count of appended log lines. The frontend uses
    # deltas to drive the progress bar from the real log stream even when the
    # percent itself stalls inside a long Docker step.
    log_seq: int = 0
    # Lazy progress smoothing state: percent is bumped on every read while the
    # job runs, so the value is real (persisted on the job), monotonic and
    # survives page refreshes — unlike a frontend-only simulation.
    phase_started_at: float | None = None
    last_advance_at: float = field(default_factory=time.time)
    last_advance_log_seq: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    actor_id: str | None = None
    # Live docker build tracking. Written only by the bootstrap worker thread
    # and consumed by ``_refresh_build_progress`` to drive the realistic
    # percent/detail the UI polls via /sandbox/bootstrap/status; never
    # serialized directly.
    context_current: int = 0
    context_total: int = 0
    download_current: int = 0
    download_total: int = 0
    download_complete: bool = False
    download_ref: str | None = None
    extract_current: int = 0
    extract_total: int = 0
    step_index: int = 0
    step_total: int = 0
    step_command: str = ""

    def append_log(self, line: str) -> None:
        text = _redact_build_detail(line.strip())
        if not text:
            return
        self.log_lines.append(text[:500])
        self.log_seq += 1
        if len(self.log_lines) > 80:
            self.log_lines = self.log_lines[-80:]

    def advance_lazy_progress(self) -> None:
        """Persistently smooth the running percent between real docker signals.

        Called on every read of the job while it is running. Applies a time
        creep that is fast right after a phase starts and decays (but never
        reaches zero), plus a boost proportional to freshly appended log
        lines. The result is written back onto the job, so the value is real,
        monotonic and identical after a page refresh.
        """
        if self.status != "running":
            return
        now = time.time()
        elapsed = now - self.last_advance_at
        if elapsed <= 0:
            return
        cap = _PHASE_PERCENT_CAP.get(self.phase, 90)
        if self.progress_percent >= cap:
            self.last_advance_at = now
            self.last_advance_log_seq = self.log_seq
            return
        phase_started = self.phase_started_at or self.started_at
        phase_age = max(0.0, now - phase_started)
        creep = _BOOTSTRAP_MIN_CREEP + (
            _BOOTSTRAP_EARLY_CREEP - _BOOTSTRAP_MIN_CREEP
        ) * math.exp(-phase_age / _BOOTSTRAP_CREEP_DECAY_S)
        log_delta = max(0, self.log_seq - self.last_advance_log_seq)
        self.last_advance_log_seq = self.log_seq
        log_boost = min(
            _BOOTSTRAP_MAX_LOG_BOOST, log_delta * _BOOTSTRAP_LOG_BOOST_PER_LINE
        )
        boosted = self.progress_percent + creep * elapsed + log_boost
        # max() guards against a concurrent real signal jump from the worker.
        self.progress_percent = max(self.progress_percent, min(cap, boosted))
        self.last_advance_at = now

    def to_public(self) -> dict[str, Any]:
        self.advance_lazy_progress()
        return {
            "job_id": self.id,
            "phase": self.phase,
            "progress_percent": self.progress_percent,
            "message": self.message,
            "detail": self.detail,
            "status": self.status,
            "mode": self.mode,
            "image_digest": self.image_digest,
            "browser_image_digest": self.browser_image_digest,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "log_tail": self.log_lines[-20:],
            "log_seq": self.log_seq,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _redact_build_detail(value: str) -> str:
    """Keep build diagnostics useful without disclosing registry credentials."""

    value = _URL_CREDENTIALS_RE.sub(r"\1<redacted>@", value)
    value = _URL_QUERY_OR_FRAGMENT_RE.sub(r"\1<redacted>", value)
    return _TOKEN_ASSIGNMENT_RE.sub(r"\1=<redacted>", value)


_KB = 1024
_MB = 1024 * 1024
_BYTES_PER_UNIT = {
    "b": 1,
    "kb": 1000,
    "mb": 1000 * 1000,
    "gb": 1000 * 1000 * 1000,
    "kib": _KB,
    "mib": _MB,
    "gib": _MB * 1024,
}
_BUILDKIT_STEP_RE = re.compile(
    r"^#\d+\s+\[(\d+)/(\d+)\]\s*"
    r"(FROM|RUN|COPY|ADD|ENV|ARG|WORKDIR|CMD|ENTRYPOINT|EXPOSE|USER|LABEL|VOLUME|SHELL|HEALTHCHECK|STOPSIGNAL)\b\s*(.*)$",
    re.IGNORECASE,
)
_BUILDKIT_PROGRESS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(kB|MB|GB|KiB|MiB|GiB)\s*/\s*"
    r"(\d+(?:\.\d+)?)\s*(kB|MB|GB|KiB|MiB|GiB)",
    re.IGNORECASE,
)
_BUILDKIT_RESOLVE_RE = re.compile(r"^#\d+\s+resolve\s+(\S+)", re.IGNORECASE)
_CLASSIC_STEP_RE = re.compile(r"^Step\s+(\d+)/(\d+)\s*:?\s*(.*)$", re.IGNORECASE)
_PREBUILT_IMAGE_REF_RE = re.compile(
    r"^(?:[a-z0-9]+(?:[._-][a-z0-9]+)*(?::\d+)?/)?"
    r"[a-z0-9]+(?:[._/-][a-z0-9]+)*(?:(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})|@sha256:[0-9a-f]{64})?$"
)


def _prebuilt_image_ref(value: str | None) -> str | None:
    """Return a safe pull reference; runtime refs are pinned separately."""

    raw = value or ""
    if any(character.isspace() or ord(character) < 32 for character in raw):
        raise ValueError("Prebuilt sandbox image reference contains invalid characters")
    ref = raw.strip()
    if not ref:
        return None
    if not _PREBUILT_IMAGE_REF_RE.fullmatch(ref):
        raise ValueError("Prebuilt sandbox image reference is invalid")
    return ref


def _repository_for_ref(ref: str) -> str:
    """Drop a tag/digest while retaining the registry-qualified repository."""

    without_digest = ref.split("@", 1)[0]
    slash = without_digest.rfind("/")
    colon = without_digest.rfind(":")
    return without_digest[:colon] if colon > slash else without_digest


def _matching_repo_digest(image, configured_ref: str) -> str | None:
    """Select the pulled registry digest for the configured repository.

    Docker image IDs are local config IDs and cannot be pulled on another
    device.  Only a RepoDigest is safe to persist as a portable runtime ref.
    """

    expected = _repository_for_ref(configured_ref)
    digests = image.attrs.get("RepoDigests") or []
    if not isinstance(digests, list):
        return None
    for digest in digests:
        if isinstance(digest, str) and digest.startswith(expected + "@sha256:"):
            return digest if image_ref_is_pinned(digest) else None
    return None


def _friendly_image_ref(ref: str) -> str:
    ref = (ref or "").strip()
    if ref.startswith("docker.io/"):
        ref = ref[len("docker.io/") :]
    ref = ref.split("@sha256:")[0]
    return ref or "镜像层"


def _clean_stream_line(value: str) -> str:
    """Strip ANSI CSI codes and \r-based in-place rewrites, keep the newest segment.

    BuildKit rewrites progress with \r and embeds ANSI color/clear codes, so
    the raw stream is not human-readable; this yields the last visible update
    of each line.
    """
    value = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", value)
    parts = [part.strip() for part in value.split("\r")]
    parts = [part for part in parts if part]
    return parts[-1] if parts else ""


def _parse_byte_progress(entry: dict[str, Any]) -> tuple[int, int] | None:
    detail = entry.get("progressDetail")
    if not isinstance(detail, dict):
        return None
    current, total = detail.get("current"), detail.get("total")
    if isinstance(current, int) and isinstance(total, int) and total > 0:
        return current, total
    return None


def _size_to_bytes(value: str, unit: str) -> int:
    return int(float(value) * _BYTES_PER_UNIT[unit.lower()])


def _track_build_entry(job: BootstrapJob, entry: dict[str, Any]) -> None:
    """Fold one docker build log entry into the job's live progress state."""

    status = entry.get("status")
    if isinstance(status, str) and status.strip():
        _track_classic_entry(job, status, entry)
        return
    stream = entry.get("stream")
    if isinstance(stream, str):
        _track_buildkit_line(job, stream)


def _track_classic_entry(
    job: BootstrapJob, status: str, entry: dict[str, Any]
) -> None:
    step_match = _CLASSIC_STEP_RE.match(status)
    if step_match:
        job.step_index = int(step_match.group(1))
        job.step_total = int(step_match.group(2))
        job.step_command = step_match.group(3).strip()[:80]
        # A step implies the base layers are pulled/extracted; clear the
        # download trackers so later steps aren't masked by stale pulls.
        job.context_current = job.context_total = 0
        job.download_current = job.download_total = 0
        job.download_complete = False
        job.extract_current = job.extract_total = 0
        _refresh_build_progress(job)
        return
    if "Sending build context" in status:
        progress = _parse_byte_progress(entry)
        if progress:
            job.context_current, job.context_total = progress
            _refresh_build_progress(job)
        return
    if "Pulling from" in status:
        job.download_ref = status.split("Pulling from", 1)[1].strip()
        _refresh_build_progress(job)
        return
    if status == "Downloading":
        progress = _parse_byte_progress(entry)
        if progress:
            job.download_current, job.download_total = progress
            job.download_complete = job.download_current >= job.download_total
            if not job.download_ref:
                layer_id = entry.get("id")
                if isinstance(layer_id, str) and layer_id:
                    job.download_ref = layer_id
            _refresh_build_progress(job)
        return
    if status == "Download complete":
        job.download_complete = True
        _refresh_build_progress(job)
        return
    if status == "Extracting":
        job.download_complete = True  # extraction implies downloads finished
        progress = _parse_byte_progress(entry)
        if progress:
            job.extract_current, job.extract_total = progress
        elif not job.extract_total:
            job.extract_total = 1
        _refresh_build_progress(job)
        return
    if status in ("Waiting", "Pull complete", "Verifying Checksum", "Already exists"):
        _refresh_build_progress(job)
        return


def _track_buildkit_line(job: BootstrapJob, stream: str) -> None:
    line = _clean_stream_line(stream)
    if not line:
        return
    step_match = _BUILDKIT_STEP_RE.match(line)
    if step_match:
        job.step_index = int(step_match.group(1))
        job.step_total = int(step_match.group(2))
        job.step_command = f"{step_match.group(3).upper()} {step_match.group(4)}".strip()[:80]
        # A step implies the base layers are pulled/extracted; clear the
        # download trackers so later steps aren't masked by stale pulls.
        job.context_current = job.context_total = 0
        job.download_current = job.download_total = 0
        job.download_complete = False
        job.extract_current = job.extract_total = 0
        _refresh_build_progress(job)
        return
    resolve_match = _BUILDKIT_RESOLVE_RE.match(line)
    if resolve_match:
        job.download_ref = _friendly_image_ref(resolve_match.group(1))
        return
    progress_match = _BUILDKIT_PROGRESS_RE.search(line)
    if progress_match:
        current = _size_to_bytes(progress_match.group(1), progress_match.group(2))
        total = _size_to_bytes(progress_match.group(3), progress_match.group(4))
        if total > 0:
            if "extracting" in line.lower() or "unpacking" in line.lower():
                job.download_complete = True  # extraction implies downloads finished
                job.extract_current, job.extract_total = current, total
            else:
                job.download_current, job.download_total = current, total
                job.download_complete = current >= total
            _refresh_build_progress(job)
        return
    if "DONE" in line and job.download_total:
        job.download_complete = True
        _refresh_build_progress(job)


def _refresh_build_progress(job: BootstrapJob) -> None:
    """Recompute the live build percent (40→68) and a human-readable detail.

    ``progress_percent`` only moves forward within the build phase, so the bar
    never regresses while the daemon switches between downloading layers,
    extracting them and executing steps.
    """
    mb = _MB
    if job.context_total:
        frac = max(0.0, min(1.0, job.context_current / job.context_total))
        detail = (
            f"正在上传构建上下文 · "
            f"{job.context_current / mb:.1f} MB / {job.context_total / mb:.1f} MB"
        )
        percent = 40 + round(28 * 0.15 * frac)
    elif job.download_total and not job.download_complete:
        frac = max(0.0, min(1.0, job.download_current / job.download_total))
        ref = _friendly_image_ref(job.download_ref or "镜像层")
        detail = (
            f"正在下载镜像 {ref} · "
            f"{job.download_current / mb:.1f} MB / {job.download_total / mb:.1f} MB"
        )
        percent = 40 + round(28 * (0.15 + 0.25 * frac))
    elif job.extract_total:
        detail = (
            f"正在解压镜像层 · "
            f"{job.extract_current / mb:.1f} MB / {job.extract_total / mb:.1f} MB"
        )
        percent = 40 + round(28 * 0.45)
    elif job.step_total:
        index = max(1, job.step_index)
        command = job.step_command or "执行构建指令"
        detail = f"正在构建 · 步骤 {index}/{job.step_total}：{command}"
        percent = 40 + round(28 * (0.5 + 0.5 * ((index - 1) / job.step_total)))
    else:
        detail = "正在构建镜像…"
        percent = 40
    job.detail = detail
    job.progress_percent = min(68, max(40, job.progress_percent, percent))


def _append_build_entry(job: BootstrapJob, entry: Any) -> str | None:
    """Append one docker-py/BuildKit entry and return its failure detail."""

    if not isinstance(entry, dict):
        if isinstance(entry, str) and entry.strip():
            job.append_log(_clean_stream_line(entry))
        return None
    stream = entry.get("stream")
    if isinstance(stream, str) and stream.strip():
        job.append_log(_clean_stream_line(stream))
    status = entry.get("status")
    if isinstance(status, str) and status.strip():
        job.append_log(_clean_stream_line(status))
    _track_build_entry(job, entry)
    err = entry.get("error")
    detail = entry.get("errorDetail")
    detail_message = (
        detail.get("message")
        if isinstance(detail, dict) and isinstance(detail.get("message"), str)
        else None
    )
    failure_detail = err if isinstance(err, str) and err.strip() else detail_message
    if failure_detail:
        job.append_log(f"[docker-error] {failure_detail}")
        return failure_detail
    return None


def _append_exception_build_log(job: BootstrapJob, exc: BaseException) -> str:
    """Consume BuildError.build_log when docker-py raises after a failed build."""

    detail = _redact_build_detail(str(exc)).strip()
    build_log = getattr(exc, "build_log", None)
    if build_log is not None:
        try:
            for entry in build_log:
                failure = _append_build_entry(job, entry)
                if failure:
                    detail = failure
        except Exception as log_exc:  # diagnostic collection must not mask the build error
            job.append_log(f"[docker-log-read-error] {_redact_build_detail(str(log_exc))}")
    return detail


def _build_failure_code(detail: str) -> str:
    """Classify a build failure without guessing when evidence is weak."""

    lowered = detail.lower()
    if "parameter not set" in lowered or "unbound variable" in lowered:
        return "build_config"
    if any(
        marker in lowered
        for marker in (
            "could not fetch url",
            "temporary failure in name resolution",
            "name or service not known",
            "connection refused",
            "connection reset",
            "timed out",
            "timeout",
            "tls",
            "ssl",
            "certificate verify failed",
            "502 bad gateway",
            "503 service unavailable",
            "504 gateway timeout",
        )
    ):
        return "build_network"
    if any(
        marker in lowered
        for marker in (
            "no matching distribution",
            "no matching package",
            "could not find a version that satisfies",
            "only-binary",
            "requires a different python",
            "not a supported wheel on this platform",
        )
    ):
        return "build_no_wheel"
    if any(
        marker in lowered
        for marker in (
            "requires-python",
            "dependency conflict",
            "conflicting dependencies",
            "resolutionimpossible",
            "invalid requirement",
            "no such version",
        )
    ):
        return "build_version"
    return "build_failed"


def _build_failure_message(detail: str) -> str:
    """Summarize common package-fetch failures while retaining a redacted tail."""

    text = _redact_build_detail(detail).strip()
    lowered = text.lower()
    if "parameter not set" in lowered or "unbound variable" in lowered:
        summary = "Dockerfile build argument is unavailable in this stage"
    elif any(marker in lowered for marker in ("no matching distribution", "could not find a version")):
        summary = "Python dependency or compatible wheel is unavailable from the configured package index"
    elif any(marker in lowered for marker in ("hashes", "hash mismatch")):
        summary = "Python dependency download failed integrity verification"
    elif any(
        marker in lowered
        for marker in ("ssl", "certificate", "tls", "connection", "timed out", "temporary failure", "name resolution")
    ):
        summary = "Python dependency download could not reach the configured package index"
    else:
        summary = "Docker build failed; inspect the redacted build log tail for the failing instruction"
    return f"{summary}: {text[:300]}" if text else summary


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

        try:
            _, effective_prebuilt = effective_bootstrap_source(settings)
            prebuilt_ref = _prebuilt_image_ref(effective_prebuilt) if effective_prebuilt else None
        except ValueError:
            prebuilt_ref = None

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
            "member_bootstrap_allowed": effective_member_bootstrap_allowed(settings),
            "bootstrap_policy": load_bootstrap_policy(settings).to_dict()
            if load_bootstrap_policy(settings)
            else None,
            "prebuilt_image_configured": bool(prebuilt_ref),
            "prebuilt_image_ref": (
                _friendly_image_ref(prebuilt_ref) if prebuilt_ref else None
            ),
            "bootstrap_mode": effective_bootstrap_source(settings)[0],
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
            "detail": active["detail"] if active else None,
            # While a job is running, the UI should join progress rather than start another.
            "can_initialize": bool(can_initialize and active is None),
            "active_job": active,
            "last_failed_job": last_failed,
            "remediation_steps": remediation,
        }

    def start(
        self, settings: Settings, *, actor_id: str, mode: str = "auto"
    ) -> dict[str, Any]:
        if mode not in ("auto", "prebuilt", "build"):
            return {
                "accepted": False,
                "error_code": "invalid_bootstrap_mode",
                "error_message": f"Unsupported sandbox bootstrap mode: {mode}",
                "job": None,
                "status": self.status(settings),
            }
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
        if mode == "prebuilt":
            _, effective_prebuilt = effective_bootstrap_source(settings)
            try:
                prebuilt_ref = _prebuilt_image_ref(effective_prebuilt)
            except ValueError as exc:
                return {
                    "accepted": False,
                    "error_code": "prebuilt_image_invalid",
                    "error_message": str(exc),
                    "job": None,
                    "status": self.status(settings),
                }
            if not prebuilt_ref:
                return {
                    "accepted": False,
                    "error_code": "prebuilt_image_not_configured",
                    "error_message": (
                        "部署未配置预构建沙箱镜像"
                        "（LEARNGRAPH_SANDBOX_PREBUILT_IMAGE 与设置页镜像来源均为空），"
                        "请选择本地构建或由部署管理员在设置页配置预构建镜像。"
                    ),
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
            job = BootstrapJob(id=str(uuid.uuid4()), actor_id=actor_id, mode=mode)
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
            job.detail = None
            # Reset the lazy progress smoothing so the next phase starts with a
            # fresh, fast early creep.
            job.phase_started_at = time.time()
            job.last_advance_at = time.time()
            # Reset the docker build tracker for a fresh phase.
            job.context_current = job.context_total = 0
            job.download_current = job.download_total = 0
            job.download_complete = False
            job.download_ref = None
            job.extract_current = job.extract_total = 0
            job.step_index = job.step_total = 0
            job.step_command = ""
            job.append_log(f"[{phase}] {message}")
            job.last_advance_log_seq = job.log_seq

    def _fail(self, job: BootstrapJob, code: str, message: str) -> None:
        with self._lock:
            job.status = "failed"
            job.phase = "failed"
            job.error_code = code
            job.error_message = message
            job.message = message
            job.detail = None
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
            job.detail = None
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

    def _pull_prebuilt_image(
        self, job: BootstrapJob, settings: Settings, configured_ref: str
    ) -> str | None:
        """Pull a configured registry image, persist only its RepoDigest."""

        self._set_phase(job, "pull_runner", 15, "正在下载预构建沙箱镜像…")
        client = self._docker_client()
        try:
            # docker-py's pull() parses ``repository:tag`` itself.  Passing the
            # bare repository here would silently drop the configured tag and
            # fetch ``:latest`` instead (e.g. ``...:1.0.0`` → ``...:latest``),
            # which 404s for a tag-only registry image and would also make the
            # ``client.images.get(configured_ref)`` below fail.
            for chunk in client.api.pull(configured_ref, stream=True, decode=True):
                failure = _append_build_entry(job, chunk)
                if failure:
                    self._fail(job, "prebuilt_pull_failed", _build_failure_message(failure))
                    return None
                with self._lock:
                    if job.download_total:
                        fraction = min(1.0, job.download_current / job.download_total)
                        job.progress_percent = max(job.progress_percent, 15 + round(53 * fraction))
                        ref = _friendly_image_ref(job.download_ref or configured_ref)
                        job.detail = (
                            f"正在下载预构建镜像 {ref} · "
                            f"{job.download_current / _MB:.1f} MB / {job.download_total / _MB:.1f} MB"
                        )
                    elif job.detail is None:
                        job.detail = f"正在下载预构建镜像 {_friendly_image_ref(configured_ref)}…"
            self._set_phase(job, "resolve_digest", 70, "正在解析预构建镜像 digest…")
            image = client.images.get(configured_ref)
            digest = _matching_repo_digest(image, configured_ref)
            if not digest:
                self._fail(
                    job,
                    "prebuilt_digest_missing",
                    "Registry pull completed but Docker did not expose a matching immutable RepoDigest",
                )
                return None
            return digest
        except Exception as exc:
            detail = _redact_build_detail(str(exc))
            job.append_log(f"[docker-pull-exception] {detail}")
            self._fail(job, "prebuilt_pull_failed", _build_failure_message(detail))
            return None
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _verify_and_persist_image(
        self,
        job: BootstrapJob,
        settings: Settings,
        digest: str,
        *,
        source: str,
        tag: str,
    ) -> None:
        job.image_digest = digest
        self._set_phase(
            job, "smoke_test", 86, "正在做 Python / Node / ffmpeg / Browser 冒烟检查…"
        )

        def _bump_smoke(detail: str, percent: int) -> None:
            with self._lock:
                job.detail = detail
                job.progress_percent = max(job.progress_percent, percent)

        smoke_error = self._smoke_test(digest, settings, on_progress=_bump_smoke)
        if smoke_error:
            self._fail(job, "smoke_failed", smoke_error)
            return
        self._set_phase(job, "persist_runtime", 97, "正在保存运行时配置…")
        try:
            save_runtime_config(
                settings,
                image_digest=digest,
                source=source,
                builder_user_id=job.actor_id,
                tag=tag,
                browser_image_digest=digest,
            )
        except Exception as exc:
            self._fail(job, "persist_failed", f"Failed to persist runtime config: {exc}")
            return
        self._succeed(job, digest, digest)

    def _run_job_locked(self, job: BootstrapJob, settings: Settings) -> None:
        try:
            self._set_phase(job, "detect_docker", 10, "正在检测 Docker Engine…")
            ok, detail = self._probe_docker()
            if not ok:
                self._fail(job, "docker_unavailable", detail or "Docker Engine is unavailable")
                return

            # Resolve the prebuilt reference from the deployment source config:
            # env LEARNGRAPH_SANDBOX_PREBUILT_IMAGE wins over the page-persisted
            # reference; a forced build mode ignores it entirely.
            effective_mode, effective_prebuilt = effective_bootstrap_source(settings)
            prebuilt_candidate = (
                effective_prebuilt
                if job.mode != "build" and effective_mode != "build"
                else None
            )
            try:
                prebuilt_ref = (
                    _prebuilt_image_ref(prebuilt_candidate) if prebuilt_candidate else None
                )
            except ValueError as exc:
                self._fail(job, "prebuilt_image_invalid", str(exc))
                return
            if job.mode == "prebuilt" and not prebuilt_ref:
                self._fail(
                    job,
                    "prebuilt_image_not_configured",
                    "部署未配置预构建沙箱镜像"
                    "（LEARNGRAPH_SANDBOX_PREBUILT_IMAGE 与设置页镜像来源均为空）",
                )
                return
            if prebuilt_ref:
                digest = self._pull_prebuilt_image(job, settings, prebuilt_ref)
                if digest:
                    self._verify_and_persist_image(
                        job, settings, digest, source="prebuilt_pull", tag=prebuilt_ref
                    )
                    return
                # Auto mode promises "pull the prebuilt image when one is
                # configured, otherwise fall back to a local Docker build":
                # an unreachable/missing registry image (not pushed yet,
                # private without login, wrong tag) must not strand the
                # deployment uninitialized.  The one-click init always sends
                # auto, so a failed prebuilt pull degrades to the local build
                # even when the settings page persisted ``prebuilt`` as the
                # source mode; only an explicit request mode ``prebuilt``
                # still fails closed.
                if job.mode == "auto":
                    with self._lock:
                        job.status = "running"
                        job.phase = "pull_runner"
                        job.message = "预构建镜像不可用，正在回退本地构建…"
                        job.error_code = None
                        job.error_message = None
                        job.finished_at = None
                    job.append_log(
                        "[auto-fallback] 预构建镜像拉取失败，回退到本地构建"
                    )
                else:
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
                # Stream the build via the low-level API (``images.build``
                # buffers the whole stream before returning, which would freeze
                # progress at 40% until the image is done). Each decoded chunk
                # updates the job's percent/detail from real layer downloads
                # and steps as the daemon emits them.
                build_error_detail: str | None = None
                for chunk in client.api.build(
                    path=str(sandbox_root),
                    tag=DEFAULT_TAG,
                    buildargs=buildargs or None,
                    rm=True,
                    forcerm=True,
                    decode=True,
                ):
                    failure_detail = _append_build_entry(job, chunk)
                    if failure_detail:
                        build_error_detail = failure_detail
                if build_error_detail:
                    self._fail(
                        job,
                        _build_failure_code(build_error_detail),
                        _build_failure_message(build_error_detail),
                    )
                    return
            except Exception as exc:
                detail = _append_exception_build_log(job, exc)
                job.append_log(f"[docker-exception] {detail}")
                self._fail(job, _build_failure_code(detail), _build_failure_message(detail))
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
            self._verify_and_persist_image(
                job, settings, digest, source="bootstrap_build", tag=DEFAULT_TAG
            )
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

    def _smoke_test(
        self,
        image_digest: str,
        settings: Settings,
        on_progress: Callable[[str, int], None] | None = None,
    ) -> str | None:
        """Exercise the unified image under both code-offline and browser-offline hardening.

        Creates two short-lived containers, each with its own runtime profile
        (seccomp + /dev/shm), and requires both to pass before the image is
        published.  The container options mirror ``DockerSandboxBackend.create``.
        """

        if on_progress is not None:
            on_progress("正在冒烟检查 Python / Node / ffmpeg / 文档解析库…", 88)
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
                    "import av, bs4, docx, fitz, mammoth, markdown_it, numpy, odf, openpyxl, pandas, pdfplumber, PIL, pydub, pypdf, pptx, pyxlsb, trafilatura, xlsxwriter, learngraph_tasks",
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

        if on_progress is not None:
            on_progress("正在冒烟检查 Chromium / 前端构建工具链…", 93)
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
                pids_limit=max(settings.sandbox_pids_max, 1024),
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
                    environment={
                        "HOME": "/tmp",
                        "XDG_CONFIG_HOME": "/tmp/.config",
                        "XDG_CACHE_HOME": "/tmp/.cache",
                    },
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
        archive_bytes=settings.sandbox_agent_archive_bytes,
    )
