"""Additional durable-configuration recovery on the owned PG acceptance stack."""
import argparse
import json
from uuid import uuid4
from pathlib import Path

import httpx

from updater.agent import SERVICES
from updater.tests.postgresql_roundtrip import BACKUP_PASSWORD, PASSWORD, FixtureAgent
from updater.tests.docker_isolation import require


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    evidence = json.loads((root / "verification.json").read_text())
    require(evidence.get("passed"), "a passed fixture is required")
    agent = FixtureAgent(root, "LearnGraph/UpdateFixture", evidence["image"], ["compose.json"], timeout=100)
    require(evidence["project"] == agent.verify_isolation()["project"] and not agent.active(), "fixture mismatch or active maintenance")
    app = agent.containers()["app"]["Id"]
    with httpx.Client(base_url=agent.api_url(), timeout=120, trust_env=False) as client:
        def login():
            result = client.post("/auth/login", json={"username": "admin", "password": PASSWORD})
            result.raise_for_status()
            value = result.json()
            client.headers.update({"Authorization": "Bearer " + value["access_token"], "X-Workspace-ID": value["default_workspace_id"]})
        login()
        before = agent.command(["exec", app, "learngraph-entrypoint", "python", "-c", "from app.core.config import Settings; print(Settings().max_upload_bytes)"])
        archive = root / ("encrypted-site-" + uuid4().hex + ".zip")
        with client.stream("POST", "/migrations/backup", json={"current_password": PASSWORD, "backup_password": BACKUP_PASSWORD}) as response:
            response.raise_for_status()
            with archive.open("xb") as output:
                for chunk in response.iter_bytes(1024*1024): output.write(chunk)
        with archive.open("rb") as source:
            staged = client.post("/migrations/restore", data={"confirm": "true", "current_password": PASSWORD, "backup_password": BACKUP_PASSWORD}, files={"backup": ("backup.zip", source, "application/zip")})
        staged.raise_for_status()
        agent.command(["exec", app, "python", "-c", "from pathlib import Path; Path('/data/restored.env').write_text('LEARNGRAPH_MAX_UPLOAD_BYTES=12345678\\n')"])
        changed = agent.command(["exec", app, "learngraph-entrypoint", "python", "-c", "from app.core.config import Settings; print(Settings().max_upload_bytes)"])
        assert changed == "12345678"
        agent.compose(["stop", *SERVICES], 90)
        script = "from pathlib import Path; from app.core.config import Settings; from app.services.site_restore import restore_offline; print(restore_offline(Path(" + repr(staged.json()["restore_path"]) + ")," + repr(BACKUP_PASSWORD) + ",Settings(),Path('/data/restored.env'))['status'])"
        assert "restored" in agent.compose(["run", "--rm", "--no-deps", "app", "python", "-c", script], 240)
        agent.compose(["start", "--wait", "--wait-timeout", "160", *SERVICES], 240)
        app = agent.containers()["app"]["Id"]
        after = agent.command(["exec", app, "learngraph-entrypoint", "python", "-c", "from app.core.config import Settings; print(Settings().max_upload_bytes)"])
        assert after == before
        assert client.get("/sessions").status_code == 401
        login()
        assert client.get("/migrations/database/status").json()["active_database"] == "postgresql"
        client.get("/sessions").raise_for_status()
    result = {"passed": True, "original_upload_limit": int(before), "changed_upload_limit": int(changed), "restored_upload_limit": int(after), "persistent_file": "/data/restored.env", "active_database": "postgresql", "old_session_revoked": True}
    (root / "configuration-verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
