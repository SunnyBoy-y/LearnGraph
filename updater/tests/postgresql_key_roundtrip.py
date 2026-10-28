"""Restore a backup onto a differently generated deployment key, using a packaged image."""
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
    parser.add_argument("--image", default="learngraph:postgres-maintenance-packaged-smoke-20261018")
    args = parser.parse_args()
    root = args.root.resolve()
    evidence = json.loads((root / "verification.json").read_text())
    require(evidence.get("passed"), "a passed fixture is required")
    agent = FixtureAgent(root, "LearnGraph/UpdateFixture", evidence["image"], ["compose.json"], timeout=100)
    require(evidence["project"] == agent.verify_isolation()["project"] and not agent.active(), "fixture mismatch or active maintenance")
    with httpx.Client(base_url=agent.api_url(), timeout=120, trust_env=False) as client:
        response = client.post("/auth/login", json={"username": "admin", "password": PASSWORD}); response.raise_for_status()
        user = response.json()
        client.headers.update({"Authorization": "Bearer " + user["access_token"], "X-Workspace-ID": user["default_workspace_id"]})
        archive = root / ("encrypted-site-" + uuid4().hex + ".zip")
        with client.stream("POST", "/migrations/backup", json={"current_password": PASSWORD, "backup_password": BACKUP_PASSWORD}) as response:
            response.raise_for_status()
            with archive.open("xb") as output:
                for chunk in response.iter_bytes(1024*1024): output.write(chunk)
        with archive.open("rb") as source:
            staged = client.post("/migrations/restore", data={"confirm": "true", "current_password": PASSWORD, "backup_password": BACKUP_PASSWORD}, files={"backup": ("backup.zip", source, "application/zip")})
        staged.raise_for_status()
        agent.compose(["stop", *SERVICES], 90)
        rotation = "import secrets; from pathlib import Path; from app.core.config import Settings; from app.services.postgresql_backup import effective_url; from app.services.site_restore_postgresql import _write; from app.core.database_routing import state_root,read_json,atomic_json,encrypt_url; s=Settings(); u=effective_url(s); p=state_root(s)/'active.json'; n=secrets.token_urlsafe(48); atomic_json(p,{**read_json(p),**encrypt_url(s.model_copy(update={'master_key':n}),u)}); _write(Path('/data/.master-key'),n.encode()); print('isolated target key changed without printing it')"
        agent.compose(["run", "--rm", "--no-deps", "app", "python", "-c", rotation], 120)
        script = "import os; from pathlib import Path; from app.core.config import Settings; from app.services.site_restore import restore_offline; assert os.environ.get('_LEARNGRAPH_MASTER_KEY_FROM_FILE')=='1'; print(restore_offline(Path(" + repr(staged.json()["restore_path"]) + ")," + repr(BACKUP_PASSWORD) + ",Settings(),Path('/data/restored.env'))['status'])"
        token = agent.state_dir / "token"
        output = agent.command(["run", "--rm", "--network", evidence["project"] + "_default", "--mount", "type=volume,source=" + evidence["project"] + "_data,target=/data", "--mount", "type=bind,source=" + str(token) + ",target=/run/updater-token,readonly", args.image, "python", "-c", script], 240)
        assert "restored" in output
        agent.compose(["start", "--wait", "--wait-timeout", "160", *SERVICES], 240)
        response = client.post("/auth/login", json={"username": "admin", "password": PASSWORD}); response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        assert client.get("/migrations/database/status").json()["active_database"] == "postgresql"
        client.get("/sessions").raise_for_status()
    result = {"passed": True, "packaged_image_without_source_mount": True, "generated_key_change_recovered": True, "database_target_preserved": True, "active_database": "postgresql"}
    (root / "key-verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
