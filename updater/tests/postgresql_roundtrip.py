"""Opt-in real application PostgreSQL backup/restore/update acceptance.

Uses local release fixture images, a fresh Compose project and data volume.
No production image tag, container, volume, port or GitHub release is modified.
Run with the backend virtualenv: python -m updater.tests.postgresql_roundtrip.
"""
from __future__ import annotations

import argparse
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import uuid

import httpx

from updater.agent import SERVICES, TERMINAL, handler
from updater.tests.docker_isolation import IsolatedAgent, initialize_fixture, validate_location

PASSWORD = "Postgres-Maintenance-Test-Administrator!2026"
BACKUP_PASSWORD = "Postgres-Maintenance-Backup-Test!2026"


class FixtureAgent(IsolatedAgent):
    def check(self):
        pass  # Releases below are isolated local fixtures, never published.


def wait_until(function, *, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(.5)
    raise AssertionError("Isolated acceptance deadline exceeded")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--base", default="learngraph:postgres-maintenance-test-20261018")
    parser.add_argument("--port", type=int, default=18246)
    parser.add_argument("--agent-port", type=int, default=18746)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--large", action="store_true", help="Generate a real 4 GiB synthetic SQLite database; no production data is read")
    args = parser.parse_args()
    if args.large:
        import shutil
        if shutil.disk_usage(args.root.parent).free < 70 * 1024**3:
            raise RuntimeError("4 GiB acceptance needs 70 GiB free on the host; no fixture writes performed")
    root = validate_location(args.root)
    root.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    project = "lg-pg-maintenance-" + uuid.uuid4().hex[:10]
    image = "learngraph-maintenance-fixture-" + uuid.uuid4().hex[:10]
    baseline = "v1.0.0"
    environment = {
        "LEARNGRAPH_ENV": "test", "LEARNGRAPH_DATABASE_URL": "sqlite:////data/learngraph.db",
        "LEARNGRAPH_STORAGE_ROOT": "/data/storage", "LEARNGRAPH_MEMORY_ROOT": "/data/memory",
        "LEARNGRAPH_BOOTSTRAP_ADMIN_PASSWORD": PASSWORD,
        "LEARNGRAPH_ENABLE_DEMO_SEED": "false", "LEARNGRAPH_ENABLE_DEMO_LOGIN": "false",
        "LEARNGRAPH_DATABASE_MIGRATION_AGENT_URL": "http://database-migration-agent:8092",
        "LEARNGRAPH_DATABASE_MIGRATION_AGENT_TOKEN_FILE": "/data/database-migration-agent/token",
        "LEARNGRAPH_UPDATE_AGENT_URL": f"http://host.docker.internal:{args.agent_port}",
        "LEARNGRAPH_UPDATE_AGENT_TOKEN_FILE": "/run/updater-token",
    }
    for key in ("MODELS_DEV_REFRESH", "DURABLE_QUEUE", "MASTERY_EMBEDDED_SCHEDULER", "MEMORY_RETENTION_SCHEDULER", "MEMORY_EXTRACTION_SCHEDULER", "MEMORY_OUTBOX_WORKER", "SANDBOX", "SANDBOX_CLEANUP_SCHEDULER", "SANDBOX_EXECUTION_SCHEDULER", "MCP_STDIO_CLEANUP_SCHEDULER", "VOICE_TURN_SWEEP", "VOICE_SESSION_REAPER", "VOICE_RUNTIME_EMBEDDED"):
        environment["LEARNGRAPH_" + key + "_ENABLED"] = "false"
    environment["LEARNGRAPH_VOICE_RUNTIME_EMBEDDED"] = "false"
    app_mount = {"type": "bind", "source": str(repo / "backend/app"), "target": "/app/app", "read_only": True}
    ui_mount = {"type": "bind", "source": str(repo / "frontend/dist"), "target": "/app/frontend-dist", "read_only": True}
    token_mount = {"type": "bind", "source": str(root / "data/updater/token"), "target": "/run/updater-token", "read_only": True}
    services = {name: {"image": image + ":" + baseline, "volumes": ["data:/data", app_mount, ui_mount, token_mount], "environment": dict(environment), "restart": "no"} for name in SERVICES}
    if args.large:
        services["app"]["mem_limit"] = "1g"
    services["app"]["ports"] = [f"127.0.0.1:{args.port}:8000"]
    services["app"]["extra_hosts"] = ["host.docker.internal:host-gateway"]
    services["app"]["depends_on"] = {"database-migration-agent": {"condition": "service_healthy"}}
    services["preview"]["depends_on"] = {"app": {"condition": "service_healthy"}}
    services["egress-proxy"]["depends_on"] = {"app": {"condition": "service_healthy"}}
    services["preview"]["environment"]["LEARNGRAPH_SQLITE_READ_ONLY"] = "true"
    services["preview"]["command"] = ["python", "-m", "uvicorn", "app.preview:preview_app", "--host", "0.0.0.0", "--port", "8000"]
    services["egress-proxy"]["entrypoint"] = ["python", "-m", "app.services.egress_proxy_main"]
    services["egress-proxy"]["environment"]["LEARNGRAPH_SANDBOX_EGRESS_PROXY_PORT"] = "8888"
    services["egress-proxy"]["healthcheck"] = {"test": ["CMD", "python", "-c", "import os,socket; assert os.environ.get('FIXTURE_BROKEN')!='1'; socket.create_connection(('127.0.0.1',8888),timeout=2).close()"], "interval": "2s", "timeout": "3s", "retries": 3, "start_period": "4s"}
    services["database-migration-agent"] = {"image": image + ":" + baseline, "entrypoint": ["python", "-m", "app.migration_agent"], "environment": {"LEARNGRAPH_DATABASE_URL": "sqlite:////data/learngraph.db"}, "volumes": ["data:/data", "/var/run/docker.sock:/var/run/docker.sock", app_mount], "restart": "no"}
    services["database-migration-agent"]["healthcheck"] = {"test": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8092/v1/health',timeout=2)"], "interval": "2s", "timeout": "3s", "retries": 5}
    initialize_fixture(root, project, image, {"name": project, "services": services, "volumes": {"data": {}}}, port=args.port)
    agent = FixtureAgent(root, "LearnGraph/UpdateFixture", image, ["compose.json"], timeout=100, backup_timeout=3600)
    (root / "Dockerfile").write_text(f'''FROM {args.base}
ARG FIXTURE_VERSION
ARG BROKEN=0
ARG DATABASE_CAPABILITY=sqlite-postgresql-v1
USER root
RUN printf '%s' "$FIXTURE_VERSION" > /app/VERSION
ENV LEARNGRAPH_APP_VERSION=$FIXTURE_VERSION FIXTURE_BROKEN=$BROKEN
LABEL org.opencontainers.image.version=$FIXTURE_VERSION org.opencontainers.image.source=https://github.com/LearnGraph/UpdateFixture io.learngraph.primary-database=$DATABASE_CAPABILITY
HEALTHCHECK --interval=2s --timeout=4s --start-period=35s --retries=5 CMD python -c "import os,urllib.request; assert os.environ.get('FIXTURE_BROKEN')!='1'; urllib.request.urlopen('http://localhost:8000/api/v1/livez',timeout=3)"
''', encoding="utf-8")
    server = ThreadingHTTPServer(("0.0.0.0", args.agent_port), handler(agent))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    evidence = {"project": project, "image": image, "local_release_fixtures": True, "checks": []}
    (root / "fixture.json").write_text(json.dumps(evidence), encoding="utf-8")
    def check(value):
        evidence["checks"].append(value)
        (root / "verification.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        print(value, flush=True)
    monitor_stop = threading.Event()
    def memory_monitor():
        from updater.tests.large_database_fixture import MEMORY
        with (root / "memory-samples.jsonl").open("a", encoding="utf-8") as out:
            while not monitor_stop.wait(5):
                try:
                    app = agent.containers()["app"]["Id"]
                    sample = json.loads(agent.command(["exec", app, "python", "-c", MEMORY]))
                    sample["phase"] = evidence.get("checks", [])[-1:]
                    out.write(json.dumps(sample) + "\n"); out.flush()
                except Exception as exc:
                    out.write(json.dumps({"monitor_error":type(exc).__name__,"time":time.time()})+"\n"); out.flush()
    if args.large:
        threading.Thread(target=memory_monitor,daemon=True).start()
    try:
        for tag, broken, capability in [(baseline, "0", "sqlite-postgresql-v1"), ("v1.1.0", "0", "sqlite-postgresql-v1"), ("v1.2.0", "1", "sqlite-postgresql-v1"), ("v1.3.0", "0", "sqlite-only")]:
            agent.command(["build", "-t", image + ":" + tag, "--build-arg", "FIXTURE_VERSION=" + tag, "--build-arg", "BROKEN=" + broken, "--build-arg", "DATABASE_CAPABILITY=" + capability, str(root)], 240)
        agent.compose(["up", "-d", "--wait", "--wait-timeout", "160"], 240)
        check("fresh isolated application + preview + egress proxy + migration agent healthy")
        app_id = agent.containers()["app"]["Id"]
        agent.command(["exec", app_id, "learngraph-entrypoint", "python", "-c", "from app.core.database import SessionLocal; from app.domain.models import User; from sqlalchemy import update; db=SessionLocal(); db.execute(update(User).where(User.username=='admin').values(must_change_password=False)); db.commit()"])
        client = httpx.Client(base_url=agent.api_url(), timeout=3600 if args.large else 90, trust_env=False)
        def login():
            result = client.post("/auth/login", json={"username": "admin", "password": PASSWORD})
            result.raise_for_status()
            value = result.json()
            client.headers.update({"Authorization": "Bearer " + value["access_token"], "X-Workspace-ID": value["default_workspace_id"]})
        login()
        state = client.get("/migrations/database/status").json()
        assert state["active_database"] == "sqlite"
        if args.large:
            from updater.tests.large_database_fixture import SEED
            seed = json.loads(agent.command(["exec",app_id,"learngraph-entrypoint","python","-c",SEED],1800))
            evidence["synthetic_database"] = seed
            require_large = seed["sqlite_bytes"] >= 4 * 1024**3 and seed["integrity"] == "ok" and seed["foreign_key_violations"] == 0
            if not require_large: raise RuntimeError("Synthetic size/integrity gate failed")
            check("generated >=4 GiB SQLite database from unique random synthetic JSON; integrity and foreign keys pass")
        def download(path):
            with client.stream("POST", "/migrations/backup", json={"current_password": PASSWORD, "backup_password": BACKUP_PASSWORD}) as response:
                response.raise_for_status()
                with path.open("xb") as out:
                    for chunk in response.iter_bytes(1024*1024): out.write(chunk)
            return path
        sqlite_backup = download(root / "encrypted-sqlite-site-backup.zip")
        check("SQLite encrypted full-site backup streamed to disk and independently verified by server")
        probe_stop, probes = threading.Event(), []
        def probe_business():
            with httpx.Client(base_url=client.base_url,headers=dict(client.headers),timeout=30,trust_env=False) as live:
                while not probe_stop.wait(1):
                    try:
                        started = time.monotonic()
                        read = live.get('/sessions')
                        write = live.post('/sessions',json={'title':'live-migration-probe-'+str(len(probes))})
                        probes.append({'read':read.status_code,'write':write.status_code,'id':write.json().get('id'),'seconds':time.monotonic()-started})
                    except Exception as exc:
                        probes.append({'error':type(exc).__name__})
        probe_thread = threading.Thread(target=probe_business,daemon=True)
        probe_thread.start()
        assert client.post("/migrations/database/preflight").json()["ready"]
        request = client.post("/migrations/database/start", json={"confirm": True, "current_password": PASSWORD, "managed": True, "auto_activate": True})
        request.raise_for_status()
        def migrated():
            state = client.get("/migrations/database/status").json()
            assert state.get("state") not in {"FAILED", "RECOVERING"}, state
            return state if state.get("state") == "COMPLETED" else None
        try:
            migration = wait_until(migrated,timeout=7200 if args.large else 180)
        finally:
            probe_stop.set()
            probe_thread.join(35)
        evidence["live_migration_requests"] = probes
        if not probes or any(p.get("read") != 200 or p.get("write") != 201 for p in probes):
            raise RuntimeError("Live migration business probe failed; inspect private verification evidence")
        pg_container = "learngraph-main-postgres-" + migration["id"]
        evidence["postgres_container"] = pg_container
        if args.large:
            import hashlib
            probe_ids = [p['id'] for p in probes]
            expected = hashlib.sha256("\n".join(sorted(probe_ids)).encode()).hexdigest()
            verify = "import json,hashlib; from app.core.database import SessionLocal; from sqlalchemy import text; db=SessionLocal(); rows=sorted(db.execute(text(\"SELECT id FROM chat_sessions WHERE title LIKE 'live-migration-probe-%'\")).scalars()); assert len(rows)=="+str(len(probe_ids))+"; assert hashlib.sha256('\\n'.join(rows).encode()).hexdigest()=="+repr(expected)+"; print(json.dumps({'verified_live_writes':len(rows),'dialect':db.connection().dialect.name})); db.close()"
            evidence['live_migration_verification'] = json.loads(agent.command(['exec',app_id,'learngraph-entrypoint','python','-c',verify]))
        check("administrator triggered mandatory backup, Docker PostgreSQL provision, complete table validation and one-way activation")
        with sqlite_backup.open("rb") as sqlite_file:
            forbidden = client.post("/migrations/restore", data={"confirm": "true", "current_password": PASSWORD, "backup_password": BACKUP_PASSWORD}, files={"backup": ("sqlite.zip", sqlite_file, "application/zip")})
        assert forbidden.status_code == 409, forbidden.text
        check("real encrypted SQLite full-site backup cannot be restored after PostgreSQL activation")
        uploaded = client.post("/files", files={"file": ("maintenance.txt", b"PostgreSQL backup fixture", "text/plain")})
        uploaded.raise_for_status()
        agent.command(["exec", app_id, "python", "-c", "from pathlib import Path; Path('/data/memory/maintenance-fixture.txt').write_text('backup memory fixture')"])
        saved = client.post("/sessions", json={"title": "before PostgreSQL backup"}); saved.raise_for_status()
        saved_id = saved.json()["id"]
        archive_path = download(root / "encrypted-site-backup.zip")
        expected_upload_limit = agent.command(["exec", app_id, "learngraph-entrypoint", "python", "-c", "from app.core.config import Settings; print(Settings().max_upload_bytes)"])
        with archive_path.open("rb") as backup_file:
            staged = client.post("/migrations/restore", data={"confirm": "true", "current_password": PASSWORD, "backup_password": BACKUP_PASSWORD}, files={"backup": ("backup.zip", backup_file, "application/zip")})
        staged.raise_for_status()
        check("real administrator HTTP encrypted full-site PostgreSQL backup, uploaded file and Memory, restore validation/staging succeeded")
        after = client.post("/sessions", json={"title": "after backup must disappear"}); after.raise_for_status()
        after_id = after.json()["id"]
        agent.command(["exec", app_id, "python", "-c", "from pathlib import Path; Path('/data/restored.env').write_text('LEARNGRAPH_MAX_UPLOAD_BYTES=12345678\\n')"])
        old_auth = client.headers["Authorization"]
        agent.compose(["stop", *SERVICES], 90)
        script = "import getpass; from pathlib import Path; from app.core.config import Settings; from app.services.site_restore import restore_offline; print(restore_offline(Path(" + repr(staged.json()["restore_path"]) + ")," + repr(BACKUP_PASSWORD) + ",Settings(),Path('/data/restored.env'))['status'])"
        agent.compose(["run", "--rm", "--no-deps", "app", "python", "-c", script], 3600 if args.large else 240)
        agent.compose(["start", "--wait", "--wait-timeout", "160", *SERVICES], 240)
        assert client.get("/sessions").status_code == 401
        login()
        sessions = client.get("/sessions"); sessions.raise_for_status()
        ids = {item["id"] for item in sessions.json()}
        assert saved_id in ids and after_id not in ids
        assert client.get("/migrations/database/status").json()["active_database"] == "postgresql"
        restored_limit = agent.command(["exec", app_id, "learngraph-entrypoint", "python", "-c", "from app.core.config import Settings; print(Settings().max_upload_bytes)"])
        assert restored_limit == expected_upload_limit
        assert client.get("/files").json()[0]["id"] == uploaded.json()["id"]
        check("stopped all business processes, restored actual PostgreSQL + files + Memory, revoked old session, restarted on same PostgreSQL identity")
        with httpx.Client(base_url=client.base_url, timeout=20, trust_env=False) as member:
            result = member.post("/auth/register", json={"username": "maintenance_member", "display_name": "test member", "password": PASSWORD})
            result.raise_for_status()
            value = result.json()
            member.headers.update({"Authorization": "Bearer " + value["access_token"], "X-Workspace-ID": value["default_workspace_id"]})
            assert member.post("/migrations/backup", json={"current_password": PASSWORD, "backup_password": BACKUP_PASSWORD}).status_code == 403
            assert member.post("/migrations/restore", data={"confirm": "true", "current_password": PASSWORD, "backup_password": BACKUP_PASSWORD}, files={"backup": ("backup.zip", b"unauthorized request must not parse a real archive", "application/zip")}).status_code == 403
            for path, body in [("/updates/jobs", {"version": "v1.1.0", "request_id": uuid.uuid4().hex}), ("/updates/policy", {"auto_install": True})]:
                assert member.post(path, json=body).status_code == 403
        check("non-system-admin HTTP backup, restore, update and automatic policy all return 403")
        agent.state["releases"] = [{"version": "v1.1.0"}]
        agent.save()
        policy = client.post("/updates/policy", json={"auto_install": True}); policy.raise_for_status()
        stop = threading.Event()
        threading.Thread(target=agent.poll, args=(stop, 1), daemon=True).start()
        def completed_job():
            return agent.state["jobs"][-1] if agent.state["jobs"] and agent.state["jobs"][-1]["status"] in TERMINAL and not agent.active() else None
        updated = wait_until(completed_job, timeout=3600 if args.large else 300)
        stop.set()
        assert updated["status"] == "succeeded", updated
        assert updated["backup"]["engine"] == "postgresql"
        check("administrator enabled automatic policy: scheduler performed native PostgreSQL backup and real three-container upgrade to v1.1.0")
        login()
        written = client.post("/sessions", json={"title": "written after upgrade, preserve on image rollback"}); written.raise_for_status()
        written_id = written.json()["id"]
        rollback = client.post("/updates/jobs", json={"rollback_id": updated["id"], "request_id": uuid.uuid4().hex}); rollback.raise_for_status()
        rolled = wait_until(lambda: agent.state["jobs"][-1] if agent.state["jobs"][-1]["id"] == rollback.json()["id"] and agent.state["jobs"][-1]["status"] in TERMINAL and not agent.active() else None, timeout=3600 if args.large else 300)
        assert rolled["status"] == "succeeded", rolled
        login()
        assert written_id in {item["id"] for item in client.get("/sessions").json()}
        check("manual image rollback to v1.0.0 preserved post-upgrade PostgreSQL write, target identity and activation marker")
        for version, expected in [("v1.3.0", "failed"), ("v1.2.0", "rolled_back")]:
            agent.state["releases"] = [{"version": version}]
            agent.save()
            result = client.post("/updates/jobs", json={"version": version, "request_id": uuid.uuid4().hex}); result.raise_for_status()
            job = wait_until(lambda: agent.state["jobs"][-1] if agent.state["jobs"][-1]["id"] == result.json()["id"] and agent.state["jobs"][-1]["status"] in TERMINAL and not agent.active() else None, timeout=3600 if args.large else 360)
            assert job["status"] == expected, job
            check("incompatible SQLite-only image rejected before mutation" if version == "v1.3.0" else "unhealthy image triggered real automatic rollback, PostgreSQL identity and new data preserved")
            login()
        assert client.get("/migrations/database/status").json()["active_database"] == "postgresql"
        assert written_id in {item["id"] for item in client.get("/sessions").json()}
        assert not agent.state["auto_install"]
        # Emulate a host-updater crash after replacement but before its journal
        # commit, then recover with a fresh Agent instance against real Docker.
        incomplete = next(item for item in agent.state["jobs"] if item["id"] == updated["id"])
        agent.phase(incomplete, "applying", "isolated crash recovery fixture")
        agent.replace(incomplete["target_images"])
        restarted = FixtureAgent(root, "LearnGraph/UpdateFixture", image, ["compose.json"], timeout=100, backup_timeout=3600)
        restarted.recover()
        assert restarted.state["jobs"][0]["status"] == "rolled_back"
        assert restarted.current()["version"] == baseline
        login()
        assert written_id in {item["id"] for item in client.get("/sessions").json()}
        check("fresh host updater recovered an interrupted real image switch; PostgreSQL post-upgrade data remained intact")
        evidence["database_backups"] = len(list(agent.private.glob("*.zip")))
        evidence["passed"] = True
        check("final PostgreSQL readiness and post-upgrade data verified; no SQLite fallback; native pre-update artifacts retained privately")
        print(json.dumps({"passed":True,"project":project,"checks":len(evidence["checks"]),"evidence":str(root/"verification.json")},ensure_ascii=False),flush=True)
    finally:
        monitor_stop.set()
        server.shutdown(); server.server_close()
        if not args.keep:
            if evidence.get("postgres_container"):
                agent.command(["rm", "-f", evidence["postgres_container"]])
            agent.compose(["down", "--remove-orphans"], 90)


if __name__ == "__main__":
    main()
