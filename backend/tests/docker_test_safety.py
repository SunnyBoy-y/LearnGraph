"""Read-only Docker identity checks BEFORE any live integration connection.

Names, localhost, database names, opt-in flags and reachability are not proof
of ownership. All mounts and networks must belong to the explicit test owner.
No production target is adopted and no credentials are read by these checks.
"""
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

OWNER = "io.learngraph.acceptance-owner"


def require(condition, message):
    if not condition:
        raise RuntimeError("Docker test isolation: " + message)


def owned_container(target):
    project = os.environ.get("LEARNGRAPH_TEST_DOCKER_PROJECT", "")
    owner = os.environ.get("LEARNGRAPH_TEST_DOCKER_OWNER", "")
    require(re.fullmatch(r"lg-(?:pg-maintenance|update-test|sandboxd-test)-[0-9a-f]{10}", project), "explicit private test project required")
    require(re.fullmatch(r"[0-9a-f]{32}", owner), "explicit test owner required")
    require(target and not target.startswith("-"), "explicit test container required")
    import docker
    client = docker.from_env()
    try:
        item = client.containers.get(target).attrs
        labels = item["Config"].get("Labels") or {}
        job = labels.get("io.learngraph.primary-migration", "")
        pg = bool(re.fullmatch(r"[0-9a-f]{32}", job)) and item["Name"] == "/learngraph-main-postgres-" + job
        require(pg or labels.get(OWNER) == owner and labels.get("com.docker.compose.project") == project, "foreign/production container rejected")
        require(pg or not item["Name"].startswith("/learngraph-"), "production container name rejected")
        require(set(item["NetworkSettings"]["Networks"]) == {project + "_default"}, "foreign/additional network rejected")
        network = client.networks.get(project + "_default").attrs
        require(network.get("Labels", {}).get(OWNER) == owner and network["Labels"].get("com.docker.compose.project") == project, "network owner mismatch")
        require(bool(item["Mounts"]), "owned data mount required")
        for mount in item["Mounts"]:
            # No host bind or unlabelled/foreign read-only volume exceptions.
            require(mount["Type"] == "volume", "integration target host/anonymous mount rejected")
            require(mount.get("Name") == item["Name"].lstrip("/") + "-data" if pg else mount.get("Name", "").startswith(project + "_"), "foreign/production volume name rejected before inspection")
            volume = client.volumes.get(mount["Name"]).attrs
            volume_labels = volume.get("Labels") or {}
            require((pg and len(item["Mounts"]) == 1 and mount["Name"] == item["Name"].lstrip("/") + "-data" and mount["Destination"] == "/var/lib/postgresql" and volume_labels.get("io.learngraph.primary-migration") == job)
                or (not pg and mount["Name"].startswith(project + "_") and volume_labels.get(OWNER) == owner and volume_labels.get("com.docker.compose.project") == project), "foreign/production volume rejected, including read-only")
        return item
    finally:
        client.close()


def endpoint(value, container_env, *, postgres=False):
    require(bool(value), "explicit test endpoint required")
    url = urlsplit(value)
    require(url.scheme in ({"postgresql", "postgresql+psycopg"} if postgres else {"http"}), "unexpected endpoint scheme")
    require(not url.query and not url.fragment, "endpoint options cannot redirect a test connection")
    item = owned_container(os.environ.get(container_env, ""))
    inside = url.hostname == item["Name"].lstrip("/")
    port = url.port or (5432 if postgres else 80)
    if inside:
        require(port == (5432 if postgres else 8090), "unexpected internal port")
    else:
        require(url.hostname == "127.0.0.1" and port not in {8000, 18000, 18090}, "non-private/production endpoint rejected")
        bindings = item["NetworkSettings"].get("Ports", {}).get("5432/tcp" if postgres else "8090/tcp") or []
        require(bindings == [{"HostIp":"127.0.0.1", "HostPort":str(port)}], "endpoint differs from inspected test container")
    if postgres:
        require(url.path in {"/maintenance_test", "/migration_test"}, "disposable test database required")
    return value


def credential_path(value):
    root = Path(os.environ.get("LEARNGRAPH_TEST_DOCKER_ROOT", "")).resolve()
    require(re.fullmatch(r"[0-9a-f]{32}", os.environ.get("LEARNGRAPH_TEST_DOCKER_OWNER", "")), "test owner required before reading credentials")
    require(root.name.startswith("lg-") and root != Path.cwd().resolve(), "explicit disposable credential root required")
    path = Path(value).resolve()
    require(path.is_relative_to(root), "credential file escapes disposable fixture")
    return path
