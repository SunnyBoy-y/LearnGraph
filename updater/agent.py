"""Run on the Docker host, outside the application container: python -m updater.

Only published stable releases from the operator-configured repository can be
installed. Compose configuration and secrets never travel through the HTTP API.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SERVICES = ("app", "preview", "egress-proxy")
TERMINAL = {"succeeded", "failed", "rolled_back", "rollback_failed"}
VERSION = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?$")
COMPAT_LABEL = "io.learngraph.update-compatibility"
DATABASE_LABEL = "io.learngraph.primary-database"
DATABASE_CAPABILITY = "sqlite-postgresql-v1"
TOPOLOGY_LABEL = "io.learngraph.app-topology"
COMBINED_TOPOLOGY = "combined-preview-v1"


def version(value: str) -> tuple[int, ...]:
    match = VERSION.fullmatch(value)
    if not match:
        raise ValueError("仅支持正式 Release 版本号，例如 v0.5 或 v1.2.3")
    return tuple(int(part) if part is not None else 0 for part in match.groups())


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: Path, value: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class UpdateError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


# Older SQLite images cannot import newer maintenance modules. These checks
# must also stay active when the container sets PYTHONOPTIMIZE.
LEGACY_SQLITE_GUARD = '''import os,json,sqlite3
from pathlib import Path
root=Path(os.environ.get('LEARNGRAPH_DATABASE_MIGRATION_STATE_DIR') or '/data/database-migration')
if (root/'active.json').exists():
    raise RuntimeError('PostgreSQL activation forbids the legacy SQLite updater')
job=json.loads((root/'job.json').read_text()) if (root/'job.json').exists() else {}
if job and job.get('state') not in ('CANCELLED','COMPLETED'):
    raise RuntimeError('Database migration is in progress')
url=os.environ.get('LEARNGRAPH_DATABASE_URL','sqlite:////data/learngraph.db')
if not url.startswith('sqlite:///'):
    raise RuntimeError('Only SQLite is supported')
src=sqlite3.connect(Path(url[10:]).resolve().as_uri()+'?mode=ro',uri=True)
try:
    fenced=src.execute("SELECT 1 FROM sqlite_master WHERE name='_lg_database_fence'").fetchone()
    row=src.execute('SELECT sealed FROM _lg_database_fence WHERE singleton=1').fetchone() if fenced else None
    if row and row[0]:
        raise RuntimeError('Sealed SQLite cannot be used as the primary database')
except BaseException:
    src.close()
    raise
'''


class Agent:
    def __init__(self, root: Path, repository: str, image: str,
                 compose_files: list[str] | None = None, timeout: int = 180, backup_timeout: int = 900):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid GitHub repository")
        if not re.fullmatch(r"[a-z0-9][a-z0-9./_-]*", image):
            raise ValueError("Image must be a registry/repository without a tag")
        self.root = root.resolve()
        self.repository, self.image, self.timeout = repository, image, timeout
        self.backup_timeout = backup_timeout
        self.state_dir = self.root / "data" / "updater"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "state.json"
        self.override = self.root / "docker-compose.update.json"
        self.compose_files = compose_files or ["docker-compose.yml", "docker-compose.updater.yml"]
        self.lock = threading.RLock()
        self.check_lock = threading.Lock()
        self.state = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {
            "auto_install": False, "releases": [], "checked_at": None,
            "etag": None, "check_error": None, "jobs": [], "failed_versions": [],
        }
        if self.state.get("repository", repository).casefold() != repository.casefold():
            raise ValueError("已有更新器状态属于其他 GitHub 仓库，请先在宿主机完成部署迁移")
        self.state["repository"] = repository
        token_file = self.state_dir / "token"
        if not token_file.exists():
            token_file.write_text(secrets.token_urlsafe(48), encoding="ascii")
        # Read-only bind mount into UID 1000 application; the directory contains
        # only this service credential. Private journal/backups are kept apart.
        token_file.chmod(0o644)
        self.token = token_file.read_text(encoding="ascii").strip()
        if len(self.token) < 32:
            raise ValueError("Updater token is too short")
        self.private = self.state_dir / "private"
        self.private.mkdir(exist_ok=True, mode=0o700)
        self.private.chmod(0o700)

    def save(self) -> None:
        atomic_json(self.state_file, self.state)

    def command_environment(self) -> dict[str, str]:
        return os.environ.copy()

    def command_failed(self, result) -> None:
        pass  # Production diagnostics must not enter public job records.

    def command(self, args: list[str], timeout: int = 60) -> str:
        environment = self.command_environment()
        version_file = self.root / "VERSION"
        if version_file.exists():
            environment["LEARNGRAPH_APP_VERSION"] = version_file.read_text(encoding="utf-8").strip()
        try:
            result = subprocess.run(["docker", *args], cwd=self.root, timeout=timeout,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UpdateError("Docker 命令不可用或超时，请检查更新器宿主机日志", 503) from exc
        if result.returncode:
            self.command_failed(result)
            # Docker diagnostics can contain environment values. Do not return
            # stderr to the browser or persist it in a public job record.
            raise UpdateError(f"Docker {args[0]} 执行失败，请在宿主机检查 Docker/镜像权限", 503)
        return result.stdout.strip()

    def compose(self, args: list[str], timeout: int = 60) -> str:
        files = [part for file in self.compose_files for part in ("-f", file)]
        if self.override.exists():
            files += ["-f", str(self.override)]
        return self.command(["compose", *files, *args], timeout)

    def services(self) -> tuple[str, ...]:
        # Discover the operator's base topology, never resurrect a service
        # merely because an old image-selection override still mentions it.
        files = [part for file in self.compose_files for part in ("-f", file)]
        declared = set(self.command(["compose", *files, "config", "--services"]).splitlines())
        if not {"app", "egress-proxy"} <= declared:
            raise UpdateError("Compose 必须声明 app 和 egress-proxy")
        return tuple(service for service in SERVICES if service in declared)

    def containers(self) -> dict[str, dict]:
        result = {}
        for service in self.services():
            ids = self.compose(["ps", "--all", "--quiet", service]).splitlines()
            if len(ids) != 1:
                raise UpdateError(f"{service} 必须恰好有一个容器；不支持多副本或尚未启动的栈")
            result[service] = json.loads(self.command(["inspect", ids[0]]))[0]
        projects = {item["Config"].get("Labels", {}).get("com.docker.compose.project") for item in result.values()}
        if len(projects) != 1 or None in projects:
            raise UpdateError("服务不属于同一 Compose 项目")
        return result

    def current(self) -> dict:
        ids = self.compose(["ps", "--all", "--quiet", "app"]).splitlines()
        if len(ids) != 1:
            raise UpdateError("app 必须恰好有一个容器")
        app = json.loads(self.command(["inspect", ids[0]]))[0]
        try:
            self.image_info(app["Image"])
        except UpdateError as exc:
            raise UpdateError("当前运行镜像已不在本地镜像库，无法保证回滚；请重新部署并保留基线镜像") from exc
        labels = app["Config"].get("Labels", {})
        return {"version": labels.get("org.opencontainers.image.version", "unknown"),
                "image": app["Image"], "compatibility": labels.get(COMPAT_LABEL)}

    def snapshot(self) -> dict:
        with self.lock:
            state = json.loads(json.dumps(self.state))
        try:
            current, error = self.current(), None
        except UpdateError as exc:
            current, error = {"version": "unknown", "image": None, "compatibility": None}, str(exc)
        releases = state["releases"]
        latest = releases[0] if releases else None
        try:
            has_update = bool(latest and version(latest["version"]) > version(current["version"]))
        except ValueError:
            has_update = False
        return {"enabled": True, "repository": self.repository, "current": current,
                "latest": latest, "has_update": has_update, "auto_install": state["auto_install"],
                "checked_at": state["checked_at"], "check_error": state["check_error"],
                "deployment_error": error, "jobs": list(reversed(state["jobs"]))}

    def check(self) -> None:
        # One conditional request at a time, including manual checks.
        with self.check_lock:
            headers = {"Accept": "application/vnd.github+json", "User-Agent": "LearnGraph-Updater",
                       "X-GitHub-Api-Version": "2022-11-28"}
            if self.state.get("etag"):
                headers["If-None-Match"] = self.state["etag"]
            if os.environ.get("LEARNGRAPH_UPDATE_GITHUB_TOKEN"):
                headers["Authorization"] = "Bearer " + os.environ["LEARNGRAPH_UPDATE_GITHUB_TOKEN"]
            try:
                request = Request(f"https://api.github.com/repos/{self.repository}/releases?per_page=100", headers=headers)
                with urlopen(request, timeout=20) as response:
                    payload = json.load(response)
                    releases = []
                    for release in payload:
                        tag = release.get("tag_name", "")
                        if release.get("draft") or release.get("prerelease") or not VERSION.fullmatch(tag):
                            continue
                        releases.append({"version": tag, "notes": (release.get("body") or "")[:20000],
                                         "url": f"https://github.com/{self.repository}/releases/tag/{tag}",
                                         "published_at": release.get("published_at")})
                    releases.sort(key=lambda item: version(item["version"]), reverse=True)
                    with self.lock:
                        self.state.update(releases=releases, etag=response.headers.get("ETag"))
            except HTTPError as exc:
                if exc.code != 304:
                    with self.lock:
                        self.state.update(check_error=f"GitHub Release 检查失败（HTTP {exc.code}）", checked_at=now())
                        self.save()
                    return
            except (URLError, OSError, ValueError, TypeError, AttributeError):
                with self.lock:
                    self.state.update(check_error="GitHub Release 检查失败，请检查网络", checked_at=now())
                    self.save()
                return
            with self.lock:
                self.state.update(check_error=None, checked_at=now())
                self.save()

    def active(self) -> bool:
        return any(job["status"] not in TERMINAL or job.get("maintenance_reserved", False) for job in self.state["jobs"])

    def set_policy(self, enabled: bool) -> dict:
        with self.lock:
            self.state["auto_install"] = enabled
            self.save()
        return {"auto_install": enabled}

    def submit(self, target: str | None = None, rollback_id: str | None = None,
               request_id: str | None = None) -> dict:
        with self.lock:
            if request_id:
                existing = next((j for j in self.state["jobs"] if j.get("request_id") == request_id), None)
                if existing:
                    return json.loads(json.dumps(existing))
            if self.active():
                raise UpdateError("已有更新任务正在运行")
            rollback = None
            if rollback_id:
                rollback = next((j for j in self.state["jobs"] if j["id"] == rollback_id and j["status"] == "succeeded"), None)
                if not rollback:
                    raise UpdateError("找不到可回退的成功部署记录", 404)
                target = rollback["previous_version"]
                # A deliberate downgrade must not be undone by the next poll.
                self.state["auto_install"] = False
            elif not any(release["version"] == target for release in self.state["releases"]):
                raise UpdateError("目标必须是已检测到的正式 GitHub Release", 422)
            job = {"id": secrets.token_hex(12), "request_id": request_id, "target_version": target,
                   "kind": "rollback" if rollback else "update", "status": "queued", "created_at": now(),
                   "message": "任务已排队", "rollback_id": rollback_id}
            self.state["jobs"].append(job)
            self.save()  # Persist acceptance before the HTTP response / worker.
            threading.Thread(target=self.execute, args=(job["id"],), daemon=True).start()
            return json.loads(json.dumps(job))

    def phase(self, job: dict, status: str, message: str, **fields) -> None:
        with self.lock:
            job.update(status=status, message=message, updated_at=now(), **fields)
            self.save()

    def write_images(self, images: dict[str, str]) -> None:
        atomic_json(self.override, {"services": {service: {"image": image} for service, image in images.items()}})

    def pin(self, images: dict[str, str], job_id: str) -> dict[str, str]:
        pins = {}
        for service, image_id in images.items():
            tag = f"learngraph-retained:{job_id}-{service}"
            self.command(["image", "tag", image_id, tag])
            pins[service] = tag
        return pins

    def image_info(self, image: str) -> dict:
        return json.loads(self.command(["image", "inspect", image]))[0]

    def verify_configuration(self, containers: dict[str, dict]) -> None:
        # A shell-only env override or an omitted hardening file must not
        # silently change volumes, credentials or ports during an update.
        hashes = dict(line.split() for line in self.compose(["config", "--hash", ",".join(containers)]).splitlines())
        for service, container in containers.items():
            labels = container["Config"].get("Labels", {})
            directory = labels.get("com.docker.compose.project.working_dir", "")
            if not directory or Path(directory).resolve() != self.root:
                raise UpdateError("Compose 工作目录与运行中的部署不一致，已拒绝更新")
            if hashes.get(service) != labels.get("com.docker.compose.config-hash"):
                raise UpdateError(f"{service} 的 Compose 配置与运行中容器不同；请恢复原文件/环境并先同步部署")
            if container["State"].get("Health", {}).get("Status") != "healthy":
                raise UpdateError(f"{service} 当前不健康，不能作为可回滚基线")

    def compatibility(self, images: dict[str, str]) -> str:
        values = {(self.image_info(image)["Config"].get("Labels") or {}).get(COMPAT_LABEL) for image in images.values()}
        if len(values) != 1 or not next(iter(values)):
            raise UpdateError("镜像缺少一致的数据库兼容标记；请先部署支持更新器的基线版本")
        return next(iter(values))

    def maintenance(self, container: str, operation: str, *args) -> dict:
        output = self.command(["exec", container, "learngraph-entrypoint", "python", "-m", "app.services.database_maintenance", operation, *args], self.backup_timeout)
        try:
            return json.loads(output)
        except (ValueError, TypeError) as exc:
            raise UpdateError("数据库维护工具未返回有效结果", 503) from exc

    def database_status(self, container: dict) -> dict:
        capability = (container["Config"].get("Labels") or {}).get(DATABASE_LABEL)
        if capability == DATABASE_CAPABILITY:
            return self.maintenance(container["Id"], "status")
        # Older SQLite releases remain updatable. Never infer SQLite from the
        # bootstrap environment when a PostgreSQL activation manifest exists.
        self.command(["exec", container["Id"], "python", "-c",
            LEGACY_SQLITE_GUARD + "\nsrc.close()\n"])
        return {"engine": "sqlite", "capability": None}

    def verify_database(self, job: dict) -> None:
        expected = job.get("database", {})
        if expected.get("engine") == "postgresql":
            current = self.database_status(self.containers()["app"])
            if any(current.get(key) != expected.get(key) for key in ("engine", "activation_id", "identity")):
                raise UpdateError("更新后的主数据库身份发生变化；禁止回退 SQLite 或连接其他数据库")

    def release_maintenance(self, job: dict) -> None:
        if job.get("maintenance_reserved"):
            app = self.containers()["app"]
            self.maintenance(app["Id"], "release", "--owner", job["id"])
            job["maintenance_reserved"] = False
            job.pop("maintenance_cleanup_required", None)
            self.save()

    def backup(self, job: dict, container: str) -> None:
        if job.get("database", {}).get("capability") == DATABASE_CAPABILITY:
            import hashlib
            remote = f"/data/.backup-work/learngraph-update-{job['id']}.zip"
            dest = self.private / (job["id"] + ".zip")
            verified = False
            try:
                info = self.maintenance(container, "backup", "--owner", job["id"], "--output", remote)
                self.command(["cp", f"{container}:{remote}", str(dest)], self.backup_timeout)
                dest.chmod(0o600)
                digest = hashlib.sha256()
                with dest.open("rb") as handle:
                    while block := handle.read(1024 * 1024):
                        digest.update(block)
                if digest.hexdigest() != info["sha256"] or dest.stat().st_size != info["bytes"]:
                    raise UpdateError("宿主机数据库备份与容器内校验结果不一致")
                verified = True
                job["backup"] = {"file": dest.name, "bytes": info["bytes"], "sha256": info["sha256"], "engine": info["engine"]}
                self.save()
            finally:
                if not verified:
                    dest.unlink(missing_ok=True)
                with contextlib.suppress(UpdateError):
                    self.command(["exec", container, "python", "-c", f"from pathlib import Path; Path({remote!r}).unlink(missing_ok=True)"])
            return
        remote = f"/tmp/learngraph-update-{job['id']}.db"
        script = LEGACY_SQLITE_GUARD + f'''
try:
    dst=sqlite3.connect({remote!r})
    try:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or dst.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('SQLite backup integrity validation failed')
    finally:
        dst.close()
finally:
    src.close()
'''
        self.command(["exec", container, "python", "-c", script], self.backup_timeout)
        try:
            dest = self.private / (job["id"] + ".db")
            self.command(["cp", f"{container}:{remote}", str(dest)], self.backup_timeout)
            dest.chmod(0o600)
        finally:
            with contextlib.suppress(UpdateError):
                self.command(["exec", container, "python", "-c", f"import os; os.unlink({remote!r})"])

    def replace(self, images: dict[str, str]) -> None:
        # No pull, build, dependency recreation, volume deletion, or image prune.
        # Exact local image IDs make rollback independent of mutable remote tags.
        if set(images) != set(self.services()):
            raise UpdateError("目标部署拓扑与当前 Compose 不一致，禁止跨拓扑自动回退")
        self.write_images(images)
        self.compose(["up", "-d", "--no-deps", "--no-build", "--pull", "never",
                      "--wait", "--wait-timeout", str(self.timeout), *images], self.timeout + 90)
        actual = self.containers()
        for service in images:
            if actual[service]["Image"] != images[service]:
                raise UpdateError(f"{service} 镜像与目标不一致")
            if actual[service]["State"].get("Health", {}).get("Status") != "healthy":
                raise UpdateError(f"{service} 未通过容器健康检查")
        # App's Compose probe is liveness only. Require DB readiness as well.
        self.command(["exec", actual["app"]["Id"], "python", "-c",
                      "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health',timeout=10));\nif r.get('status')!='ok': raise RuntimeError('Database readiness failed')"], 20)

    def restore(self, job: dict) -> None:
        self.phase(job, "rolling_back", "正在恢复更新前的镜像，不恢复数据库快照")
        try:
            self.replace(job["previous_images"])
            self.verify_database(job)
            self.phase(job, "rolled_back", "更新失败，已恢复原版本并通过健康检查")
        except Exception:
            self.phase(job, "rollback_failed", "回滚失败，需要管理员在宿主机检查；备份和原镜像已保留")

    def execute(self, job_id: str) -> None:
        job = next(item for item in self.state["jobs"] if item["id"] == job_id)
        try:
            self.phase(job, "preparing", "检查当前容器与镜像")
            containers = self.containers()
            self.verify_configuration(containers)
            database = self.database_status(containers["app"])
            job["database"] = database
            self.save()
            previous = {service: item["Image"] for service, item in containers.items()}
            compatibility = self.compatibility(previous)
            old_version = containers["app"]["Config"].get("Labels", {}).get("org.opencontainers.image.version", "unknown")
            if job["kind"] == "rollback":
                old = next(item for item in self.state["jobs"] if item["id"] == job["rollback_id"])
                images = old["previous_images"]
            else:
                if version(job["target_version"]) <= version(old_version):
                    raise UpdateError("升级目标必须高于当前版本；降级请使用部署历史回退")
                self.phase(job, "pulling", "正在下载版本镜像（旧服务继续运行）")
                reference = f"{self.image}:{job['target_version']}"
                self.command(["pull", reference], 1800)
                info = self.image_info(reference)
                labels = info["Config"].get("Labels") or {}
                if labels.get("org.opencontainers.image.version") != job["target_version"]:
                    raise UpdateError("镜像版本标签与 Release 不匹配")
                if labels.get("org.opencontainers.image.source", "").casefold().rstrip("/") != f"https://github.com/{self.repository}".casefold():
                    raise UpdateError("镜像来源与配置的 GitHub 仓库不匹配")
                images = {service: info["Id"] for service in containers}
            if "preview" not in containers and (self.image_info(images["app"])["Config"].get("Labels") or {}).get(TOPOLOGY_LABEL) != COMBINED_TOPOLOGY:
                raise UpdateError("目标镜像不支持合并预览拓扑，禁止更新或回退到独立预览版本")
            if self.compatibility(images) != compatibility:
                raise UpdateError("数据库兼容标记不同，禁止自动升级/回退；请使用人工备份迁移流程")
            if database["engine"] == "postgresql" and any((self.image_info(image)["Config"].get("Labels") or {}).get(DATABASE_LABEL) != DATABASE_CAPABILITY for image in images.values()):
                raise UpdateError("目标镜像不支持 PostgreSQL 单向主库，禁止升级或版本回退到 SQLite 版本")
            if images == previous:
                raise UpdateError("目标镜像已经运行")
            pins = self.pin(previous, job["id"])
            self.pin(images, job["id"] + "-target")
            if database.get("capability") == DATABASE_CAPABILITY:
                job["maintenance_reserved"] = True
                self.save()
                self.maintenance(containers["app"]["Id"], "reserve", "--owner", job["id"])
            self.phase(job, "backing_up", "正在创建并验证主数据库一致性备份", previous_images=previous,
                       target_images=images, previous_version=old_version, retained_tags=pins)
            self.backup(job, containers["app"]["Id"])
            self.verify_configuration(self.containers())
            # Journal BEFORE changing the override or replacing any container.
            self.phase(job, "applying", "正在替换应用（含预览）和出网代理，连接将短暂中断")
            self.replace(images)
            self.verify_database(job)
            self.phase(job, "succeeded", "版本切换完成，容器与数据库健康检查通过")
        except Exception as exc:
            message = str(exc) if isinstance(exc, (UpdateError, ValueError)) else "更新器发生内部错误"
            with self.lock:
                if job["kind"] == "update":
                    self.state["failed_versions"] = list(set(self.state["failed_versions"] + [job["target_version"]]))
                job["error"] = message
                self.save()
            if job["status"] in {"applying", "rolling_back"}:
                self.restore(job)
            else:
                self.phase(job, "failed", message)
        finally:
            try:
                self.release_maintenance(job)
            except UpdateError:
                self.phase(job, job["status"], job["message"], maintenance_cleanup_required=True)

    def recover(self) -> None:
        for job in self.state["jobs"]:
            if job["status"] in TERMINAL:
                with contextlib.suppress(UpdateError):
                    self.release_maintenance(job)
                continue
            if job["status"] in {"applying", "rolling_back"} and job.get("previous_images"):
                if job["kind"] == "update":
                    self.state["failed_versions"] = list(set(self.state["failed_versions"] + [job["target_version"]]))
                self.restore(job)
            else:
                self.phase(job, "failed", "更新器重启中断了切换前的任务；运行中的容器未修改")
            with contextlib.suppress(UpdateError):
                self.release_maintenance(job)

    def poll(self, stop: threading.Event, interval: int) -> None:
        while not stop.is_set():
            try:
                self.check()
                data = self.snapshot()
                with self.lock:
                    target = data["latest"]["version"] if data["latest"] else None
                    should_install = (self.state["auto_install"] and data["has_update"]
                                      and not self.state["check_error"] and not self.active()
                                      and target not in self.state["failed_versions"])
                if should_install:
                    self.submit(target=target)
            except Exception:
                # A transient Docker/GitHub error must not kill the scheduler.
                pass
            stop.wait(interval)


def handler(agent: Agent):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log credentials or request bodies.

        def dispatch(self):
            try:
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + agent.token):
                    raise UpdateError("Unauthorized", 401)
                if self.headers.get("Origin"):
                    raise UpdateError("Browser origins cannot access the host agent directly", 403)
                if self.command == "GET" and self.path == "/v1/health":
                    return 200, {"status": "ok", "service": "learngraph-updater"}
                if self.command == "GET" and self.path == "/v1/status":
                    return 200, agent.snapshot()
                if self.command != "POST":
                    raise UpdateError("Not found", 404)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise UpdateError("Invalid body length", 413)
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise UpdateError("Expected an object", 422)
                if self.path == "/v1/check":
                    agent.check()
                    return 200, agent.snapshot()
                if self.path == "/v1/policy" and type(body.get("auto_install")) is bool:
                    return 200, agent.set_policy(body["auto_install"])
                if self.path == "/v1/jobs":
                    request_id = body.get("request_id")
                    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,64}", request_id):
                        raise UpdateError("A valid request_id is required", 422)
                    target, rollback_id = body.get("version"), body.get("rollback_id")
                    if bool(target) == bool(rollback_id):
                        raise UpdateError("Choose one version or rollback_id", 422)
                    if target is not None and (not isinstance(target, str) or not VERSION.fullmatch(target)):
                        raise UpdateError("Invalid version", 422)
                    if rollback_id is not None and (not isinstance(rollback_id, str) or not re.fullmatch(r"[a-f0-9]{24}", rollback_id)):
                        raise UpdateError("Invalid rollback_id", 422)
                    return 202, agent.submit(target, rollback_id, request_id)
                raise UpdateError("Not found or invalid payload", 422)
            except UpdateError as exc:
                return exc.status, {"error": str(exc)}
            except (ValueError, TypeError, KeyError):
                return 422, {"error": "Invalid request"}
            except Exception:
                return 500, {"error": "Updater internal error"}

        def respond(self):
            self.connection.settimeout(30)
            status, body = self.dispatch()
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            with contextlib.suppress(BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(encoded)

        do_GET = respond
        do_POST = respond

    return Handler


@contextlib.contextmanager
def process_lock(path: Path):
    """OS lock is released on crash; a stale PID file cannot block recovery."""
    with path.open("a+b") as file:
        file.seek(0)
        file.write(b"0")
        file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SystemExit("Another updater already owns this deployment") from exc
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--repository", default="SunnyBoy-y/LearnGraph")
    parser.add_argument("--image", default="ghcr.io/sunnyboy-y/learngraph")
    parser.add_argument("--compose-file", action="append", help="Repeat in the original deployment order")
    parser.add_argument("--host", default="0.0.0.0", help="Container must reach this address; restrict with host firewall")
    parser.add_argument("--port", type=int, default=34116)
    parser.add_argument("--interval", type=int, default=3600)
    parser.add_argument("--backup-timeout", type=int, default=900, help="Primary database backup/verification/copy timeout in seconds")
    parser.add_argument("--init-only", action="store_true", help="Create the service token without starting or changing containers")
    args = parser.parse_args()
    if args.interval < 300:
        parser.error("--interval must be at least 300 seconds")
    state_dir = args.project_dir.resolve() / "data" / "updater"
    state_dir.mkdir(parents=True, exist_ok=True)
    with process_lock(state_dir / "agent.lock"):
        agent = Agent(args.project_dir, args.repository, args.image, args.compose_file, backup_timeout=args.backup_timeout)
        if args.init_only:
            print("Updater credential initialized at data/updater/token; no container was changed.")
            return
        agent.recover()
        stop = threading.Event()
        server = ThreadingHTTPServer((args.host, args.port), handler(agent))
        server.daemon_threads = True
        thread = threading.Thread(target=agent.poll, args=(stop, args.interval), daemon=True)
        thread.start()
        print(f"LearnGraph updater listening on {args.host}:{args.port}; repository={args.repository}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()


if __name__ == "__main__":
    main()
