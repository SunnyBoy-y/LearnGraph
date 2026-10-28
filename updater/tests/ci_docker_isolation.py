"""CI smoke is allowed only on a fresh GitHub-hosted Docker daemon.

Run BEFORE Buildx, builds, secrets creation, Compose or cleanup traps. Local
replays/self-hosted runners must use the owned fixture entrypoints instead.
"""
import os
from pathlib import Path
import subprocess

from updater.tests.docker_isolation import require


def inspect_daemon(args):
    return subprocess.check_output(["docker",*args],text=True,encoding="utf-8",timeout=30).strip()


def require_fresh_daemon(root, *, environment=None, inspect=inspect_daemon):
    environment=os.environ if environment is None else environment
    require(environment.get("GITHUB_ACTIONS")=="true" and environment.get("RUNNER_ENVIRONMENT")=="github-hosted" and not environment.get("ACT"), "CI Docker smoke refuses local/self-hosted production daemons")
    require(not (Path(root)/".env").exists(),"CI may not inherit a deployment .env")
    require(environment.get("DOCKER_HOST","") in {"","unix:///var/run/docker.sock"} and environment.get("DOCKER_CONTEXT","") in {"","default"},"remote/alternate Docker daemon rejected")
    require(inspect(["context","inspect","--format","{{.Endpoints.docker.Host}}"])=="unix:///var/run/docker.sock","CI requires its local ephemeral daemon")
    require(not inspect(["ps","-aq"]),"CI daemon already has containers; production/foreign targets rejected")
    require(not inspect(["volume","ls","--format","{{.Name}}"]),"CI daemon already has volumes; production data rejected")
    networks=set(inspect(["network","ls","--format","{{.Name}}"]).splitlines())
    require(networks <= {"bridge","host","none"},"CI daemon has existing custom/production networks")


if __name__ == "__main__":
    require_fresh_daemon(Path.cwd())
    print("Fresh GitHub-hosted Docker daemon verified before any mutation")
