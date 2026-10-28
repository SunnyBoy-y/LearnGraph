"""Read-only identity gate for live UI tests, before login or artifact writes."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from updater.tests.docker_isolation import IsolatedAgent, require, validate_fixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    marker, _ = validate_fixture(root)
    agent = object.__new__(IsolatedAgent)
    agent.root, agent.image = root, marker["image"]
    agent.state_dir, agent.override, agent.compose_files = root / "data/updater", root / "docker-compose.update.json", ["compose.json"]
    agent.state = json.loads((agent.state_dir / "state.json").read_text())
    expected = urlsplit(agent.api_url())
    actual = urlsplit(args.url)
    require((actual.scheme,actual.netloc)==(expected.scheme,expected.netloc) and actual.path in {"","/"} and not actual.query and not actual.fragment, "live test must connect directly to its owned Docker endpoint; production/proxy URLs rejected")
    print("Owned disposable Docker endpoint verified")


if __name__ == "__main__":
    main()
