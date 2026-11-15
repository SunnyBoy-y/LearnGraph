"""Opt-in actual Chromium test against a local approved-proxy fixture, no internet.

Run: LEARNGRAPH_BROWSER_INTEGRATION=1 uv run --no-sync python -m pytest
     tests/integration/test_browser_read_live.py -q
No user profile, account, cookies or external service are accessed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from app.core.errors import AppError
from app.services.browser_page_read import read_browser_page
from app.providers.remote.sandbox import validate_agent_argv

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("LEARNGRAPH_BROWSER_INTEGRATION") != "1", reason="isolated Chromium integration is opt-in")]


def browser_executable():
    override = os.environ.get("LEARNGRAPH_BROWSER_TEST_EXECUTABLE")
    if override and Path(override).is_file():
        return override
    for executable in ("chromium", "chromium-browser", "google-chrome", "chrome", "msedge"):
        found = shutil.which(executable)
        if found:
            return found
    if os.name == "nt":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                for relative in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
                    candidate = Path(base) / relative
                    if candidate.is_file():
                        return str(candidate)
    pytest.fail("No Chromium binary found for the explicitly requested integration test")


class LocalSandbox:
    """Test seam only: production always uses the existing sandbox service."""
    def __init__(self, root, env):
        self.root, self.env = root, env

    def execute_agent_tool(self, name, arguments, **_):
        if name == "sandbox_write_file":
            target = self.root / arguments["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(arguments["content"], encoding="utf-8")
            return {"status": "completed"}
        assert name == "sandbox_exec"
        validate_agent_argv(tuple(arguments["argv"]))
        result = subprocess.run(arguments["argv"], cwd=self.root, env=self.env,
                                capture_output=True, text=True, timeout=40, encoding="utf-8")
        return {"status": "SUCCEEDED" if result.returncode == 0 else "FAILED", "exit_code": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}


@pytest.fixture
def isolated_fixture(tmp_path, monkeypatch):
    digest = "e" * 64
    requests = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            if self.headers.get("X-LearnGraph-Policy-Digest") != digest:
                self.send_error(403)
                return
            if self.path.startswith("http://read.example.test/redirect"):
                self.send_response(302)
                self.send_header("Location", "http://outside.invalid/private")
                self.end_headers()
                return
            if not self.path.startswith("http://read.example.test/"):
                self.send_error(403)
                return
            if self.path.endswith("/unreadable"):
                body = b"<html><body>Please enable JavaScript</body></html>"
            elif self.path.endswith("/long"):
                body = ("<html><body>" + "中文正文与表情🙂" * 12000 + "</body></html>").encode("utf-8")
            else:
                body = b'''<html><head><title>Isolated browser fixture</title></head><body><div id="body">Loading...</div>
                <script>document.getElementById('body').textContent='Dynamic page body rendered by JavaScript. '+
                'These words did not exist in the initial HTML body. Independent context, no user cookies.';</script></body></html>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", "fixture_cookie=private; SameSite=Lax")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ)
    env.update({"HTTPS_PROXY": f"http://127.0.0.1:{server.server_port}", "HTTP_PROXY": f"http://127.0.0.1:{server.server_port}",
        "LEARNGRAPH_EGRESS_POLICY_DIGEST": digest,
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH": browser_executable(),
        "NODE_PATH": str(Path(__file__).resolve().parents[2] / "sandbox/toolchain/node_modules")})
    monkeypatch.setattr("app.services.browser_page_read.require_public_http_url", lambda *_: "read.example.test")
    sandbox = LocalSandbox(tmp_path, env)
    try:
        yield sandbox, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_real_chromium_renders_javascript_and_authenticates_only_the_proxy(isolated_fixture):
    sandbox, requests = isolated_fixture
    result, sources = read_browser_page(sandbox, {"url": "http://read.example.test/dynamic"},
        chat_session_id="chat", allowed_domains=["read.example.test"], agent_authorized=True)
    assert "Dynamic page body rendered by JavaScript" in result["content"]
    assert result["page_read"] and sources[0]["evidence_kind"] == "page_text"
    read_browser_page(sandbox, {"url": "http://read.example.test/fresh"},
        chat_session_id="chat", allowed_domains=["read.example.test"], agent_authorized=True)
    navigations = [(url, headers) for url, headers in requests if url.endswith(("/dynamic", "/fresh"))]
    assert len(navigations) == 2
    assert all("Cookie" not in headers and "cookie" not in headers for _, headers in navigations)
    assert all(headers.get("x-learngraph-policy-digest") == "e" * 64 or headers.get("X-LearnGraph-Policy-Digest") == "e" * 64 for _, headers in navigations)


def test_real_browser_bounds_unicode_json_without_claiming_the_entire_page(isolated_fixture):
    sandbox, _ = isolated_fixture
    result, sources = read_browser_page(sandbox, {"url": "http://read.example.test/long"},
        chat_session_id="chat", allowed_domains=["read.example.test"], agent_authorized=True)
    assert result["content_truncated"] is True
    assert "中文正文" in result["content"]
    assert len(result["content"].encode("utf-8")) < 48000
    assert sources[0]["page_read"] is True


@pytest.mark.parametrize("path", ["redirect", "unreadable"])
def test_real_browser_rejects_unauthorized_redirect_and_unreadable_page(isolated_fixture, path):
    sandbox, requests = isolated_fixture
    with pytest.raises(AppError) as error:
        read_browser_page(sandbox, {"url": f"http://read.example.test/{path}"}, chat_session_id="chat",
                          allowed_domains=["read.example.test"], agent_authorized=True)
    assert error.value.code == "browser_read_failed"
    assert all("outside.invalid" not in url for url, _ in requests)
