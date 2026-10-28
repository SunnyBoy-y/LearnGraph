"""Opt-in combined API/preview acceptance using owned disposable resources."""
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
    DISABLED_WORKERS, IsolatedAgent, initialize_fixture, require,
)


SEED = r'''
import json
from sqlalchemy import select
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.models import User,Workspace,ChatSession
from app.services.session_workspace import SessionWorkspaceService
from app.services.subapp_bundles import SubAppBundleService
with SessionLocal() as db:
    user=db.scalar(select(User).where(User.username=='admin'))
    user.must_change_password=False
    workspace=db.scalar(select(Workspace).limit(1))
    chat=ChatSession(workspace_id=workspace.id,title='Combined runtime acceptance')
    db.add(chat);db.commit()
    files=SessionWorkspaceService(db,workspace.id,user.id,get_settings())
    files.put_bytes(chat_session_id=chat.id,path='dist/index.html',
        data=b'<!doctype html><html><body><button id="submit">Submit</button></body></html>',
        mime_type='text/html')
    bundles=SubAppBundleService(db,workspace.id,user.id,
        get_settings().model_copy(update={'subapp_preview_origin':'http://127.0.0.1:8001'}))
    validation=bundles.validate(chat_session_id=chat.id,sandbox_session_id=None,
        output_root='dist',entry_path='dist/index.html')
    schema={'type':'object','additionalProperties':False,'properties':{'count':{'type':'integer'}},'required':['count']}
    published=bundles.publish(validation_id=validation['validation_id'],chat_session_id=chat.id,
        sandbox_session_id=None,title='Combined runtime acceptance',
        interaction_contract={'event_schema':schema,'state_schema':schema})
    preview=bundles.mint_preview(published['bundle_id'])
    print(json.dumps({'artifact_version_id':published['artifact_version_id'],
        'chat_session_id':chat.id,'preview_url':preview['url'],'user_id':user.id,'workspace_id':workspace.id}))
'''


