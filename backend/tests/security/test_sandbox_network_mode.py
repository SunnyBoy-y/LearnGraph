from __future__ import annotations

import inspect

from app.providers.remote import sandbox as sandbox_module
from app.providers.remote.sandbox import DockerSandboxBackend
from app.providers.ports.sandbox import SandboxCreateSpec


class _FakeContainers:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def create(self, *args, **kwargs):
        self.kwargs = kwargs
        return type(
            "Container",
            (),
            {
                "id": "container-1",
                "start": lambda self: None,
                "remove": lambda self, force=False: None,
            },
        )()


class _FakeClient:
    def __init__(self) -> None:
        self.containers = _FakeContainers()

    def close(self) -> None:
        return None


def test_docker_create_forces_network_mode_none(monkeypatch) -> None:
    fake = _FakeClient()
    backend = DockerSandboxBackend(enabled=True, image_ref="sha256:" + ("a" * 64))
    monkeypatch.setattr(
        DockerSandboxBackend,
        "probe",
        lambda self: type(
            "Cap",
            (),
            {
                "available": True,
                "backend_id": "docker",
                "platform": "linux",
                "capabilities": (),
                "reason": None,
            },
        )(),
    )
    monkeypatch.setattr(DockerSandboxBackend, "_client", lambda self: fake)
    monkeypatch.setattr(
        sandbox_module,
        "sandbox_seccomp_security_options",
        lambda runtime_kind: ["seccomp={}", "no-new-privileges:true"],
    )
    monkeypatch.setattr(sandbox_module, "sandbox_shm_size", lambda runtime_kind: "64m")
    # Mounts require docker.types; stub create path after imports inside method by
    # monkeypatching the docker.types symbols used after import.
    import types
    import sys

    fake_docker = types.ModuleType("docker")
    fake_types = types.ModuleType("docker.types")

    class Mount:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class Ulimit:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    fake_types.Mount = Mount
    fake_types.Ulimit = Ulimit
    fake_docker.types = fake_types
    monkeypatch.setitem(sys.modules, "docker", fake_docker)
    monkeypatch.setitem(sys.modules, "docker.types", fake_types)

    # create() also needs a real workspace path on disk
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "ws"
        workspace.mkdir()
        handle = backend.create(
            SandboxCreateSpec(
                session_id="session-1",
                image_ref="sha256:" + ("a" * 64),
                memory_bytes=64 * 1024 * 1024,
                memory_swap_bytes=64 * 1024 * 1024,
                cpu_count=1.0,
                pids_max=64,
                disk_bytes=64 * 1024 * 1024,
                workspace_path=str(workspace),
                runtime_kind="python-node",
            )
        )
    assert handle.session_id == "session-1"
    assert fake.containers.kwargs is not None
    assert fake.containers.kwargs.get("network_mode") == "none"
    assert fake.containers.kwargs.get("read_only") is True
    assert fake.containers.kwargs.get("user") == "65532:65532"
    assert fake.containers.kwargs.get("cap_drop") == ["ALL"]
    assert fake.containers.kwargs.get("shm_size") == "64m"
    assert fake.containers.kwargs.get("security_opt") == [
        "seccomp={}",
        "no-new-privileges:true",
    ]


def test_create_source_hardcodes_network_none() -> None:
    source = inspect.getsource(DockerSandboxBackend.create)
    assert 'network_mode="none"' in source or "network_mode='none'" in source
