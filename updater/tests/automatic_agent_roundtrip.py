"""Real administrator-initiated migration with an on-demand provisioner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import tempfile
import time
import uuid

import httpx

from updater.tests.docker_isolation import (
    DISABLED_WORKERS, IsolatedAgent, LIFECYCLE_APP_ENV, LIFECYCLE_DAEMON_ENV,
    initialize_fixture, require,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="learngraph:auto-migration-20261022")
    parser.add_argument("--sandboxd-base", default="learngraph-sandboxd:auto-migration-20261022")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="lg-auto-migration-")).resolve()
    project = "lg-pg-maintenance-" + uuid.uuid4().hex[:10]
    image = "learngraph-maintenance-fixture-" + uuid.uuid4().hex[:10]
    password = secrets.token_urlsafe(40)
    environment = {
        "LEARNGRAPH_ENV": "test", "LEARNGRAPH_DATABASE_URL": "sqlite:////data/learngraph.db",
        "LEARNGRAPH_STORAGE_ROOT": "/data/storage", "LEARNGRAPH_MEMORY_ROOT": "/data/memory",
        "LEARNGRAPH_BOOTSTRAP_ADMIN_PASSWORD": password, "LEARNGRAPH_ENABLE_DEMO_SEED": "false",
        "LEARNGRAPH_ENABLE_DEMO_LOGIN": "false", "LEARNGRAPH_VOICE_RUNTIME_EMBEDDED": "false",
        "LEARNGRAPH_DATABASE_MIGRATION_AGENT_URL": "http://database-migration-agent:8092",
        "LEARNGRAPH_DATABASE_MIGRATION_AGENT_TOKEN_FILE": "/data/database-migration-agent/token",
    }
    environment.update({"LEARNGRAPH_" + worker + "_ENABLED": "false" for worker in DISABLED_WORKERS})
    services = {
        "app": {"image": image + ":v1.0.0", "restart": "no",
            "volumes": ["data:/data", "state:/data/sandboxd:ro"],
            "ports": ["127.0.0.1:18267:8000"],
            "depends_on": {"sandboxd": {"condition": "service_healthy"}},
            "environment": {**environment, **LIFECYCLE_APP_ENV, "LEARNGRAPH_SANDBOXD_DEPLOYMENT_ID": project}},
        "sandboxd": {"image": image + ":sandboxd", "restart": "no",
            "volumes": ["state:/data", "/var/run/docker.sock:/var/run/docker.sock"],
            "environment": {**LIFECYCLE_DAEMON_ENV, "SANDBOXD_DEPLOYMENT_ID": project}},
        "egress-proxy": {"image": image + ":v1.0.0", "restart": "no", "volumes": ["data:/data"],
            "environment": {**environment, "LEARNGRAPH_SANDBOX_EGRESS_PROXY_PORT": "8888"},
            "entrypoint": ["python", "-m", "app.services.egress_proxy_main"],
            "healthcheck": {"test": ["CMD", "python", "-c", "import socket; socket.create_connection(('127.0.0.1',8888),timeout=2).close()"],
                "interval": "2s", "timeout": "3s", "retries": 20}},
        "database-migration-agent": {"image": image + ":v1.0.0", "restart": "no",
            "profiles": ["database-migration"], "volumes": ["data:/data", "/var/run/docker.sock:/var/run/docker.sock"],
            "environment": {"LEARNGRAPH_MASTER_KEY": ""},
            "entrypoint": ["python", "-m", "app.migration_agent"]},
    }
    initialize_fixture(root, project, image, {"name": project, "services": services,
        "volumes": {"data": {}, "state": {}}}, port=18267)
    agent = IsolatedAgent(root, "LearnGraph/UpdateFixture", image, ["compose.json"])
    postgres = None
    report = {"project": project, "production_resources_touched": False, "checks": []}
    def check(message):
        report["checks"].append(message)
        (root / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(message, flush=True)
    try:
        for base, tag in [(args.base, "v1.0.0"), (args.sandboxd_base, "sandboxd")]:
            (root / "Dockerfile").write_text(
                f"FROM {base}\nLABEL org.opencontainers.image.version=v1.0.0\n"
                "LABEL org.opencontainers.image.source=https://github.com/LearnGraph/UpdateFixture\n",
                encoding="utf-8")
            agent.command(["build", "-t", image + ":" + tag, str(root)], 180)
        agent.compose(["up", "-d", "--wait", "--wait-timeout", "150"], 210)
        require(not agent.compose(["ps", "--all", "--quiet", "database-migration-agent"]),
                "agent was created before migration")
        check("three resident services healthy; migration agent absent")
        app = agent.containers()["app"]["Id"]
        agent.command(["exec", app, "learngraph-entrypoint", "python", "-c",
            "from app.core.database import SessionLocal; from app.domain.models import User; "
            "from sqlalchemy import update; db=SessionLocal(); "
            "db.execute(update(User).where(User.username=='admin').values(must_change_password=False));db.commit();db.close()"])
        with httpx.Client(base_url=agent.api_url(), timeout=180, trust_env=False) as client:
            login = client.post("/auth/login", json={"username": "admin", "password": password})
            login.raise_for_status()
            credentials = login.json()
            client.headers.update({"Authorization": "Bearer " + credentials["access_token"],
                                   "X-Workspace-ID": credentials["default_workspace_id"]})
            status = client.get("/migrations/database/status")
            status.raise_for_status()
            require(status.json()["agent_auto_start"] is True, "automatic agent is not advertised")
            preflight = client.post("/migrations/database/preflight", json={})
            preflight.raise_for_status()
            require(preflight.json()["ready"], "migration preflight failed")
            response = client.post("/migrations/database/start", json={
                "confirm": True, "current_password": password, "managed": True, "auto_activate": True,
            })
            response.raise_for_status()
            job = response.json()
            require(response.status_code == 202, "migration was not accepted asynchronously")
            postgres = "learngraph-main-postgres-" + job["id"]
            report["postgres_container"] = postgres
            check("same administrator/password-confirmed HTTP action as the frontend starts migration")
            deadline = time.monotonic() + 420
            while time.monotonic() < deadline:
                response = client.get("/migrations/database/status")
                response.raise_for_status()
                job = response.json()
                if job.get("state") == "FAILED":
                    raise AssertionError("Isolated migration failed: " + str(job.get("error")))
                if job.get("active_database") == "postgresql" and not job.get("running"):
                    break
                time.sleep(.5)
            else:
                raise AssertionError("Isolated automatic migration timed out")
            require(job.get("state") == "COMPLETED" and job.get("agent_state") == "stopped",
                    "migration/agent lifecycle did not finish")
            phases = {event["state"] for event in job.get("events", [])}
            require({"STARTING_AGENT", "AGENT_READY", "TARGET_RUNNING", "COMPLETED"} <= phases,
                    "real startup/migration progress was not recorded")
            check("real PostgreSQL provisioned; backup, copy, validation and activation complete")
            provisioner = agent.compose(["ps", "--all", "--quiet", "database-migration-agent"])
            require(bool(provisioner), "no dynamically created agent")
            state = json.loads(agent.command(["inspect", provisioner]))[0]
            require(not state["State"]["Running"] and state["HostConfig"]["RestartPolicy"]["Name"] == "no",
                    "temporary agent stayed resident")
            postgres = "learngraph-main-postgres-" + job["id"]
            report["postgres_container"] = postgres
            (root / "verification.json").write_text(json.dumps(report), encoding="utf-8")
            agent._verify_target(postgres, agent.verify_isolation())
            health = client.get("/health")
            health.raise_for_status()
            require(health.json()["status"] == "ok", "application is not ready after migration")
            check("temporary agent stopped; PostgreSQL and application remain ready")
        print(json.dumps(report, indent=2), flush=True)
    finally:
        agent.compose(["stop", "app", "sandboxd", "database-migration-agent"], 90)
        agent.remove_optional_agent()
        if postgres:
            try:
                agent.remove_managed_postgres(postgres)
            except Exception:
                print("Owned PostgreSQL cleanup needs inspection:", root, flush=True)
                raise
        agent.compose(["down", "--volumes", "--remove-orphans"], 90)
        tags = agent.fixture_image_tags()
        if tags:
            agent.command(["image", "rm", *tags], 90)
        print("Evidence directory:", root, flush=True)


if __name__ == "__main__":
    main()
