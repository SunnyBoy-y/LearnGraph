"""Fail closed before any acceptance-fixture write or Docker mutation.

This is deliberately separate from the production updater. A project prefix
alone is not ownership: the root manifest, Compose definition, Docker labels,
and every mount (including read-only mounts) must agree. Never adopt an
existing unmarked deployment. Unknown Docker/Compose options are rejected.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import uuid

from updater.agent import Agent, SERVICES as UPDATE_SERVICES

OWNER = "io.learngraph.acceptance-owner"
MARKER = ".docker-test-isolation.json"
PROJECT = re.compile(r"lg-(?:pg-maintenance|update-test)-[0-9a-f]{10}\Z")
IMAGE = re.compile(r"learngraph-(?:maintenance|updater)-fixture-[0-9a-f]{10}\Z")
SERVICES = {"app", "preview", "egress-proxy", "database-migration-agent", "sandboxd"}
SERVICE_KEYS = {"image", "environment", "volumes", "ports", "labels", "restart", "extra_hosts", "depends_on", "command", "entrypoint", "healthcheck", "mem_limit", "profiles"}
DISABLED_WORKERS = {"MODELS_DEV_REFRESH", "DURABLE_QUEUE", "MASTERY_EMBEDDED_SCHEDULER", "MEMORY_RETENTION_SCHEDULER", "MEMORY_EXTRACTION_SCHEDULER", "MEMORY_OUTBOX_WORKER", "SANDBOX", "SANDBOX_CLEANUP_SCHEDULER", "SANDBOX_EXECUTION_SCHEDULER", "MCP_STDIO_CLEANUP_SCHEDULER", "VOICE_TURN_SWEEP", "VOICE_SESSION_REAPER", "VOICE_RUNTIME_EMBEDDED"}
FIXTURE_ENV = {"LEARNGRAPH_"+name+"_ENABLED" for name in DISABLED_WORKERS} | {
    "LEARNGRAPH_ENV", "LEARNGRAPH_DATABASE_URL", "LEARNGRAPH_DATA_ROOT", "LEARNGRAPH_STORAGE_ROOT", "LEARNGRAPH_MEMORY_ROOT", "LEARNGRAPH_BOOTSTRAP_ADMIN_PASSWORD", "LEARNGRAPH_ENABLE_DEMO_SEED", "LEARNGRAPH_ENABLE_DEMO_LOGIN", "LEARNGRAPH_DATABASE_MIGRATION_AGENT_URL", "LEARNGRAPH_DATABASE_MIGRATION_AGENT_TOKEN_FILE", "LEARNGRAPH_DATABASE_MIGRATION_STATE_DIR", "LEARNGRAPH_DATABASE_DOCKER_NETWORK", "LEARNGRAPH_UPDATE_AGENT_URL", "LEARNGRAPH_UPDATE_AGENT_TOKEN_FILE", "LEARNGRAPH_SQLITE_READ_ONLY", "LEARNGRAPH_SANDBOX_EGRESS_PROXY_PORT", "LEARNGRAPH_VOICE_RUNTIME_EMBEDDED"}
LIFECYCLE_APP_ENV = {
    "LEARNGRAPH_DATABASE_MIGRATION_AGENT_AUTO_START": "true",
    "LEARNGRAPH_SANDBOXD_URL": "http://sandboxd:8090",
    "LEARNGRAPH_SANDBOXD_TOKEN_FILE": "/data/sandboxd/sandboxd-token",
    "LEARNGRAPH_SANDBOXD_ADMIN_TOKEN_FILE": "/data/sandboxd/sandboxd-admin-token",
}
LIFECYCLE_DAEMON_ENV = {
    "SANDBOXD_LISTEN_HOST": "0.0.0.0", "SANDBOXD_PORT": "8090",
    "SANDBOXD_TOKEN_FILE": "/data/sandboxd-token", "SANDBOXD_ADMIN_TOKEN_FILE": "/data/sandboxd-admin-token",
    "SANDBOXD_STATE_PATH": "/data/state.db", "SANDBOXD_EGRESS_ENABLED": "false",
    "SANDBOXD_MANAGED_DATABASE_ENABLED": "true", "SANDBOXD_MIGRATION_AGENT_IDLE_SECONDS": "30",
}


def host_path(value):
    """Compare Docker Desktop's Linux host paths with native Windows paths."""
    value = str(value).replace("\\", "/")
    if value == "/run/host-services/docker.proxy.sock":
        return "/var/run/docker.sock"  # Docker Desktop's fixed socket proxy.
    for prefix in ("/run/desktop/mnt/host/", "/host_mnt/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            if len(value) > 1 and value[1] == "/":
                value = value[0] + ":" + value[1:]
    return value.casefold() if re.match(r"^[A-Za-z]:/", value) else value


def fixture_image(value, marker):
    return bool(re.fullmatch(re.escape(marker["image"]) + r":[A-Za-z0-9_.-]+", value or ""))


def declared_mounts(root, marker, config, service):
    repo = Path(__file__).resolve().parents[2]
    binds = {"/app/app": repo / "backend/app", "/app/frontend-dist": repo / "frontend/dist", "/run/updater-token": root / "data/updater/token"}
    result = {}
    for mount in config["services"][service].get("volumes", []):
        if isinstance(mount, str):
            parts = mount.split(":")
            if mount == "/var/run/docker.sock:/var/run/docker.sock":
                require(service in {"database-migration-agent", "sandboxd"}, "Docker socket only belongs to the fixture control planes")
                item = {"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock", "RW": True}
            else:
                shared_tokens = service == "app" and parts == ["state", "/data/sandboxd", "ro"] and "sandboxd" in config["services"]
                require(shared_tokens or len(parts) in {2, 3} and parts[0] in config["volumes"] and parts[1] == "/data" and (len(parts) == 2 or parts[2] == "ro"), "unexpected volume source/target/options")
                item = {"Type": "volume", "Name": marker["project"] + "_" + parts[0], "Destination": parts[1], "RW": len(parts) == 2}
        else:
            require(isinstance(mount, dict) and set(mount) == {"type", "source", "target", "read_only"}, "unsupported mount directive")
            target = mount["target"]
            require(target in binds and mount["type"] == "bind" and mount["read_only"] is True and Path(mount["source"]).resolve() == binds[target].resolve(), "unapproved bind source/target")
            item = {"Type": "bind", "Source": host_path(binds[target].resolve()), "Destination": target, "RW": False}
        require(item["Destination"] not in result, "duplicate mount destination")
        result[item["Destination"]] = item
    require("/data" in result and result["/data"]["Type"] == "volume", "private main data volume missing")
    return result


class IsolationError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise IsolationError("Docker test isolation: " + message)


def validate_location(root):
    root = Path(root).resolve()
    repo = Path(__file__).resolve().parents[2]
    # Reject before mkdir as well as before Docker. In-container /data is the
    # production persistence mount; host Docker volumes are never test roots.
    forbidden = [repo, repo / "data", repo / "backend/data", Path("/data").resolve(), Path("/var/lib/docker").resolve()]
    require(root != repo and all(not root.is_relative_to(path) for path in forbidden[1:]), "production data directory cannot be a fixture root")
    return root


def contained_state(root):
    for relative in (MARKER, "compose.json", "docker-compose.yml", "docker-compose.update.json", "verification.json", "Dockerfile", "docker-last-failure.log", "pytest-wheels", "scripts", "scripts/docker-update.sh", "scripts/docker-gate", "backups", "data", "data/updater", "data/updater/token", "data/updater/state.json", "data/updater/private"):
        require((root / relative).resolve().is_relative_to(root), "symlink/junction escapes fixture root")


def initialize_fixture(root: Path, project: str, image: str, config: dict, *, port=None):
    """Create a new ownership manifest; never retrofit labels onto existing stacks."""
    root = validate_location(root)
    contained_state(root)
    require(PROJECT.fullmatch(project) and IMAGE.fullmatch(image), "invalid fixture namespace")
    require(not (root / MARKER).exists() and not (root / "compose.json").exists(), "fixture already exists")
    require(not (root / ".git").exists() and not (root / "backend").exists(), "repository root is forbidden")
    owner = uuid.uuid4().hex
    for service in config["services"].values():
        service.setdefault("labels", {})[OWNER] = owner
    for volume in config.get("volumes", {}).values():
        volume.setdefault("labels", {})[OWNER] = owner
    config["networks"] = {"default": {"labels": {OWNER: owner}}}
    (root / "compose.json").write_text(json.dumps(config), encoding="utf-8")
    marker = {"version": 1, "root": str(root), "project": project, "image": image, "owner": owner, "port": port}
    (root / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    validate_fixture(root)
    return marker


def validate_fixture(root: Path):
    root = validate_location(root)
    contained_state(root)
    try:
        marker = json.loads((root / MARKER).read_text(encoding="utf-8"))
        config = json.loads((root / "compose.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IsolationError("Docker test isolation: explicit fixture manifest and Compose JSON required") from exc
    require(marker.get("version") == 1 and Path(marker.get("root", "")).resolve() == root, "fixture root mismatch")
    require(PROJECT.fullmatch(marker.get("project", "")), "production/non-fixture project rejected")
    require(IMAGE.fullmatch(marker.get("image", "")), "non-fixture image rejected")
    require(re.fullmatch(r"[0-9a-f]{32}", marker.get("owner", "")), "owner token missing")
    require(config.get("name") == marker["project"], "Compose name differs from manifest")
    require(not (root / ".git").exists() and not (root / "backend").exists(), "repository root is forbidden")
    require(set(config) <= {"name", "services", "volumes", "networks"}, "unsupported Compose extension")
    volumes = config.get("volumes", {})
    require(bool(volumes), "private data volume required")
    for name, definition in volumes.items():
        require(re.fullmatch(r"[a-z0-9-]+", name), "unsafe volume name")
        require(set(definition) == {"labels"} and definition["labels"].get(OWNER) == marker["owner"], "external/shared volume rejected")
    require(config.get("networks") == {"default": {"labels": {OWNER: marker["owner"]}}}, "shared/external network rejected")
    require(isinstance(config.get("services"), dict) and "app" in config["services"], "app service required")
    for name, service in config["services"].items():
        require(name in SERVICES and set(service) <= SERVICE_KEYS, "unexpected service/directive")
        require("profiles" not in service or name == "database-migration-agent" and service["profiles"] == ["database-migration"], "unapproved optional service profile")
        require("mem_limit" not in service or name == "app" and service["mem_limit"] == "1g", "only the fixed 1 GiB application acceptance limit is allowed")
        require(service.get("labels", {}).get(OWNER) == marker["owner"], "service ownership label missing")
        require(fixture_image(service.get("image"), marker), "foreign fixture image")
        environment = service.get("environment", {})
        require(isinstance(environment, dict) and all(isinstance(v, str) and "${" not in v for v in environment.values()), "interpolated/non-literal fixture environment")
        if name == "sandboxd":
            expected = {**LIFECYCLE_DAEMON_ENV, "SANDBOXD_DEPLOYMENT_ID": marker["project"]}
            require(environment == expected and service.get("volumes") == ["state:/data", "/var/run/docker.sock:/var/run/docker.sock"], "sandboxd fixture must use the fixed private lifecycle configuration")
        else:
            extra = set(environment) - FIXTURE_ENV
            permitted = {**LIFECYCLE_APP_ENV, "LEARNGRAPH_SANDBOXD_DEPLOYMENT_ID": marker["project"]} if name == "app" and "sandboxd" in config["services"] else {"LEARNGRAPH_MASTER_KEY": ""} if name == "database-migration-agent" else {}
            require(extra <= set(permitted) and all(environment[k] == permitted[k] for k in extra), "unknown fixture environment, including forged HOSTNAME, rejected")
        require(all(environment.get("LEARNGRAPH_"+worker+"_ENABLED","false")=="false" for worker in DISABLED_WORKERS), "unrelated background workers must stay disabled in migration acceptance")
        require(environment.get("LEARNGRAPH_ENV","test")=="test", "production-mode fixture refused")
        require(environment.get("LEARNGRAPH_DATABASE_DOCKER_NETWORK",marker["project"]+"_default")==marker["project"]+"_default", "foreign provisioner network")
        require(not any(k.startswith(("DOCKER_", "COMPOSE_", "PG")) for k in environment), "foreign Docker/libpq environment")
        for field, expected in {"LEARNGRAPH_DATABASE_MIGRATION_STATE_DIR":"/data/database-migration", "LEARNGRAPH_DATABASE_MIGRATION_AGENT_TOKEN_FILE":"/data/database-migration-agent/token", "LEARNGRAPH_UPDATE_AGENT_TOKEN_FILE":"/run/updater-token", "LEARNGRAPH_STORAGE_ROOT":"/data/storage", "LEARNGRAPH_MEMORY_ROOT":"/data/memory"}.items():
            require(field not in environment or environment[field] == expected, "foreign fixture state/credential path")
        bootstrap_url = environment.get("LEARNGRAPH_DATABASE_URL", "sqlite:////data/learngraph.db" if name in {"database-migration-agent", "sandboxd"} else None)
        require(bootstrap_url == "sqlite:////data/learngraph.db", "fixture bootstrap must use its own SQLite volume")
        require(environment.get("LEARNGRAPH_DATA_ROOT", "/data") == "/data", "foreign data root")
        if "LEARNGRAPH_DATABASE_MIGRATION_AGENT_URL" in environment:
            require(environment["LEARNGRAPH_DATABASE_MIGRATION_AGENT_URL"] == "http://database-migration-agent:8092", "foreign database provisioner")
        declared_mounts(root, marker, config, name)
        require(set(service.get("depends_on", {})) <= set(config["services"]), "foreign dependency service")
        require(service.get("extra_hosts", []) in ([], ["host.docker.internal:host-gateway"]), "foreign host mapping")
        for binding in service.get("ports", []):
            require(name == "app" and binding == f"127.0.0.1:{marker['port']}:8000" and marker["port"] not in {None, 18000}, "production/public port rejected")
    override = root / "docker-compose.update.json"
    if override.exists():
        change = json.loads(override.read_text(encoding="utf-8"))
        require(set(change) == {"services"}, "override may only select images")
        for name, service in change["services"].items():
            require(name in config["services"] and set(service) == {"image"}, "override changes isolation")
            value = service["image"]
            require(fixture_image(value, marker) or bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value or "")), "foreign override image")
    return marker, config


class IsolatedAgent(Agent):
    def __init__(self, root, repository, image, *args, **kwargs):
        marker, _ = validate_fixture(Path(root))  # Before Agent mkdir/chmod/token creation.
        require(image == marker["image"], "agent image mismatch")
        super().__init__(root, repository, image, *args, **kwargs)
        self.verify_isolation()

    def _raw(self, args, timeout=60, *, validated=False):
        # Even private callers cannot accidentally bypass the mutation gate.
        # Only the guarded operation methods below may mark a command checked.
        read_only = bool(args) and (args[0] in {"ps", "inspect"} or len(args)>1 and args[0] in {"image", "volume", "network"} and args[1] in {"inspect", "ls"})
        if args and args[0] == "compose":
            position = 1
            while position < len(args) and args[position] in {"--project-name", "-f"}:
                position += 2
            read_only = position < len(args) and args[position] in {"version", "ps", "config"}
        require(read_only or validated, "raw Docker mutation requires an explicitly validated operation")
        return super().command(args, timeout)

    def command_environment(self):
        # Compose must never load an inherited file, profile, env-file or project.
        return {k: v for k, v in super().command_environment().items() if not k.startswith(("COMPOSE_", "LEARNGRAPH_"))}

    def command_failed(self, result):
        validate_fixture(self.root)
        path = self.root / "docker-last-failure.log"
        require(path.resolve().is_relative_to(self.root), "diagnostic path escapes fixture")
        path.write_text(result.stdout + result.stderr, encoding="utf-8")
        path.chmod(0o600)  # Private fixture diagnostics, never an HTTP response.

    def verify_isolation(self):
        marker, config = validate_fixture(self.root)
        project, owner = marker["project"], marker["owner"]
        resources = set()
        ids = self._raw(["ps", "-aq", "--filter", "label=com.docker.compose.project=" + project]).splitlines()
        if ids:
            for container in json.loads(self._raw(["inspect", *ids])):
                self._verify_container(container, marker, resources)
        for kind, names in [("volume", [project + "_" + n for n in config["volumes"]]), ("network", [project + "_default"])]:
            existing = self._raw([kind, "ls", "--format", "{{.Name}}"]).splitlines()
            for name in names:
                if name in existing:
                    resource = json.loads(self._raw([kind, "inspect", name]))[0]
                    require(resource.get("Labels", {}).get(OWNER) == owner and resource["Labels"].get("com.docker.compose.project") == project, "existing resource belongs to another deployment")
        return marker

    def _verify_container(self, container, marker, resources=None, *, verify_resources=True):
        _, config = validate_fixture(self.root)
        labels = container["Config"].get("Labels", {})
        require(labels.get(OWNER) == marker["owner"] and labels.get("com.docker.compose.project") == marker["project"], "container ownership mismatch")
        require(Path(labels.get("com.docker.compose.project.working_dir", "")).resolve() == self.root, "container working directory mismatch")
        service = labels.get("com.docker.compose.service")
        require(service in config["services"], "undeclared container service")
        if config["services"][service].get("mem_limit") == "1g":
            require(container.get("HostConfig", {}).get("Memory") == 1024**3, "declared application memory limit is not enforced")
        require(not container.get("Name", "").startswith("/learngraph-"), "production container name rejected")
        expected = declared_mounts(self.root, marker, config, service)
        require(len(container["Mounts"]) == len(expected), "unexpected container mount count")
        for mount in container["Mounts"]:
            wanted = expected.get(mount["Destination"])
            require(wanted is not None and all((host_path(mount.get(k, "")) if k == "Source" else mount.get(k)) == v for k, v in wanted.items()), "foreign container mount, including read-only mount")
            if verify_resources and mount["Type"] == "volume":
                if resources is None or mount["Name"] not in resources:
                    self.verify_volume(mount["Name"], marker)
                    if resources is not None: resources.add(mount["Name"])
        networks = container.get("NetworkSettings", {}).get("Networks", {})
        require(set(networks) == {marker["project"] + "_default"}, "foreign/additional container network")
        if verify_resources and (resources is None or marker["project"] + "_default" not in resources):
            self.verify_network(marker)
            if resources is not None: resources.add(marker["project"] + "_default")
        actual_environment = dict(value.split("=", 1) for value in container["Config"].get("Env", []) if "=" in value)
        require(not any(key.startswith(("DOCKER_","COMPOSE_")) for key in actual_environment), "image injects a foreign Docker endpoint")
        require("HOSTNAME" not in actual_environment or actual_environment["HOSTNAME"]==container["Config"].get("Hostname"), "image forges Docker provisioner identity")
        require(all(actual_environment.get(k) == v for k, v in config["services"][service].get("environment", {}).items()), "container environment differs from isolated declaration")
        image = container["Image"]
        if verify_resources and (resources is None or image not in resources):
            self.verify_image(image, marker)
            if resources is not None: resources.add(image)

    def verify_image(self, image, marker):
        item = json.loads(self._raw(["image", "inspect", image]))[0]
        tags = item.get("RepoTags") or []
        require(any(fixture_image(tag, marker) for tag in tags) and all(fixture_image(tag, marker) or re.fullmatch(r"learngraph-retained:[0-9a-f]{24}(?:-target)?-(?:app|preview|egress-proxy)", tag) for tag in tags), "image is shared with a foreign/production tag")

        return item

    def verify_volume(self, name, marker=None):
        marker, config = validate_fixture(self.root) if marker is None else (marker, validate_fixture(self.root)[1])
        require(name in {marker["project"] + "_" + n for n in config["volumes"]}, "foreign/production volume rejected, including read-only access")
        item = json.loads(self._raw(["volume", "inspect", name]))[0]
        labels = item.get("Labels") or {}
        require(labels.get(OWNER) == marker["owner"] and labels.get("com.docker.compose.project") == marker["project"], "volume ownership mismatch")

        return item

    def verify_network(self, marker):
        item = json.loads(self._raw(["network", "inspect", marker["project"] + "_default"]))[0]
        labels = item.get("Labels") or {}
        require(labels.get(OWNER) == marker["owner"] and labels.get("com.docker.compose.project") == marker["project"], "network ownership mismatch")

    def _verify_target(self, target, marker, *, verify_resources=True):
        item = json.loads(self._raw(["inspect", target]))[0]
        labels = item["Config"].get("Labels", {})
        if labels.get(OWNER) == marker["owner"]:
            self._verify_container(item, marker, verify_resources=verify_resources)
            return item
        # Managed PostgreSQL uses a job label, not Compose service labels.
        job = labels.get("io.learngraph.primary-migration")
        require(bool(re.fullmatch(r"[0-9a-f]{32}", job or "")) and item["Name"] == "/learngraph-main-postgres-" + job, "unowned Docker target")
        evidence = json.loads((self.root / "verification.json").read_text())
        require(evidence.get("project") == marker["project"] and evidence.get("postgres_container") == item["Name"].lstrip("/"), "PostgreSQL job was not recorded by this fixture")
        require(set(item["NetworkSettings"]["Networks"]) == {marker["project"] + "_default"}, "PostgreSQL belongs to another fixture network")
        if verify_resources: self.verify_network(marker)
        volumes = item["Mounts"]
        require(len(volumes) == 1 and volumes[0].get("Type") == "volume" and volumes[0].get("RW") is True and volumes[0].get("Destination") == "/var/lib/postgresql" and volumes[0].get("Name") == item["Name"].lstrip("/") + "-data", "unexpected PostgreSQL mount")
        if verify_resources:
            volume = json.loads(self._raw(["volume", "inspect", volumes[0]["Name"]]))[0]
            require(volume.get("Labels", {}).get("io.learngraph.primary-migration") == job, "PostgreSQL volume ownership mismatch")
        return item

    def services(self):
        declared = set(self.compose(["config", "--services"]).splitlines())
        require({"app", "egress-proxy"} <= declared, "business services missing")
        return tuple(service for service in UPDATE_SERVICES if service in declared)

    def compose(self, args, timeout=60):
        marker, config = validate_fixture(self.root)
        require(bool(args) and args[0] in {"ps", "config", "up", "down", "stop", "start", "run"}, "unsupported Compose operation")
        require(not any(a in args for a in ("--project-name", "-p", "--file", "-f", "--project-directory")), "Compose scope override rejected")
        if args[0] == "run":
            require(len(args) == 7 and args[:6] == ["run", "--rm", "--no-deps", "app", "python", "-c"], "one-off Compose container must use the declared app mounts")
        else:
            flags = {"ps": {"--all", "--quiet", "-q"}, "config": {"--hash", "--services"}, "up": {"-d", "--no-deps", "--no-build", "--wait", "--wait-timeout", "--pull"}, "down": {"--remove-orphans", "--volumes"}, "stop": set(), "start": {"--wait", "--wait-timeout"}}[args[0]]
            position = 1
            while position < len(args):
                value = args[position]
                if value in {"--wait-timeout", "--pull", "--hash"}:
                    require(value in flags and position + 1 < len(args), "invalid Compose argument")
                    parameter = args[position + 1]
                    require((parameter.isdigit() and 0 < int(parameter) <= 300) if value == "--wait-timeout" else parameter == "never" if value == "--pull" else set(parameter.split(",")) <= set(config["services"]), "unsafe Compose option value")
                    position += 2
                    continue
                require(value in flags or (args[0] != "down" and value in config["services"]), "unknown Compose option/service")
                position += 1
        files = [part for name in self.compose_files for part in ("-f", name)]
        require(self.compose_files == ["compose.json"], "unexpected Compose file")
        if self.override.exists():
            files += ["-f", str(self.override)]
        if args[0] not in {"ps", "config"}:
            self.verify_isolation()
            if self.override.exists():
                for selected in json.loads(self.override.read_text())["services"].values():
                    self.verify_image(selected["image"], marker)
        # Explicit project always wins over inherited COMPOSE_PROJECT_NAME.
        return self._raw(["compose", "--project-name", marker["project"], *files, *args], timeout, validated=args[0] not in {"ps", "config"})

    def command(self, args, timeout=60):
        marker, _ = validate_fixture(self.root)
        require(bool(args) and all(isinstance(a, str) for a in args), "Docker arguments missing")
        action = args[0]
        if action == "compose":
            raise IsolationError("Docker test isolation: use guarded compose()")
        if action == "pull":
            require(args[1].startswith(marker["image"] + ":"), "remote/foreign release rejected")
            self.image_info(args[1])  # Only local fixture releases.
            return ""
        if action in {"exec", "cp", "rm", "inspect", "stop"}:
            require(len(args) >= 2 and (action != "exec" or not args[1].startswith("-")), "Docker target/options rejected")
            if action == "cp":
                require(len(args) == 3 and not any(v.startswith("-") for v in args[1:]), "unsupported copy option")
            if action == "rm":
                require(all(not a.startswith("-") or a == "-f" for a in args[1:]), "unsupported removal option")
            if action == "inspect":
                require(len(args) == 2 and not args[1].startswith("-"), "unsupported inspect option")
            if action == "stop":
                require(len(args) == 2 and not args[1].startswith("-"), "unsupported stop option")
            if action != "inspect":
                self.verify_isolation()
            targets = ([args[1]] if action == "exec" else [s.split(":", 1)[0] for s in args[1:] if not s.startswith("-")])
            if action == "cp":
                targets = [s.split(":", 1)[0] for s in args[1:] if ":" in s and not re.match(r"^[A-Za-z]:", s)]
                local_paths = [s for s in args[1:] if ":" not in s or re.match(r"^[A-Za-z]:", s)]
                require(all(Path(p).resolve().is_relative_to(self.root) for p in local_paths), "copy destination/source escapes fixture root")
            require(bool(targets), "Docker target missing")
            for target in targets:
                self._verify_target(target, marker)
        elif action == "run":
            self.verify_isolation()
            position = 1
            while position < len(args) and args[position].startswith("-"):
                flag = args[position]
                require(flag in {"--rm", "--network", "--mount"}, "unsafe standalone container option")
                position += 1 if flag == "--rm" else 2
            require(position < len(args) and args[position+1:position+3] == ["python", "-c"] and len(args) == position+4, "unexpected standalone command")
            require("--network" in args and args[args.index("--network") + 1] == marker["project"] + "_default", "foreign standalone network")
            require(args.count("--network") == 1 and args.count("--mount") == 2 and args.count("--rm") == 1, "exact private restore mounts required")
            self.verify_network(marker)
            runtime_image = args[position]
            require(fixture_image(runtime_image, marker) or re.fullmatch(r"learngraph:postgres-maintenance-packaged-smoke-[0-9]{8}", runtime_image), "unapproved standalone image")
            info = json.loads(self._raw(["image", "inspect", runtime_image]))[0]
            require(set((info.get("Config") or {}).get("Volumes") or {}) <= {"/data"}, "standalone image adds unexpected volumes")
            destinations = set()
            for i, arg in enumerate(args):
                if arg == "--mount":
                    parts = [v.split("=", 1) if "=" in v else (v, "true") for v in args[i+1].split(",")]
                    mount = dict(parts)
                    require(len(parts) == len(mount), "duplicate mount options")
                    valid = mount == {"type": "volume", "source": marker["project"] + "_data", "target": "/data"}
                    valid |= mount == {"type": "bind", "source": str(self.state_dir / "token"), "target": "/run/updater-token", "readonly": "true"}
                    require(valid, "foreign standalone mount")
                    require(mount["target"] not in destinations, "duplicate standalone mount target")
                    destinations.add(mount["target"])
                    if mount["type"] == "volume":
                        self.verify_volume(mount["source"], marker)
        elif action == "build":
            require("-t" in args and args[args.index("-t")+1].startswith(marker["image"] + ":") and Path(args[-1]).resolve() == self.root, "foreign build target/context")
            require(len(args) >= 4 and args[1] == "-t" and fixture_image(args[2], marker), "unexpected build output")
            require(len(args[3:-1]) % 2 == 0, "unexpected build option")
            for index in range(3, len(args)-1, 2):
                require(args[index] == "--build-arg" and args[index+1].split("=", 1)[0] in {"VERSION", "FIXTURE_VERSION", "BROKEN", "DATABASE_CAPABILITY"} and "\n" not in args[index+1], "unapproved build option")
        elif action == "image" and args[1] in {"tag", "rm"}:
            for position, target in enumerate(args[2:], 2):
                allowed = target.startswith(marker["image"] + ":") or target.startswith("learngraph-retained:")
                destination = args[1] == "tag" and position == 3
                if destination and target.startswith("learngraph-retained:"):
                    allowed = any(target.startswith("learngraph-retained:" + job["id"] + "-") for job in self.state["jobs"])
                elif target.startswith("sha256:") or target.startswith("learngraph-retained:"):
                    image = json.loads(self._raw(["image", "inspect", target]))[0]
                    allowed = any(t.startswith(marker["image"] + ":") for t in (image.get("RepoTags") or []))
                require(allowed, "foreign image mutation")
        else:
            require(action == "image" and len(args) == 3 and args[1] == "inspect", "unsupported Docker command")
            require(fixture_image(args[2], marker) or re.fullmatch(r"sha256:[0-9a-f]{64}", args[2]), "foreign image query")
        return self._raw(args, timeout, validated=action not in {"inspect", "image"} or action == "image" and args[1] in {"tag", "rm"})

    def api_url(self):
        marker = self.verify_isolation()
        app = self.containers()["app"]
        bindings = app["NetworkSettings"]["Ports"].get("8000/tcp") or []
        require(bindings == [{"HostIp": "127.0.0.1", "HostPort": str(marker["port"])}], "HTTP endpoint does not belong to this fixture")
        return f"http://127.0.0.1:{marker['port']}/api/v1"

    def remove_managed_postgres(self, name):
        marker = self.verify_isolation()
        existing = self._raw(["ps", "-aq", "--filter", "name=^/" + name + "$"])
        if not existing:
            return
        target = self._verify_target(name, marker)
        volume = target["Mounts"][0]["Name"]
        self.command(["rm", "-f", name])
        # _verify_target checks the recorded job, exact private network,
        # sole mount and matching job label on this dedicated database volume.
        self._raw(["volume", "rm", volume], validated=True)

    def remove_optional_agent(self):
        marker = self.verify_isolation()
        ids = self._raw(["ps", "-aq", "--filter", "label=com.docker.compose.project=" + marker["project"],
                         "--filter", "label=com.docker.compose.service=database-migration-agent"]).splitlines()
        for identifier in ids:
            target = self._verify_target(identifier, marker)
            if target["State"]["Running"]:
                self.command(["stop", identifier])
            self.command(["rm", identifier])

    def fixture_image_tags(self):
        marker, _ = validate_fixture(self.root)
        references = [marker["image"] + ":*"]
        for job in self.state["jobs"]:
            require(re.fullmatch(r"[0-9a-f]{24}", job["id"]), "invalid retained image job")
            references.append("learngraph-retained:" + job["id"] + "-*")
        tags = set()
        for reference in references:
            tags.update(self._raw(["image", "ls", "--filter", "reference=" + reference, "--format", "{{.Repository}}:{{.Tag}}"] ).splitlines())
        return sorted(tags)

    def run_pytest(self, files, wheels, *, postgres=None, database="maintenance_test"):
        """Fixed test worker: no caller-supplied containers, volumes or Docker flags."""
        marker = self.verify_isolation()
        repo = Path(__file__).resolve().parents[2]
        require(files and all(re.fullmatch(r"tests/(?:unit|services|integration)/test_[a-z0-9_]+\.py", name) and (repo / "backend" / name).is_file() for name in files), "unapproved pytest target")
        wheels = Path(wheels).resolve()
        require(wheels.is_relative_to(self.root) and wheels.is_dir(), "test dependencies must stay inside owned fixture")
        options = ["run", "--rm", "--read-only", "--cap-drop", "ALL", "--cap-add", "CHOWN", "--tmpfs", "/tmp:rw,exec,nosuid,nodev", "--label", OWNER + "=" + marker["owner"]]
        environment = {"PYTHONDONTWRITEBYTECODE":"1", "LEARNGRAPH_TEST_DOCKER_PROJECT":marker["project"], "LEARNGRAPH_TEST_DOCKER_OWNER":marker["owner"]}
        if postgres:
            require(database in {"maintenance_test", "migration_test"}, "disposable PostgreSQL test database required")
            item = self._verify_target(postgres, marker)
            require(item["Name"].startswith("/learngraph-main-postgres-"), "managed fixture PostgreSQL required")
            from urllib.parse import quote
            values = dict(v.split("=", 1) for v in item["Config"]["Env"] if "=" in v)
            # The inspected fixture administrator creates only these named test
            # databases. The application database is never selected by pytest.
            exists = self.command(["exec", item["Id"], "psql", "-U", values["POSTGRES_USER"], "-d", "postgres", "-tAc", "SELECT count(*) FROM pg_database WHERE datname='" + database + "'"])
            require(exists.strip() in {"0", "1"}, "unexpected disposable database catalogue")
            if exists.strip() == "0":
                self.command(["exec", item["Id"], "psql", "-U", values["POSTGRES_USER"], "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", "CREATE DATABASE " + database])
            environment.update(LEARNGRAPH_TEST_DOCKER_PROJECT=marker["project"], LEARNGRAPH_TEST_DOCKER_OWNER=marker["owner"], LEARNGRAPH_TEST_POSTGRES_CONTAINER=item["Id"], LEARNGRAPH_TEST_POSTGRES_URL="postgresql+psycopg://" + quote(values["POSTGRES_USER"], safe="") + ":" + quote(values["POSTGRES_PASSWORD"], safe="") + "@" + item["Name"].lstrip("/") + "/" + database)
            options += ["--network", marker["project"] + "_default", "--mount", "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock,readonly"]
        else:
            options += ["--network", "none"]
        for source, target in [(repo / "backend/app", "/app/app"), (repo / "backend/tests", "/app/tests"), (repo / "backend/scripts", "/app/backend-scripts"), (repo / "updater", "/app/updater"), (repo / "scripts", "/app/scripts"), (wheels, "/wheels")]:
            options += ["--mount", "type=bind,source=" + str(source.resolve()) + ",target=" + target + ",readonly"]
        for key, value in environment.items():
            options += ["--env", key + "=" + value]
        options += ["--entrypoint", "/bin/sh", marker["image"] + ":v1.0.0", "-c", "set -e; /usr/local/bin/python -m pip install --disable-pip-version-check --no-index --find-links=/wheels --target=/tmp/testdeps pytest==8.4.2 >/tmp/pip.log 2>&1; export PYTHONPATH=/tmp/testdeps:/app; python -B -m pytest -c /dev/null --basetemp=/tmp/pytest -q " + " ".join(files)]
        return self._raw(options, 600, validated=True)