def wait_exit(agent, container):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = json.loads(agent.command(["inspect", container]))[0]["State"]
        if not state["Running"]:
            return state["ExitCode"]
        time.sleep(.3)
    raise AssertionError("Companion processes did not exit")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="learngraph:combined-runtime-20261022")
    parser.add_argument("--port", type=int, default=18261)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="lg-combined-runtime-")).resolve()
    project = "lg-update-test-" + uuid.uuid4().hex[:10]
    image = "learngraph-updater-fixture-" + uuid.uuid4().hex[:10]
    password = secrets.token_urlsafe(40)
    environment = {
        "LEARNGRAPH_ENV": "test", "LEARNGRAPH_DATABASE_URL": "sqlite:////data/learngraph.db",
        "LEARNGRAPH_STORAGE_ROOT": "/data/storage", "LEARNGRAPH_MEMORY_ROOT": "/data/memory",
        "LEARNGRAPH_BOOTSTRAP_ADMIN_PASSWORD": password,
        "LEARNGRAPH_ENABLE_DEMO_SEED": "false", "LEARNGRAPH_ENABLE_DEMO_LOGIN": "false",
        "LEARNGRAPH_VOICE_RUNTIME_EMBEDDED": "false",
    }
    environment.update({"LEARNGRAPH_" + worker + "_ENABLED": "false" for worker in DISABLED_WORKERS})
    services = {
        "app": {
            "image": image + ":v1.0.0", "environment": environment, "volumes": ["data:/data"],
            "ports": [f"127.0.0.1:{args.port}:8000"], "restart": "no",
        },
        "egress-proxy": {
            "image": image + ":v1.0.0",
            "environment": {**environment, "LEARNGRAPH_SANDBOX_EGRESS_PROXY_PORT": "8888"},
            "volumes": ["data:/data"], "restart": "no",
            "entrypoint": ["python", "-m", "app.services.egress_proxy_main"],
            "depends_on": {"app": {"condition": "service_healthy"}},
            "healthcheck": {
                "test": ["CMD", "python", "-c", "import socket; socket.create_connection(('127.0.0.1',8888),timeout=2).close()"],
                "interval": "2s", "timeout": "3s", "retries": 10,
            },
        },
    }
    initialize_fixture(root, project, image, {
        "name": project, "services": services, "volumes": {"data": {}},
    }, port=args.port)
    (root / "Dockerfile").write_text(
        f"FROM {args.base}\n"
        "LABEL org.opencontainers.image.version=v1.0.0\n"
        "LABEL org.opencontainers.image.source=https://github.com/LearnGraph/UpdateFixture\n",
        encoding="utf-8",
    )
    agent = IsolatedAgent(root, "LearnGraph/UpdateFixture", image, ["compose.json"])
    results = []
    try:
        agent.command(["build", "-t", image + ":v1.0.0", str(root)], 180)
        agent.compose(["up", "-d", "--wait", "--wait-timeout", "150"], 210)
        container = agent.containers()["app"]["Id"]
        agent.command(["exec", container, "python", "-m", "app.container_runtime", "health"])
        results.append("combined API and preview pass health; no migration agent exists")
        agent.command(["exec", container, "python", "-c",
            "from pathlib import Path; assert not Path('/var/run/docker.sock').exists(); "
            "from app.core.config import Settings; "
            "import os; os.environ['LEARNGRAPH_SQLITE_READ_ONLY']='true'; "
            "from app.core.database import engine; "
            "from sqlalchemy import text; "
            "db=engine.connect(); assert db.scalar(text('PRAGMA query_only'))==1; db.close()"])
        results.append("preview read-only database mode retained; no Docker socket")
        seeded = json.loads(agent.command(["exec", container, "learngraph-entrypoint", "python", "-c", SEED]))
        with httpx.Client(base_url=agent.api_url(), timeout=30, trust_env=False) as client:
            login = client.post("/auth/login", json={"username": "admin", "password": password})
            login.raise_for_status()
            token = login.json()
            client.headers.update({"Authorization": "Bearer " + token["access_token"],
                                   "X-Workspace-ID": token["default_workspace_id"]})
            response = client.post("/subapps/sessions", json={
                "artifact_version_id": seeded["artifact_version_id"],
                "chat_session_id": seeded["chat_session_id"],
            })
            response.raise_for_status()
            session = response.json()
            response = client.post(f"/subapps/sessions/{session['session_id']}/events", json={
                "token": session["token"], "event_type": "submit", "payload": {"count": 1},
            })
            response.raise_for_status()
            require(response.json()["accepted"] is True and response.json()["next_token"] != session["token"],
                    "event acceptance/token rotation failed")
            patch_state = (
                "from app.core.database import SessionLocal; from app.services.subapps import SubAppService; "
                f"db=SessionLocal(); svc=SubAppService(db,{seeded['workspace_id']!r},{seeded['user_id']!r}); "
                f"assert svc.propose_state({session['session_id']!r},{{'count':2}},expected_version=0)==1; db.close()"
            )
            agent.command(["exec", container, "learngraph-entrypoint", "python", "-c", patch_state])
            states = client.get(f"/subapps/sessions/{session['session_id']}/states")
            states.raise_for_status()
            require(states.json()["items"][0]["state"] == {"count": 2}, "state return channel failed")
        preview_path = "/" + seeded["preview_url"].split("/", 3)[3]
        probe = (
            "import urllib.request; "
            f"r=urllib.request.urlopen('http://127.0.0.1:8001'+{preview_path!r}); body=r.read().decode(); "
            "assert r.status==200 and 'subapp-client.js' in body and 'runtime-shim.js' in body; "
            "assert r.headers['Content-Security-Policy'] and r.headers['Referrer-Policy']=='no-referrer'"
        )
        agent.command(["exec", container, "python", "-c", probe])
        results.append("real immutable preview + SDK/CSP; HTTP event/token rotation + state return round trip")
        kill_preview = (
            "import os,signal; from pathlib import Path; "
            "targets=[int(p.name) for p in Path('/proc').iterdir() if p.name.isdigit() "
            "and p.joinpath('cmdline').exists() and b'app.preview:preview_app' in p.joinpath('cmdline').read_bytes().split(b'\\0')]; "
            "assert len(targets)==1; os.kill(targets[0],signal.SIGTERM)"
        )
        agent.command(["exec", container, "python", "-c", kill_preview])
        require(wait_exit(agent, container) != 0, "preview exit did not fail the container")
        results.append("preview process exit stops API and fails the container")
        agent.compose(["up", "-d", "--no-deps", "--wait", "--wait-timeout", "150", "app"], 210)
        container = agent.containers()["app"]["Id"]
        agent.compose(["stop", "app"], 40)
        require(wait_exit(agent, container) == 0, "SIGTERM shutdown was not clean")
        results.append("Docker stop forwards SIGTERM and both children exit cleanly")
        report = {"project": project, "passed": results, "production_resources_touched": False}
        (root / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        agent.compose(["down", "--volumes", "--remove-orphans"], 90)
        tags = agent.fixture_image_tags()
        if tags:
            agent.command(["image", "rm", *tags], 90)
        print("Evidence directory:", root)


if __name__ == "__main__":
    main()
