from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.providers.remote.sandbox import (
    BROWSER_RUNTIME_KIND,
    BROWSER_SHM_SIZE,
    CODE_RUNTIME_KIND,
    CODE_SHM_SIZE,
    SandboxCapabilityMismatch,
    sandbox_runtime_policy,
    sandbox_seccomp_security_options,
    sandbox_shm_size,
)


ROOT = Path(__file__).resolve().parents[2]


def _profile(runtime_kind: str) -> dict:
    path, _ = sandbox_runtime_policy(runtime_kind)
    return json.loads(path.read_text(encoding="utf-8"))


def _all_syscalls(profile: dict) -> set[str]:
    return {
        name
        for rule in profile["syscalls"]
        for name in rule.get("names", [])
    }


def test_runtime_policy_selects_distinct_profiles_and_shm_budgets() -> None:
    code_path, code_shm = sandbox_runtime_policy(CODE_RUNTIME_KIND)
    browser_path, browser_shm = sandbox_runtime_policy(BROWSER_RUNTIME_KIND)

    assert code_path.name == "seccomp_profile_code.json"
    assert browser_path.name == "seccomp_profile.json"
    assert code_shm == CODE_SHM_SIZE == "64m"
    assert browser_shm == BROWSER_SHM_SIZE == "1g"
    assert sandbox_shm_size(CODE_RUNTIME_KIND) == "64m"
    assert sandbox_shm_size(BROWSER_RUNTIME_KIND) == "1g"


def test_code_profile_excludes_browser_only_namespace_syscalls() -> None:
    code_syscalls = _all_syscalls(_profile(CODE_RUNTIME_KIND))
    browser_syscalls = _all_syscalls(_profile(BROWSER_RUNTIME_KIND))

    assert "clone" in code_syscalls
    assert {"clone3", "setns", "unshare", "chroot"}.isdisjoint(code_syscalls)
    assert {"clone", "clone3", "setns", "unshare", "chroot"}.issubset(
        browser_syscalls
    )


def test_security_options_preserve_no_new_privileges_for_both_profiles() -> None:
    for runtime_kind in (CODE_RUNTIME_KIND, BROWSER_RUNTIME_KIND):
        options = sandbox_seccomp_security_options(runtime_kind)
        assert "no-new-privileges:true" in options
        assert any(option.startswith("seccomp={") for option in options)


def test_unknown_runtime_kind_fails_closed() -> None:
    with pytest.raises(SandboxCapabilityMismatch):
        sandbox_runtime_policy("unknown-runtime")
    with pytest.raises(SandboxCapabilityMismatch):
        sandbox_seccomp_security_options("unknown-runtime")
