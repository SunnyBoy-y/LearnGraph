from __future__ import annotations

from app.services.sandbox_live_output import (
    combine_live_shell_output,
    emit_live_output,
    live_output_sink,
)


def test_combine_live_shell_output_caps_lines_and_chars() -> None:
    stdout_tail = "".join(f"out-{i}\n" for i in range(250))
    stderr_tail = "err-final\n"
    combined, truncated = combine_live_shell_output(stdout_tail, stderr_tail, max_lines=50, max_chars=2000)
    assert truncated is True
    lines = combined.splitlines()
    assert len(lines) <= 50
    assert lines[-1] == "err-final"


def test_live_output_sink_routes_thread_local_snapshots_and_swallows_errors() -> None:
    received: list[dict] = []
    with live_output_sink(received.append):
        emit_live_output({"output": "installing deps...", "truncated": False, "latency_ms": 420})
    assert len(received) == 1
    assert received[0]["output"] == "installing deps..."

    # Outside context manager emits safely as a no-op
    emit_live_output({"output": "ignored"})
    assert len(received) == 1

    def _boom(_snapshot: dict) -> None:
        raise RuntimeError("broken sink")

    with live_output_sink(_boom):
        emit_live_output({"output": "must not raise"})
