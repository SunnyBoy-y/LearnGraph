"""Restricted, leased lifecycle for this deployment's PostgreSQL provisioner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import threading
import time

JOB = re.compile(r"[a-f0-9]{32}\Z")
ENTRYPOINT = ["python", "-m", "app.migration_agent"]
SERVICE = "database-migration-agent"


class MigrationAgentError(RuntimeError):
    pass


def _mount(mount):
    source = mount.get("Name") if mount.get("Type") == "volume" else mount.get("Source")
    if mount.get("Destination") == "/var/run/docker.sock" and source == "/run/host-services/docker.proxy.sock":
        source = "/var/run/docker.sock"
    return (mount.get("Type"), source,
            mount.get("Destination"), mount.get("RW"))


class ManagedMigrationAgent:
    def __init__(self, config, *, client_factory=None):
        self.config = config
        self.path = Path(config.state_path).parent / "migration-agent-lease.json"
        self.lock = threading.RLock()
        self.client_factory = client_factory
        self.last_sweep = 0.0

    def _client(self):
        if self.client_factory:
            return self.client_factory()
        import docker
        return docker.DockerClient(base_url=self.config.docker_host, timeout=15) if self.config.docker_host else docker.from_env(timeout=15)

    def _read(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def _scope(self, client, *, require_running=True):
        owner = client.containers.get(socket.gethostname())
        project = owner.labels.get("com.docker.compose.project", "")
        owner_environment = dict(v.split("=", 1) for v in owner.attrs["Config"].get("Env", []) if "=" in v)
        if owner_environment.get("SANDBOXD_DEPLOYMENT_ID") != self.config.deployment_id:
            raise MigrationAgentError("Control-plane deployment identity does not match")
        if owner.labels.get("com.docker.compose.service") != "sandboxd" or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
            raise MigrationAgentError("Docker Compose deployment identity is unavailable")
        apps = client.containers.list(all=True, filters={"label": [
            "com.docker.compose.project=" + project, "com.docker.compose.service=app",
        ]})
        if len(apps) != 1 or require_running and apps[0].status != "running":
            raise MigrationAgentError("Exactly one running application is required")
        app = apps[0]
        if app.labels.get("io.learngraph.acceptance-owner") != owner.labels.get("io.learngraph.acceptance-owner"):
            raise MigrationAgentError("Acceptance ownership differs from the control plane")
        network = project + "_default"
        if network not in app.attrs["NetworkSettings"]["Networks"]:
            raise MigrationAgentError("Application network does not match deployment")
        if client.networks.get(network).attrs.get("Labels", {}).get("com.docker.compose.project") != project:
            raise MigrationAgentError("Application network ownership does not match")
        data = [m for m in app.attrs["Mounts"] if m["Destination"] == "/data"]
        sockets = [m for m in owner.attrs["Mounts"] if m["Destination"] == "/var/run/docker.sock"]
        if len(data) != 1 or len(sockets) != 1 or not data[0].get("RW") or data[0].get("Type") not in {"bind", "volume"} or sockets[0].get("Type") != "bind":
            raise MigrationAgentError("Approved application data/socket mounts are unavailable")
        if not _mount(data[0])[1] or _mount(data[0])[1] in {"/", "C:\\", "C:/"}:
            raise MigrationAgentError("Unsafe application data mount")
        if (app.image.attrs.get("Config", {}).get("Labels") or {}).get("io.learngraph.primary-database") != "sqlite-postgresql-v1":
            raise MigrationAgentError("Application image lacks PostgreSQL support")
        return project, app, network, data[0], sockets[0]

    def _find(self, client, project):
        agents = client.containers.list(all=True, filters={"label": [
            "com.docker.compose.project=" + project, "com.docker.compose.service=" + SERVICE,
        ]})
        if len(agents) > 1:
            raise MigrationAgentError("Multiple migration agents exist")
        return agents[0] if agents else None

    def _verify(self, agent, project, image, network, data, socket_mount):
        agent.reload()
        attrs, config = agent.attrs, agent.attrs["Config"]
        mounts = {m["Destination"]: m for m in attrs["Mounts"]}
        host = attrs["HostConfig"]
        if (agent.labels.get("com.docker.compose.project") != project
            or agent.labels.get("com.docker.compose.service") != SERVICE
            or (attrs.get("Image") or agent.image.id) != image or config.get("Entrypoint") != ENTRYPOINT or config.get("Cmd")
            or set(mounts) != {"/data", "/var/run/docker.sock"}
            or _mount(mounts["/data"]) != _mount(data)
            or _mount(mounts["/var/run/docker.sock"]) != _mount(socket_mount)
            or set(attrs["NetworkSettings"]["Networks"]) != {network}
            or SERVICE not in (attrs["NetworkSettings"]["Networks"].get(network, {}).get("Aliases") or [])
            or host.get("Privileged") or host.get("CapAdd") or host.get("Devices") or host.get("PidMode")
            or host.get("PortBindings") or config.get("User") not in {"", "root", "0", "0:0"}
            or host.get("RestartPolicy", {}).get("Name") not in {"no", ""}):
            raise MigrationAgentError("Migration agent configuration does not match the approved deployment")

    def start(self, job_id):
        if not JOB.fullmatch(job_id):
            raise MigrationAgentError("Invalid migration job")
        if not self.config.managed_database_enabled or self.config.runtime_backend != "docker":
            raise MigrationAgentError("Managed database control is disabled")
        with self.lock:
            self.sweep()
            client = self._client()
            try:
                prior = self._read()
                if prior and prior["job_id"] != job_id and prior["expires_at"] > time.time():
                    raise MigrationAgentError("Another migration owns the agent")
                project, app, network, data, socket_mount = self._scope(client)
                agent = self._find(client, project)
                if agent is not None and (agent.attrs.get("Image") or agent.image.id) != app.image.id:
                    old_image = agent.attrs.get("Image") or agent.image.id
                    self._verify(agent, project, old_image, network, data, socket_mount)
                    if agent.status == "running":
                        raise MigrationAgentError("Stop the outdated migration agent before upgrading it")
                    # An image-only application update must not leave a stopped
                    # provisioner permanently pinned to the previous release.
                    agent.remove()
                    agent = None
                if agent is None:
                    volumes = {
                        _mount(data)[1]: {"bind": "/data", "mode": "rw"},
                        socket_mount["Source"]: {"bind": "/var/run/docker.sock", "mode": "rw"},
                    }
                    environment = dict(v.split("=", 1) for v in app.attrs["Config"].get("Env", []) if "=" in v)
                    labels = {"com.docker.compose.project": project, "com.docker.compose.service": SERVICE,
                              "com.docker.compose.container-number": "1", "com.docker.compose.oneoff": "False",
                              "io.learngraph.managed-migration-agent": self.config.deployment_id}
                    # Preserve only deployment location and the test fixture's
                    # ownership marker, never caller-supplied Docker labels.
                    for key in ("com.docker.compose.project.working_dir", "io.learngraph.acceptance-owner"):
                        if key in app.labels:
                            labels[key] = app.labels[key]
                    agent = client.containers.create(
                        app.image.id, name=project + "-" + SERVICE + "-1",
                        entrypoint=ENTRYPOINT, command=[], network=network,
                        networking_config={network: client.api.create_endpoint_config(aliases=[SERVICE])},
                        environment={"LEARNGRAPH_MASTER_KEY": environment.get("LEARNGRAPH_MASTER_KEY", "")},
                        volumes=volumes, restart_policy={"Name": "no"},
                        labels=labels,
                        healthcheck={"Test": ["CMD", "python", "-c",
                            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8092/v1/health',timeout=3)"],
                            "Interval": 5_000_000_000, "Timeout": 4_000_000_000, "Retries": 12},
                    )
                self._verify(agent, project, app.image.id, network, data, socket_mount)
                if prior and prior["job_id"] != job_id and agent.status == "running":
                    raise MigrationAgentError("Expired agent must be stopped before assigning a new job")
                # Journal before start: a daemon/app crash still leaves a
                # bounded lease for an agent that may have started.
                record = {"job_id": job_id, "container_id": agent.id, "image": app.image.id,
                          "expires_at": time.time() + self.config.migration_agent_idle_seconds}
                self._save(record)
                if agent.status != "running":
                    agent.start()
                return {"state": "running", "job_id": job_id}
            except MigrationAgentError:
                raise
            except Exception:
                raise MigrationAgentError("Migration agent startup failed; inspect host configuration") from None
            finally:
                client.close()

    def stop(self, job_id):
        with self.lock:
            record = self._read()
            if not record:
                return {"state": "stopped"}
            if record["job_id"] != job_id:
                raise MigrationAgentError("Migration job does not own the agent")
            client = self._client()
            try:
                project, _app, network, data, socket_mount = self._scope(client, require_running=False)
                agent = self._find(client, project)
                if agent is not None:
                    if agent.id != record["container_id"]:
                        raise MigrationAgentError("Migration agent identity changed")
                    self._verify(agent, project, record["image"], network, data, socket_mount)
                    if agent.status == "running":
                        agent.stop(timeout=10)
                self.path.unlink(missing_ok=True)
                return {"state": "stopped", "job_id": job_id}
            except MigrationAgentError:
                raise
            except Exception:
                raise MigrationAgentError("Migration agent shutdown failed") from None
            finally:
                client.close()

    def sweep(self):
        if time.monotonic() - self.last_sweep < 5:
            return
        self.last_sweep = time.monotonic()
        with self.lock:
            record = self._read()
            if record and record["expires_at"] <= time.time():
                self.stop(record["job_id"])
