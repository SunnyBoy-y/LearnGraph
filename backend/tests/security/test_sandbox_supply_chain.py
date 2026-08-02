from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_sandbox_toolchain_is_lockfile_backed() -> None:
    dockerfile = (ROOT / "sandbox" / "Dockerfile").read_text(encoding="utf-8")
    package_json = (ROOT / "sandbox" / "toolchain" / "package.json").read_text(
        encoding="utf-8"
    )
    lockfile = ROOT / "sandbox" / "toolchain" / "package-lock.json"

    assert lockfile.is_file()
    assert '"playwright-core": "1.62.0"' in package_json
    assert "package-lock.json" in dockerfile
    assert "npm ci --omit=dev --ignore-scripts --no-audit --no-fund" in dockerfile
    assert "npm install --global" not in dockerfile


def test_supply_chain_builder_and_verifier_bind_inputs() -> None:
    build = (ROOT / "scripts" / "build_sandbox_supply_chain.ps1").read_text(
        encoding="utf-8"
    )
    verify = (ROOT / "scripts" / "verify_sandbox_supply_chain.py").read_text(
        encoding="utf-8"
    )

    assert "docker sbom" in build
    assert "dockerfile_sha256" in build
    assert "npm_lock_sha256" in build
    assert "unsigned-local" in build
    assert "cosign" in build

    assert "Supply-chain input mismatch" in verify
    assert "SBOM hash mismatch" in verify
    assert "Docker image ID does not match" in verify
    assert "--skip-docker" in verify
