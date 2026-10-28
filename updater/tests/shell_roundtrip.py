"""Real shell backup and rejection checks, ONLY on a passed, owned PG fixture."""
import argparse
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import uuid
from zipfile import ZipFile

from updater.tests.docker_isolation import IsolationError, require, validate_fixture
from updater.tests.postgresql_roundtrip import FixtureAgent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    marker, _ = validate_fixture(root)  # Must precede copies, mkdir or Docker.
    evidence = json.loads((root / "verification.json").read_text())
    require(evidence.get("passed") and evidence["project"] == marker["project"], "passed owned fixture required")
    agent = FixtureAgent(root, "LearnGraph/UpdateFixture", evidence["image"], ["compose.json"], timeout=100)
    require(not agent.active(), "maintenance must finish before shell acceptance")
    script = root / "scripts/docker-update.sh"
    script.parent.mkdir(exist_ok=True)
    shutil.copyfile(Path(__file__).resolve().parents[2] / "scripts/docker-update.sh", script)
    shutil.copyfile(root / "compose.json", root / "docker-compose.yml")
    bash = str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe") if os.name == "nt" else shutil.which("bash")
    require(bash and Path(bash).is_file(), "native Bash required; do not use WSL against host Docker")
    backup_dir = root / "backups" / ("shell-isolation-" + uuid.uuid4().hex)
    env = {**os.environ, "COMPOSE_PROJECT_NAME": "learngraph", "BACKUP_DIR": str(backup_dir), "WEB_PORT": str(marker["port"])}
    for key in ("LEARNGRAPH_EXPECTED_PROJECT", "LEARNGRAPH_DATA_DIR", "LEARNGRAPH_SANDBOX_OVERRIDE", "BASH_ENV", "ENV"):
        env.pop(key, None)
    # Every branch of the real shell script uses a second, mandatory Docker gate.
    # A missed service lookup in the product script cannot reach a formal target.
    gate = root / "scripts/docker-gate"
    gate.mkdir(exist_ok=True)
    real_docker = shutil.which("docker")
    require(real_docker and Path(real_docker).is_absolute(), "native Docker executable required")
    wrapper = gate / "docker"
    wrapper.write_text("#!/bin/bash\nexec " + shlex.quote(Path(sys.executable).as_posix()) + " -m updater.tests.shell_docker_gate \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    env.update(PATH=str(gate) + os.pathsep + os.environ["PATH"],
               PYTHONPATH=str(Path(__file__).resolve().parents[2]),
               LEARNGRAPH_SHELL_TEST_ROOT=str(root), LEARNGRAPH_TEST_REAL_DOCKER=real_docker,
               LEARNGRAPH_TEST_ORIGINAL_PATH=os.environ["PATH"], PYTHONDONTWRITEBYTECODE="1")
    results = []

    def run(environment, operation="--backup-only"):
        return subprocess.run([bash, str(script), operation], cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)

    def verify_cleanup():
        app = agent.containers()["app"]["Id"]
        state = json.loads(agent.command(["exec", app, "python", "-c", "import json; from pathlib import Path; print(json.dumps({'reserved':Path('/data/database-migration/update.json').exists(),'temporary_backups':[p.name for root in [Path('/tmp'),Path('/data/.backup-work')] for p in root.glob('learngraph-upgrade-*.zip')]}))"]))
        require(not state["reserved"] and not state["temporary_backups"], "shell left a maintenance reservation or temporary backup")

    rejected = run(env)
    require(rejected.returncode != 0 and "LEARNGRAPH_EXPECTED_PROJECT" in rejected.stderr, "missing expected project was not rejected")
    require(not backup_dir.exists(), "rejected command created a backup directory")
    results.append("missing project rejected before Docker maintenance")
    rejected = run({**env, "LEARNGRAPH_EXPECTED_PROJECT": "lg-pg-maintenance-" + uuid.uuid4().hex[:10]})
    require(rejected.returncode != 0 and "Docker test isolation" in rejected.stderr, "wrong project was not rejected by Docker boundary")
    require(not backup_dir.exists(), "wrong project created a backup directory")
    results.append("wrong project rejected before business entrypoint")
    fault_dir = root / "backups" / ("checksum-fault-" + uuid.uuid4().hex)
    fault_dir.mkdir(parents=True)
    checksum = fault_dir / "bash-env"
    checksum.write_bytes(b"sha256sum() { printf '%064d  -\\n' 0; }\nexport -f sha256sum\n")
    failed = run({**env, "LEARNGRAPH_EXPECTED_PROJECT": marker["project"], "BACKUP_DIR": str(fault_dir / "backup"), "BASH_ENV": checksum.as_posix()})
    (root / "shell-checksum-fault.log").write_text(failed.stdout + failed.stderr, encoding="utf-8")
    require(failed.returncode != 0 and "SHA-256 不一致" in failed.stderr, "checksum fault must reject the real backup")
    require(not list((fault_dir/'backup').glob('primary-*.backup.zip')), 'unverified host backup was left behind')
    verify_cleanup()
    results.append("injected checksum failure rejected; temporary native artifact and maintenance reservation both released")
    accepted = run({**env, "LEARNGRAPH_EXPECTED_PROJECT": marker["project"]})
    (root / "shell-backup.log").write_text(accepted.stdout + accepted.stderr, encoding="utf-8")
    require(accepted.returncode == 0, "owned shell backup failed; inspect private fixture log")
    archives = list(backup_dir.glob("primary-*.backup.zip"))
    require(len(archives) == 1 and bool(list(backup_dir.glob("*-volume.tar.gz"))), "native and volume backups both required")
    require(not list(backup_dir.glob("*-sandboxd-state.tar.gz")), "undeclared sandboxd must never be backed up")
    with ZipFile(archives[0]) as archive:
        require("database.postgresql.dump" in archive.namelist(), "backup must contain actual PostgreSQL dump")
    results.append("inherited production project variable overridden; owned PostgreSQL native backup and volume archive passed")

    app = agent.containers()["app"]["Id"]
    remote = "/tmp/isolation-copy-probe.txt"
    agent.command(["exec", app, "python", "-c", "from pathlib import Path; Path(" + repr(remote) + ").write_text('owned fixture')"])
    copied = root / "isolation-copy-probe.txt"
    agent.command(["cp", app + ":" + remote, str(copied)])
    require(copied.read_text() == "owned fixture", "owned copy failed")
    outside = root.parent / ("isolation-outside-" + uuid.uuid4().hex + ".txt")
    try:
        agent.command(["cp", app + ":" + remote, str(outside)])
    except IsolationError:
        require(not outside.exists(), "outside copy was written")
    else:
        raise IsolationError("Docker test isolation: outside copy unexpectedly accepted")
    agent.command(["exec", app, "python", "-c", "from pathlib import Path; Path(" + repr(remote) + ").unlink()"])
    results.append("owned container copy passed; copy escaping fixture root rejected")
    from updater.agent import SERVICES
    update_env = {**env,"LEARNGRAPH_EXPECTED_PROJECT":marker["project"],"UPDATE_TIMEOUT":"45"}
    before = agent.containers()["app"]["Id"]
    incompatible = run(update_env,marker["image"]+":v1.3.0")
    (root / "shell-incompatible-update.log").write_text(incompatible.stdout + incompatible.stderr,encoding="utf-8")
    require(incompatible.returncode != 0 and "禁止回退 SQLite" in incompatible.stderr and agent.containers()["app"]["Id"]==before,"shell capability rejection failed")
    verify_cleanup()
    results.append("shell refuses SQLite-only target before replacing any PostgreSQL business container")
    upgraded = run(update_env,marker["image"]+":v1.1.0")
    (root / "shell-successful-update.log").write_text(upgraded.stdout + upgraded.stderr,encoding="utf-8")
    require(upgraded.returncode == 0 and agent.current()["version"]=="v1.1.0","shell upgrade/readiness failed")
    verify_cleanup()
    results.append("shell real upgrade verified all three healthy business containers and identical PostgreSQL identity")
    failed_update = run(update_env,marker["image"]+":v1.2.0")
    (root / "shell-unhealthy-update.log").write_text(failed_update.stdout + failed_update.stderr,encoding="utf-8")
    require(failed_update.returncode != 0 and "原服务版本已通过健康检查" in failed_update.stderr and agent.current()["version"]=="v1.1.0","shell unhealthy-image rollback failed")
    verify_cleanup()
    results.append("shell unhealthy target rolled back all three services; PostgreSQL identity retained")
    fault = root / "scripts/compose-failure-env"
    (root / "scripts/compose-failure-used").unlink(missing_ok=True)
    fault.write_text('''docker() {
  case "$*" in
    *' up -d --no-deps --no-build app preview egress-proxy')
      if [ ! -f "$LEARNGRAPH_SHELL_TEST_ROOT/scripts/compose-failure-used" ]; then
        printf used > "$LEARNGRAPH_SHELL_TEST_ROOT/scripts/compose-failure-used"
        return 89
      fi ;;
  esac
  command docker "$@"
}
export -f docker
''',encoding="utf-8")
    apply_failed = run({**update_env,"BASH_ENV":fault.as_posix()},marker["image"]+":v1.0.0")
    (root / "shell-compose-failure-update.log").write_text(apply_failed.stdout + apply_failed.stderr,encoding="utf-8")
    require(apply_failed.returncode != 0 and "原服务版本已通过健康检查" in apply_failed.stderr and agent.current()["version"]=="v1.1.0","shell replacement-command failure did not recover original services")
    verify_cleanup()
    results.append("injected Compose replacement failure recovered original healthy services and released reservation")
    baseline = agent.image_info(marker["image"]+":v1.0.0")["Id"]
    agent.replace(dict.fromkeys(SERVICES,baseline))
    require(not agent.active(), "shell maintenance reservation was not released")
    verify_cleanup()
    agent.api_url()  # Recheck actual HTTP binding, resource ownership and mounts.
    result = {"passed": True, "project": marker["project"], "checks": results, "backup_directory": str(backup_dir.relative_to(root)), "production_mutations": 0}
    (root / "shell-isolation-verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
