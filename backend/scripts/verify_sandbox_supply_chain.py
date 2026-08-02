from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def docker_image_id(tag: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify LearnGraph sandbox supply-chain manifest consistency."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--skip-docker", action="store_true")
    args = parser.parse_args()

    manifest_path: Path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema") != "learngraph-sandbox-supply-chain-v1":
        raise SystemExit("Unsupported supply-chain manifest schema")

    backend_root = Path(__file__).resolve().parents[1]
    sandbox_root = backend_root / "sandbox"
    expected_inputs = {
        "dockerfile_sha256": sandbox_root / "Dockerfile",
        "npm_lock_sha256": sandbox_root / "toolchain" / "package-lock.json",
    }
    for key, path in expected_inputs.items():
        expected = manifest.get("inputs", {}).get(key)
        actual = sha256(path)
        if not expected or expected != actual:
            raise SystemExit(f"Supply-chain input mismatch: {key}")

    sbom_name = manifest.get("sbom_file")
    sbom_hash = manifest.get("sbom_sha256")
    if bool(sbom_name) != bool(sbom_hash):
        raise SystemExit("SBOM manifest fields are inconsistent")
    if sbom_name:
        sbom_path = manifest_path.parent / sbom_name
        if not sbom_path.is_file() or sha256(sbom_path) != sbom_hash:
            raise SystemExit("SBOM hash mismatch")

    if not args.skip_docker:
        image_id = docker_image_id(str(manifest["tag"]))
        if image_id != manifest.get("image_id"):
            raise SystemExit("Docker image ID does not match supply-chain manifest")

    print("Sandbox supply-chain manifest verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
