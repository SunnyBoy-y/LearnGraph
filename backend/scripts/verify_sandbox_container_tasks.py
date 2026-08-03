"""Real-Docker end-to-end verification of the fixed sandbox tasks.

Exercises the two P2 extension tasks inside the rebuilt offline container the
same way the backend does (network_mode=none, non-root 65532, read-only root):

- ``render_component`` (P2-A): a server-owned inert preview document with the
  strict CSP must be accepted; a script-bearing or CSP-less document must fail.
- ``mcp_stdio`` (P2-B): a fixed JSON-RPC request against an allowlisted
  interpreter command must round-trip inside the container.

Requirements: Docker Engine with the rebuilt ``learngraph-sandbox:local`` image
(the backend's ``build_sandbox_image.ps1``), and a writable temp dir.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

IMAGE = "learngraph-sandbox:local"
RUNNER = "/opt/learngraph/runner.py"
# The e2e exercises the *current* runner source by mounting it over the image
# copy (the image must be rebuilt with build_sandbox_image.ps1 to bake changes
# permanently; this keeps container verification in sync with the repo).
REPO_RUNNER = pathlib.Path(__file__).resolve().parent.parent / "sandbox" / "runner.py"
PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [ok]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def run_task(workspace: pathlib.Path, task: str, input_rel: str, output_rel: str, spec_rel: str | None) -> int:
    args = [
        "docker", "run", "--rm",
        "--network", "none",
        "--user", "65532:65532",
        "--read-only",
        "-v", f"{workspace}:/workspace:rw",
        "-v", f"{REPO_RUNNER}:{RUNNER}:ro",
        "--entrypoint", "python",
        IMAGE,
        RUNNER,
        "--task", task,
        "--input", input_rel,
        "--output", output_rel,
    ]
    if spec_rel:
        args += ["--spec", spec_rel]
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


def verify_render_component(workspace: pathlib.Path) -> None:
    print("[P2-A render_component in offline container]")
    valid_doc = """<!doctype html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; connect-src 'none'">
</head><body><p>inert preview</p></body></html>"""
    (workspace / "input").mkdir(exist_ok=True)
    (workspace / "output").mkdir(exist_ok=True)
    (workspace / "input" / "valid.html").write_text(valid_doc, encoding="utf-8")

    ok = run_task(workspace, "render_component", "input/valid.html", "output/valid.json", None)
    check("valid inert document accepted", ok.returncode == 0, ok.stderr[-300:])
    if ok.returncode == 0:
        artifact = json.loads((workspace / "output" / "valid.json").read_text(encoding="utf-8"))
        check("artifact validates", artifact.get("status") == "ok" and artifact.get("task_type") == "render_component")

    evil_doc = '<!doctype html><html><head></head><body><script>alert(1)</script></body></html>'
    (workspace / "input" / "evil.html").write_text(evil_doc, encoding="utf-8")
    bad = run_task(workspace, "render_component", "input/evil.html", "output/evil.json", None)
    check("script-bearing document rejected", bad.returncode != 0, f"(exit {bad.returncode})")


def verify_mcp_stdio(workspace: pathlib.Path) -> None:
    print("[P2-B mcp_stdio in offline container]")
    (workspace / "input").mkdir(exist_ok=True)
    (workspace / "output").mkdir(exist_ok=True)
    # A fixed, allowlisted interpreter invocation (python3) with a bounded
    # request; the runner translates stdout JSON into the artifact.
    launch = {
        "command": ["python3", "-c",
                    "import sys, json; req=json.load(sys.stdin); print(json.dumps({'jsonrpc':'2.0','id':req.get('id'),'result':{'echo': req.get('params',{}).get('x')}}))"],
        "max_args": 16,
        "timeout_seconds": 30,
    }
    request = {"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"x": "hello"}}
    (workspace / "spec.json").write_text(json.dumps(launch), encoding="utf-8")
    (workspace / "input" / "req.json").write_text(json.dumps(request), encoding="utf-8")

    ok = run_task(workspace, "mcp_stdio", "input/req.json", "output/mcp.json", "spec.json")
    check("JSON-RPC request round-trips", ok.returncode == 0, ok.stderr[-300:])
    if ok.returncode == 0:
        # The output file holds the raw JSON-RPC response written by the fixed
        # task, not a wrapper artifact.
        response = json.loads((workspace / "output" / "mcp.json").read_text(encoding="utf-8"))
        check("result echoes payload", response.get("result", {}).get("echo") == "hello", str(response)[:200])
        check("response id matches", response.get("id") == 1)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="learngraph-container-e2e-") as tmp:
        workspace = pathlib.Path(tmp)
        print(f"Docker container e2e (image {IMAGE})")
        verify_render_component(workspace)
        verify_mcp_stdio(workspace)
    print(f"\n{FAIL} failed, {PASS} passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
