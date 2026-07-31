from __future__ import annotations

import pytest

from app.providers.remote.sandbox import (
    SandboxCapabilityMismatch,
    validate_agent_argv,
    validate_agent_cwd,
    validate_agent_workspace_path,
)


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "work/../secret",
        "/etc/passwd",
        "C:/Windows/System32",
        "work\\file.txt",
        "work/file\x00.txt",
        "",
        "work/../../outside",
    ],
)
def test_workspace_path_escape_is_blocked(path: str) -> None:
    with pytest.raises(SandboxCapabilityMismatch):
        validate_agent_workspace_path(path)


def test_workspace_path_accepts_portable_relative_paths() -> None:
    assert validate_agent_workspace_path("work/file.txt") == "work/file.txt"
    assert validate_agent_workspace_path("work/tmp/nested.py") == "work/tmp/nested.py"


def test_agent_cwd_must_be_workspace_root() -> None:
    assert validate_agent_cwd(".") == "."
    with pytest.raises(SandboxCapabilityMismatch):
        validate_agent_cwd("work")
    with pytest.raises(SandboxCapabilityMismatch):
        validate_agent_cwd("../")


@pytest.mark.parametrize(
    "argv",
    [
        ("bash", "-c", "id"),
        ("sh", "-c", "cat /etc/passwd"),
        ("python", "-c", "print(1)"),
        ("rm", "/tmp/x"),
        ("rm", "../outside"),
        ("rm", "C:/Windows"),
        ("node", "-e", "console.log(1)"),
        ("curl", "https://example.com"),
    ],
)
def test_agent_argv_blocks_shell_and_host_shapes(argv: tuple[str, ...]) -> None:
    with pytest.raises(SandboxCapabilityMismatch):
        validate_agent_argv(argv)


def test_agent_argv_allows_workspace_python_script() -> None:
    assert validate_agent_argv(("python", "work/run.py")) == ("python", "work/run.py")


def test_agent_argv_allows_authorized_relative_delete_shape() -> None:
    # Authorization happens in the service layer; path shape must still be portable.
    assert validate_agent_argv(("rm", "work/tmp/file.txt")) == ("rm", "work/tmp/file.txt")
