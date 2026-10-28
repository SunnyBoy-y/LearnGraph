"""Mandatory Docker CLI boundary for shell acceptance, never production use.

Every Docker invocation from the tested shell passes through this allowlist.
The production script's checks are independent of this second boundary.
"""
import json
import os
from pathlib import Path
import re
import sys

from updater.tests.docker_isolation import IsolatedAgent, require, validate_fixture


def metadata_format(item, expression):
    """Only the product script's fixed metadata queries; no arbitrary templates."""
    label = re.fullmatch(r'{{index \.Config.Labels "([a-zA-Z0-9_.-]+)"}}', expression)
    if label:
        return (item["Config"].get("Labels") or {}).get(label[1], "<no value>")
    if expression == "{{.Image}}": return item["Image"]
    if expression == "{{.Image}} {{.State.Health.Status}}": return item["Image"] + " " + item.get("State",{}).get("Health",{}).get("Status","")
    for destination in ("/data", "/data/sandboxd"):
        if expression == '{{range .Mounts}}{{if eq .Destination "' + destination + '"}}{{.Name}}{{end}}{{end}}':
            return "".join(m.get("Name", "") for m in item["Mounts"] if m["Destination"] == destination)
    filters = {
        '{{range .Mounts}}{{if and .RW (eq .Type "bind")}}{{.Destination}} {{end}}{{end}}': ("bind", True, "Destination"),
        '{{range .Mounts}}{{if and .RW (eq .Type "volume")}}{{.Name}} {{end}}{{end}}': ("volume", True, "Name"),
        '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}} {{end}}{{end}}': ("volume", False, "Name"),
    }
    require(expression in filters, "unapproved metadata template")
    kind, writable, field = filters[expression]
    return "".join(m[field] + " " for m in item["Mounts"] if m["Type"] == kind and (not writable or m.get("RW")))


def dispatch(agent, args):
    marker, config = validate_fixture(agent.root)
    require(bool(args), "empty shell Docker command")
    if args == ["compose", "version"]:
        return agent._raw(args)
    if args[0] == "compose":
        require(args[1:5] == ["--project-name", marker["project"], "-f", "docker-compose.yml"], "shell Compose scope mismatch")
        require((agent.root / "docker-compose.yml").read_bytes() == (agent.root / "compose.json").read_bytes(), "shell Compose copy differs")
        operation = args[5:]
        if operation == ["ps", "--all", "-q"]:
            agent.verify_isolation()
            return agent._raw(["ps", "-aq", "--filter", "label=com.docker.compose.project=" + marker["project"]])
        if operation == ["ps", "--status", "running", "--quiet"]:
            agent.verify_isolation()
            return agent._raw(["ps", "-q", "--filter", "label=com.docker.compose.project=" + marker["project"]])
        if operation and operation[0] == "up":
            # Model the product Compose file's LEARNGRAPH_IMAGE substitution
            # with an owned image override; the fixture JSON stays literal.
            image = os.environ.get("LEARNGRAPH_IMAGE", "")
            agent.verify_image(image, marker)
            agent.write_images(dict.fromkeys(("app", "preview", "egress-proxy"), image))
        return agent.compose(operation)
    if args[:2] == ["image", "inspect"]:
        require(len(args) == 5 and args[2] == "--format", "unapproved shell image query")
        item = agent.verify_image(args[4], marker)
        labels = {'{{index .Config.Labels "io.learngraph.primary-database"}}':"io.learngraph.primary-database", '{{index .Config.Labels "io.learngraph.update-compatibility"}}':"io.learngraph.update-compatibility"}
        require(args[3] == "{{.Id}}" or args[3] in labels, "unapproved image metadata template")
        return item["Id"] if args[3] == "{{.Id}}" else (item["Config"].get("Labels") or {}).get(labels[args[3]], "<no value>")
    if args[0] == "inspect":
        require(len(args) == 4 and args[1] == "--format" and not args[3].startswith("-"), "unapproved shell inspect")
        # Metadata inspection still validates project, role, every mount and
        # network name. Resource labels are checked afresh before exec/copy,
        # archive helpers and Compose mutations, where content is accessible.
        item = agent._verify_target(args[3], marker, verify_resources=False)
        return metadata_format(item, args[2])
    if args[:2] == ["volume", "inspect"]:
        require(len(args) == 5 and args[2] == "--format", "unapproved shell volume inspect")
        item = agent.verify_volume(args[4], marker)
        require(args[3] in {'{{index .Labels "com.docker.compose.project"}}', '{{index .Labels "io.learngraph.acceptance-owner"}}'}, "unapproved volume metadata template")
        field = "io.learngraph.acceptance-owner" if "acceptance-owner" in args[3] else "com.docker.compose.project"
        return item["Labels"][field]
    return agent.command(args)


def backup_helper(agent, args):
    marker, _ = validate_fixture(agent.root)
    require(len(args) == 13 and args[:3] == ["run", "--rm", "-v"] and args[4] == "-v" and args[6:9] == ["alpine", "tar", "czf"] and args[10:] == ["-C", "/data", "."], "unapproved shell backup helper")
    source, destination, mode = args[3].split(":")
    require(destination == "/data" and mode == "ro", "shell backup source must be read-only")
    agent.verify_volume(source, marker)
    output, target = args[5].rsplit(":", 1)
    output = Path(output).resolve()
    require(output.is_relative_to(agent.root / "backups") and target == "/backup", "shell backup output escaped fixture")
    require(re.fullmatch(r"/backup/[A-Za-z0-9_.-]+-(?:volume|sandboxd-state)\.tar\.gz", args[9]), "unexpected archive target")
    agent.verify_isolation()
    return agent._raw(["run", "--network", "none", "--read-only", "--cap-drop", "ALL", "--cap-add", "DAC_OVERRIDE", *args[1:]], 180, validated=True)


def main():
    root = Path(os.environ.get("LEARNGRAPH_SHELL_TEST_ROOT", "")).resolve()
    marker, _ = validate_fixture(root)
    # A CLI check must not initialize tokens or chmod any directory.
    agent = object.__new__(IsolatedAgent)
    agent.root, agent.image = root, marker["image"]
    agent.state_dir = root / "data/updater"
    agent.override, agent.compose_files = root / "docker-compose.update.json", ["compose.json"]
    agent.state = json.loads((agent.state_dir / "state.json").read_text())
    require(Path(os.environ.get("LEARNGRAPH_TEST_REAL_DOCKER", "")).is_absolute(), "explicit Docker executable required")
    # Agent.command uses docker from PATH. Only this child loses the shim PATH.
    real = Path(os.environ["LEARNGRAPH_TEST_REAL_DOCKER"])
    os.environ["PATH"] = str(real.parent) + os.pathsep + os.environ.get("LEARNGRAPH_TEST_ORIGINAL_PATH", "")
    args = sys.argv[1:]
    result = backup_helper(agent, args) if args and args[0] == "run" else dispatch(agent, args)
    if result:
        print(result)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never echo arguments, credentials, container env, or Docker diagnostics.
        print("Docker test isolation: shell command rejected or failed; no unscoped fallback", file=sys.stderr)
        raise SystemExit(97)
