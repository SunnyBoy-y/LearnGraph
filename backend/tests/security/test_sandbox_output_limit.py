from __future__ import annotations

from app.providers.remote import sandbox as sandbox_module
from app.providers.remote.sandbox import DockerSandboxBackend


class FakeStream:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.frames)

    def close(self):
        self.closed = True


class FakeApi:
    def __init__(self, stream):
        self.stream = stream

    def exec_create(self, *_args, **_kwargs):
        return {"Id": "exec-1"}

    def exec_start(self, *_args, **_kwargs):
        return self.stream

    def exec_inspect(self, *_args, **_kwargs):
        return {"ExitCode": 0}


class FakeContainer:
    id = "container-1"
    labels = {"com.learngraph.workspace_limit_bytes": "1024"}

    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True


def test_stream_exec_kills_at_output_limit(monkeypatch) -> None:
    stream = FakeStream([(b"abc", None), (None, b"def"), (b"ghi", None)])
    container = FakeContainer()
    client = type("Client", (), {"api": FakeApi(stream)})()
    monkeypatch.setattr(DockerSandboxBackend, "_ensure_workspace_quota", lambda *_: None)
    timestamps = iter([0.0, 0.1, 0.2, 0.3, 0.4])
    monkeypatch.setattr(sandbox_module.time, "monotonic", lambda: next(timestamps))

    result = DockerSandboxBackend._stream_exec(
        client,
        container,
        argv=("python", "-V"),
        workdir="/workspace",
        user="65532:65532",
        environment=None,
        timeout_seconds=30,
        output_limit=7,
        started=0.0,
    )

    assert result.truncated is True
    assert result.stdout == b"abcg"
    assert result.stderr == b"def"
    assert len(result.stdout) + len(result.stderr) == 7
    assert container.killed is True
    assert stream.closed is True
