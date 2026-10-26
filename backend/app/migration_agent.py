"""Private, narrowly scoped Docker provisioner for the main PostgreSQL database.

Only this service receives the Docker socket. It accepts an opaque migration ID,
never an image, command, mount, container ID or database URL from its caller.
Starting the agent alone does not pull an image or create a database.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import signal
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.core.database_routing import atomic_json, read_json
from app.core.security import SecretCipher

IMAGE = "postgres:18-alpine"
LABEL = "io.learngraph.primary-migration"
PATH = re.compile(r"^/v1/postgres/([a-f0-9]{32})$")


class Provisioner:
    def __init__(self, root: Path, token_file: Path):
        self.root, self.token_file = root, token_file
        root.mkdir(parents=True, exist_ok=True)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        if not token_file.exists():
            descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            with os.fdopen(descriptor, "w") as handle:
                handle.write(secrets.token_urlsafe(48))
                handle.flush()
                os.fsync(handle.fileno())
        if os.name != "nt" and os.getuid() == 0:
            import pwd
            gid = pwd.getpwnam("learngraph").pw_gid
            os.chown(token_file.parent, -1, gid)
            token_file.parent.chmod(0o750)
            os.chown(token_file, -1, gid)
            token_file.chmod(0o640)
            root.chmod(0o700)
        self.token = token_file.read_text().strip()
        if len(self.token) < 32:
            raise RuntimeError("Migration agent token is invalid")
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def _path(self, job: str) -> Path:
        return self.root / (job + ".json")

    def _cipher(self) -> SecretCipher:
        key = os.environ.get("LEARNGRAPH_MASTER_KEY", "").strip()
        if not key:
            key = Path("/data/.master-key").read_text().strip()
        if not key:
            raise RuntimeError("Deployment key unavailable")
        return SecretCipher(key)

    def status(self, job: str) -> dict:
        record = read_json(self._path(job)) or {"state": "absent"}
        result = {key: value for key, value in record.items() if key not in {"admin_secret", "app_secret", "database_secret"}}
        if record.get("state") == "ready":
            result["database_url"] = self._cipher().decrypt(record["database_secret"])
        return result

    def start(self, job: str) -> dict:
        with self._lock:
            if not self._threads.get(job) or not self._threads[job].is_alive():
                thread = threading.Thread(target=self._run_guarded, args=(job,), daemon=True)
                self._threads[job] = thread
                thread.start()
        return {"job_id": job, "accepted": True}

    def _save(self, job: str, **values) -> None:
        atomic_json(self._path(job), {**(read_json(self._path(job)) or {}), **values, "job_id": job})

    def _run_guarded(self, job: str) -> None:
        try:
            self._run(job)
        except Exception:
            # Docker/driver exception text can include secrets or daemon paths.
            self._save(job, state="failed", error="postgresql_provisioning_failed")

    def _run(self, job: str) -> None:
        import docker
        from docker.errors import NotFound
        from sqlalchemy.engine import URL
        import psycopg
        from psycopg import sql

        cipher = self._cipher()
        client = docker.from_env(timeout=20)
        try:
            owner = client.containers.get(socket.gethostname())
            project = owner.labels.get("com.docker.compose.project")
            networks = list(owner.attrs["NetworkSettings"]["Networks"])
            network = os.environ.get("LEARNGRAPH_DATABASE_DOCKER_NETWORK") or next((name for name in networks if name == f"{project}_default"), None)
            if not network or network not in networks:
                raise RuntimeError("The database agent requires the application network")
            labels = {LABEL: job, "io.learngraph.role": "main-postgresql"}
            record = read_json(self._path(job)) or {}
            if not record.get("admin_secret"):
                self._save(job, admin_secret=cipher.encrypt(secrets.token_urlsafe(48)), app_secret=cipher.encrypt(secrets.token_urlsafe(48)), state="pulling")
            record = read_json(self._path(job))
            admin_password = cipher.decrypt(record["admin_secret"])
            app_password = cipher.decrypt(record["app_secret"])
            self._save(job, state="pulling", image=IMAGE, progress={})
            layers: dict = {}
            last = 0.0
            # Engine pull streams real layer bytes/status. Always resolve the
            # approved tag and create by immutable image ID after pulling.
            for event in client.api.pull(IMAGE, stream=True, decode=True):
                if event.get("error"):
                    raise RuntimeError("Image pull failed")
                layers[event.get("id", "manifest")] = {"status": event.get("status"), **event.get("progressDetail", {})}
                if time.monotonic() - last > 0.3:
                    self._save(job, progress={"layers": layers})
                    last = time.monotonic()
            image = client.images.get(IMAGE)
            name = "learngraph-main-postgres-" + job
            volume_name = name + "-data"
            try:
                volume = client.volumes.get(volume_name)
                if volume.attrs.get("Labels", {}).get(LABEL) != job:
                    raise RuntimeError("Database volume ownership mismatch")
            except NotFound:
                volume = client.volumes.create(name=volume_name, labels=labels)
            try:
                container = client.containers.get(name)
                if container.labels.get(LABEL) != job:
                    raise RuntimeError("Database container ownership mismatch")
            except NotFound:
                container = client.containers.create(image.id, name=name, hostname=name, network=network,
                    labels=labels, environment={"POSTGRES_USER": "lg_bootstrap", "POSTGRES_PASSWORD": admin_password, "POSTGRES_DB": "postgres"},
                    volumes={volume.name: {"bind": "/var/lib/postgresql", "mode": "rw"}},
                    restart_policy={"Name": "unless-stopped"}, mem_limit="768m", nano_cpus=1_000_000_000, shm_size="128m",
                    security_opt=["no-new-privileges:true"],
                    healthcheck={"Test": ["CMD-SHELL", "pg_isready -U lg_bootstrap -d postgres"], "Interval": 2_000_000_000, "Timeout": 2_000_000_000, "Retries": 60})
            actual_image = container.image
            self._save(job, state="starting", container=name, volume=volume.name, image_digest=(actual_image.attrs.get("RepoDigests") or [actual_image.id])[0])
            container.reload()
            if container.status != "running":
                container.start()
            deadline = time.monotonic() + 180
            while True:
                container.reload()
                if container.attrs["State"].get("Health", {}).get("Status") == "healthy":
                    break
                if time.monotonic() > deadline or container.status in {"dead", "exited"}:
                    raise RuntimeError("PostgreSQL startup failed")
                time.sleep(0.5)
            with psycopg.connect(host=name, dbname="postgres", user="lg_bootstrap", password=admin_password, connect_timeout=5, autocommit=True) as connection:
                if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname='learngraph'").fetchone():
                    connection.execute(sql.SQL("CREATE ROLE learngraph LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Literal(app_password)))
                if not connection.execute("SELECT 1 FROM pg_database WHERE datname='learngraph'").fetchone():
                    connection.execute("CREATE DATABASE learngraph OWNER learngraph")
                    connection.execute("REVOKE CONNECT ON DATABASE learngraph FROM PUBLIC")
            url = URL.create("postgresql+psycopg", username="learngraph", password=app_password, host=name, port=5432, database="learngraph").render_as_string(hide_password=False)
            self._save(job, state="ready", database_secret=cipher.encrypt(url), progress={"layers": layers})
        finally:
            client.close()


def handler(provisioner: Provisioner):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/v1/health":
                return self.respond(200, {"status": "ok"})
            self.dispatch(False)

        def do_POST(self):
            self.dispatch(True)

        def dispatch(self, start: bool):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + provisioner.token):
                return self.respond(403, {"error": "forbidden"})
            match = PATH.fullmatch(self.path)
            if not match:
                return self.respond(404, {"error": "not_found"})
            length = self.headers.get("Content-Length", "0")
            if length != "0" or self.headers.get("Transfer-Encoding"):
                # Drain only a bounded rejected body so local clients receive
                # the 400 response instead of a TCP reset on Windows.
                if length.isdigit() and 0 < int(length) <= 4096:
                    self.rfile.read(int(length))
                return self.respond(400, {"error": "request_body_forbidden"})
            try:
                value = provisioner.start(match[1]) if start else provisioner.status(match[1])
                self.respond(202 if start else 200, value)
            except Exception:
                self.respond(503, {"error": "provisioner_unavailable"})
    return Handler


def main():
    provisioner = Provisioner(Path("/data/database-migration-agent/jobs"), Path("/data/database-migration-agent/token"))
    server = ThreadingHTTPServer(("0.0.0.0", 8092), handler(provisioner))
    def stop(_signal, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
