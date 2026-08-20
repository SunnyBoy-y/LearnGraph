from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura


MAX_TEXT_CHARS = 2_000_000
MAX_PREVIEW_CHARS = 100_000
MAX_MCP_REQUEST_BYTES = 64 * 1024
MAX_MCP_RESPONSE_BYTES = 256 * 1024
MAX_WEB_FETCH_BYTES = 2 * 1024 * 1024
MAX_WEB_FETCH_REDIRECTS = 5
MAX_WEB_FETCH_TIMEOUT_SECONDS = 60.0
MAX_WEB_FETCH_TITLE_CHARS = 1_000
# Static HTML→Markdown extraction below this length triggers the Chromium
# render fallback (SPA / JS-heavy pages). Not a security gate — the container
# resource limits and egress policy bound the render regardless.
WEB_FETCH_EXTRACT_MIN_CHARS = 200
WEB_FETCH_RENDER_TIMEOUT_SECONDS = 15.0
WEB_FETCH_RENDER_SCRIPT = "/opt/learngraph/tasks/web_render.js"
ALLOWED_WEB_FETCH_CONTENT_TYPES = frozenset({
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/json",
})
WEB_FETCH_USER_AGENT = "LearnGraphSandboxFetch/1.0 (+https://learngraph.local)"
MCP_SERVER_EXECUTABLES = frozenset({"python", "python3", "node", "nodejs"})
_EXTERNAL_SCHEME = re.compile(r"(?:^|[\s\"'])https?://", re.IGNORECASE)
_SCRIPT_SRC = re.compile(r"<script[^>]*\bsrc\s*=", re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class WebFetchSpec:
    url: str
    allowed_domains: frozenset[str]
    allow_all: bool
    max_redirects: int
    max_bytes: int
    timeout_seconds: float
    spec_sha256: str


class WebFetchError(ValueError):
    """The bounded fixed fetch task refused its host-owned specification or response."""



def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _normalize_domain(value: object) -> str:
    if not isinstance(value, str):
        raise WebFetchError("web fetch allowed_domains must contain strings")
    candidate = value.strip().casefold().rstrip(".")
    if not candidate or "://" in candidate or "/" in candidate or "*" in candidate:
        raise WebFetchError("web fetch allowed_domains contains an invalid hostname")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise WebFetchError("web fetch allowed_domains must not contain IP addresses")
    labels = candidate.split(".")
    if len(labels) < 2 or any(
        not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ):
        raise WebFetchError("web fetch allowed_domains contains an invalid hostname")
    return candidate


def _validated_fetch_url(
    url: object,
    allowed_domains: frozenset[str],
    *,
    allow_all: bool = False,
) -> str:
    if not isinstance(url, str) or not url.strip():
        raise WebFetchError("web fetch URL is required")
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise WebFetchError("web fetch only permits absolute HTTPS URLs")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise WebFetchError("web fetch URLs must not contain userinfo")
    try:
        port = parsed.port
    except ValueError as exc:
        raise WebFetchError("web fetch URL port is invalid") from exc
    if port not in {None, 443}:
        raise WebFetchError("web fetch only permits HTTPS port 443")
    hostname = parsed.hostname.casefold().rstrip(".")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise WebFetchError("web fetch URL host must be a DNS name")
    if allow_all:
        # No-interception mode: any public DNS host is accepted. The network
        # boundary stays enforced by the egress proxy, which re-classifies the
        # resolved address at CONNECT time (private/loopback/metadata denied).
        return parsed.geturl()
    # Exact-host matching: the derived egress policy is exact-host by security
    # design (no wildcard/suffix expansion), so the runner must agree with the
    # proxy on the same set. Host-side authorization layers stay subdomain-
    # inclusive; this network boundary is deliberately stricter and fails closed.
    if hostname not in allowed_domains:
        raise WebFetchError("web fetch URL host is outside the approved domain set")
    return parsed.geturl()


def load_web_fetch_spec(path: Path) -> WebFetchSpec:
    try:
        raw = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebFetchError("web fetch spec is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise WebFetchError("web fetch spec must be an object")
    expected_digest = raw.pop("spec_sha256", None)
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise WebFetchError("web fetch spec is missing a valid spec_sha256")
    if hashlib.sha256(_canonical_json(raw)).hexdigest() != expected_digest:
        raise WebFetchError("web fetch spec digest does not match its content")
    if raw.get("schema_version") != "1.0" or set(raw) != {
        "schema_version", "url", "allowed_domains", "allow_all", "max_redirects", "max_bytes", "timeout_seconds"
    }:
        raise WebFetchError("web fetch spec has an unsupported schema")
    allow_all = raw["allow_all"]
    if not isinstance(allow_all, bool):
        raise WebFetchError("web fetch spec allow_all must be a boolean")
    domains_raw = raw["allowed_domains"]
    if not isinstance(domains_raw, list):
        raise WebFetchError("web fetch spec allowed_domains must be a list")
    if not allow_all and not 1 <= len(domains_raw) <= 50:
        raise WebFetchError("web fetch spec requires 1 to 50 allowed domains")
    if allow_all and len(domains_raw) > 50:
        raise WebFetchError("web fetch spec allowed_domains exceeds 50 entries")
    domains = frozenset(_normalize_domain(item) for item in domains_raw)
    if len(domains) != len(domains_raw):
        raise WebFetchError("web fetch spec allowed domains must be unique")
    max_redirects = raw["max_redirects"]
    max_bytes = raw["max_bytes"]
    timeout_seconds = raw["timeout_seconds"]
    if not isinstance(max_redirects, int) or not 0 <= max_redirects <= MAX_WEB_FETCH_REDIRECTS:
        raise WebFetchError("web fetch spec max_redirects is outside the permitted range")
    if not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_WEB_FETCH_BYTES:
        raise WebFetchError("web fetch spec max_bytes is outside the permitted range")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < float(timeout_seconds) <= MAX_WEB_FETCH_TIMEOUT_SECONDS:
        raise WebFetchError("web fetch spec timeout_seconds is outside the permitted range")
    url = _validated_fetch_url(raw["url"], domains, allow_all=allow_all)
    return WebFetchSpec(
        url=url,
        allowed_domains=domains,
        allow_all=allow_all,
        max_redirects=max_redirects,
        max_bytes=max_bytes,
        timeout_seconds=float(timeout_seconds),
        spec_sha256=expected_digest,
    )


def _proxy_is_configured() -> bool:
    # The dedicated fetch runner is never allowed to establish a direct socket.
    # The sandbox backend only creates it on the internal egress network, but
    # refusing a missing proxy here keeps that invariant true in test/dev misuse.
    return bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))


def _egress_policy_digest() -> str:
    # The container's approved egress policy digest, injected by the backend at
    # create time. In multi-tenant mode the proxy resolves the per-workspace
    # policy from this digest, so the runner must echo it on CONNECT.
    return os.environ.get("LEARNGRAPH_EGRESS_POLICY_DIGEST") or ""


def _fetch_headers() -> dict[str, str]:
    # Only benign request headers; the egress policy digest is NOT sent to the
    # origin — it rides the proxy's CONNECT headers instead (see web_fetch).
    return {
        "Accept": "text/html,application/xhtml+xml,text/plain,text/markdown",
        "User-Agent": WEB_FETCH_USER_AGENT,
    }


def _response_content_type(response: httpx.Response) -> str:
    value = response.headers.get("content-type", "").split(";", 1)[0].casefold().strip()
    # Normalize structured JSON variants (application/vnd.github+json etc.)
    # to the plain JSON lane so API endpoints are fetchable as text.
    if value.endswith("+json"):
        return "application/json"
    return value


def _read_response_body(response: httpx.Response, max_bytes: int) -> bytes:
    payload = bytearray()
    for chunk in response.iter_bytes():
        payload.extend(chunk)
        if len(payload) > max_bytes:
            raise WebFetchError("web fetch response exceeds the configured byte limit")
    return bytes(payload)


def _title_from_html(html: str) -> str:
    matched = _TITLE.search(html)
    if not matched:
        return ""
    return re.sub(r"\\s+", " ", matched.group(1)).strip()[:MAX_WEB_FETCH_TITLE_CHARS]


def _should_render(html: str, markdown: str) -> bool:
    if not markdown.strip():
        return True
    if len(markdown) < WEB_FETCH_EXTRACT_MIN_CHARS:
        return True
    return "<noscript" in html.casefold()


def _render_with_chromium(url: str, timeout_seconds: float) -> str:
    """Render a JS-heavy page with the image's Chromium-only Playwright runtime.

    Runs in the same container under the same egress proxy: the browser's only
    outbound path is the proxy, a non-persistent launch keeps zero browser state
    (no user cookies), downloads are cancelled, and the produced HTML is bounded.
    A render failure never falls back to arbitrary fetching — it fails the task.
    """
    if not _proxy_is_configured():
        raise WebFetchError("web render requires the sandbox egress proxy")
    timeout = min(float(timeout_seconds), WEB_FETCH_RENDER_TIMEOUT_SECONDS)
    profile_root = "/workspace"
    out_path = f"{profile_root}/render-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]}.html"
    completed = subprocess.run(
        [
            "node",
            WEB_FETCH_RENDER_SCRIPT,
            url,
            str(timeout),
            out_path,
            profile_root,
        ],
        capture_output=True,
        timeout=min(int(timeout) + 30, 90),
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).decode("utf-8", errors="replace").strip()[:2_000]
        raise WebFetchError(f"web render failed: {detail or completed.returncode}")
    try:
        html = Path(out_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise WebFetchError("web render produced no output") from exc
    if not html.strip():
        raise WebFetchError("web render produced empty output")
    return html


def web_fetch(
    spec_path: Path,
    *,
    transport: httpx.BaseTransport | None = None,
    render: bool = True,
    render_impl=None,
) -> dict[str, Any]:
    """Fetch approved public content through the sandbox's mandatory proxy.

    DNS/public-address classification is deliberately not done here: this fixed
    runner has no trusted resolver. The M2 egress proxy performs that check at
    CONNECT time and is the only route to the network. This runner still checks
    every redirect's HTTPS authority against the host-owned allowlist.

    When the static HTML yields little or no main text (JS-heavy / SPA page),
    ``render`` triggers the in-container Chromium fallback and the rendered HTML
    goes through the same trafilatura extraction pipeline. ``render_impl`` is an
    injectable stand-in for tests; production uses the image's Chromium.
    """
    spec = load_web_fetch_spec(spec_path)
    policy_digest = _egress_policy_digest()
    if transport is None:
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if not proxy_url:
            raise WebFetchError("web fetch requires the sandbox egress proxy")
        if not policy_digest:
            raise WebFetchError("web fetch requires the egress policy digest")
        # httpx does not send custom headers on CONNECT, so the policy digest
        # rides the standard Proxy-Authorization: Basic channel instead
        # (username = digest) — the same channel web_render.js uses. The proxy
        # URL is backend-injected via HTTPS_PROXY; no direct socket is ever
        # opened.
        transport = httpx.HTTPTransport(
            proxy=httpx.Proxy(
                proxy_url,
                auth=(policy_digest, ""),
            )
        )
    current_url = spec.url
    response: httpx.Response | None = None
    try:
        with httpx.Client(
            headers=_fetch_headers(),
            timeout=spec.timeout_seconds,
            transport=transport,
            follow_redirects=False,
        ) as client:
            for redirect_count in range(spec.max_redirects + 1):
                response = client.get(current_url)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise WebFetchError("web fetch redirect has no location")
                    if redirect_count >= spec.max_redirects:
                        raise WebFetchError("web fetch exceeded the redirect limit")
                    current_url = _validated_fetch_url(urljoin(current_url, location), spec.allowed_domains)
                    continue
                if not response.is_success:
                    raise WebFetchError(f"web fetch upstream returned HTTP {response.status_code}")
                break
            else:  # pragma: no cover - loop always breaks or raises
                raise WebFetchError("web fetch did not receive a response")
            content_type = _response_content_type(response)
            if content_type not in ALLOWED_WEB_FETCH_CONTENT_TYPES:
                raise WebFetchError("web fetch response content type is not permitted")
            body = _read_response_body(response, spec.max_bytes)
    except httpx.TimeoutException as exc:
        raise WebFetchError("web fetch timed out") from exc
    except httpx.HTTPError as exc:
        raise WebFetchError("web fetch transport failed") from exc
    if response is None:  # pragma: no cover - defensive type narrowing
        raise WebFetchError("web fetch did not receive a response")
    text = body.decode(response.encoding or "utf-8", errors="replace")
    render_reason = None
    if content_type in {"text/plain", "text/markdown", "application/json"}:
        markdown = text.strip()
        title = ""
    else:
        markdown = trafilatura.extract(text, output_format="markdown", include_links=True) or ""
        title = _title_from_html(text)
    markdown = markdown.strip()
    extracted_by = "trafilatura"
    render_failed = None
    if render and content_type not in {"text/plain", "text/markdown", "application/json"}:
        if _should_render(text, markdown):
            render_reason = (
                "empty"
                if not markdown
                else ("below_min_chars" if len(markdown) < WEB_FETCH_EXTRACT_MIN_CHARS else "noscript")
            )
            try:
                rendered_html = (
                    render_impl(current_url, spec.timeout_seconds)
                    if render_impl is not None
                    else _render_with_chromium(current_url, spec.timeout_seconds)
                )
                markdown = (
                    trafilatura.extract(rendered_html, output_format="markdown", include_links=True)
                    or ""
                ).strip()
                extracted_by = "chromium"
                # Rendering implies the page actually needed JS; the static title
                # is usually a shell — prefer the rendered document's title.
                rendered_title = _title_from_html(rendered_html)
                if rendered_title:
                    title = rendered_title
            except WebFetchError:
                # Chromium unavailable or failed: degrade to the thin static
                # extraction instead of failing the whole fetch. Only fail hard
                # when there is no content at all. This never falls back to an
                # unauthorized target — the static content is already within the
                # approved allowlist.
                if not markdown:
                    raise
                render_failed = True
    if not markdown:
        raise WebFetchError("web fetch response contains no extractable text")
    truncated = len(markdown) > MAX_TEXT_CHARS
    markdown = markdown[:MAX_TEXT_CHARS]
    return {
        "schema_version": "1.0",
        "task_type": "web_fetch",
        "status": "ok",
        "source_url": spec.url,
        "final_url": current_url,
        "title": title,
        "markdown": markdown,
        "content_type": "text/markdown",
        "extracted_by": extracted_by,
        "render_reason": render_reason,
        "render_failed": render_failed,
        "truncated": truncated,
        "spec_sha256": spec.spec_sha256,
        "content_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    }


def safe_relative(value: str) -> Path:
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError("path must remain inside the sandbox workspace")
    return Path("/workspace", *candidate.parts)


def render_component(path: Path) -> dict:
    payload = path.read_bytes()
    if len(payload) > MAX_PREVIEW_CHARS:
        raise ValueError("preview document exceeds the renderer size limit")
    document = payload.decode("utf-8-sig", errors="strict")
    # The server-owned preview must stay fully inert: no external fetch, no
    # remote scripts.  Any violation fails the render (the caller degrades to
    # the unavailable baseline rather than weakening it).
    if _EXTERNAL_SCHEME.search(document):
        raise ValueError("preview document contains an external http(s) reference")
    if _SCRIPT_SRC.search(document) or "<script>" in document.casefold():
        raise ValueError("preview document contains a script element")
    if "Content-Security-Policy" not in document:
        raise ValueError("preview document is missing the server-owned CSP")
    return {
        "schema_version": "1.0",
        "task_type": "render_component",
        "status": "ok",
        "valid": True,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def mcp_stdio(request_path: Path, launch_path: Path, target: Path) -> dict:
    """One-shot JSON-RPC over stdio inside the isolated container.

    The FastAPI host never spawns the third-party server command; only this
    fixed, reviewed task in the offline container may launch it, subject to the
    executable allowlist, argument bound, request/response size limits and a
    hard timeout carried in the immutable launch spec.
    """

    launch_payload = json.loads(launch_path.read_bytes().decode("utf-8"))
    command = launch_payload.get("command")
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise ValueError("MCP launch command must be a non-empty string list")
    max_args = int(launch_payload.get("max_args") or 16)
    if len(command) > max_args:
        raise ValueError("MCP launch command exceeds the argument bound")
    executable = PurePosixPath(command[0].replace("\\", "/")).name.casefold()
    if executable not in MCP_SERVER_EXECUTABLES:
        raise ValueError("MCP server executable is not in the runner allowlist")

    request_bytes = request_path.read_bytes()
    if not request_bytes:
        raise ValueError("MCP request is empty")
    if len(request_bytes) > MAX_MCP_REQUEST_BYTES:
        raise ValueError("MCP request exceeds the size limit")
    timeout_seconds = float(launch_payload.get("timeout_seconds") or 60)
    completed = subprocess.run(
        command,
        input=request_bytes,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise ValueError(f"MCP server exited {completed.returncode}: {detail}")
    if len(completed.stdout) > MAX_MCP_RESPONSE_BYTES:
        raise ValueError("MCP response exceeds the size limit")
    try:
        parsed = json.loads(completed.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("MCP server returned non-JSON output") from exc
    if not isinstance(parsed, dict):
        raise ValueError("MCP server returned a non-object JSON-RPC response")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
    return {
        "schema_version": "1.0",
        "task_type": "mcp_stdio",
        "status": "ok",
        "bytes": len(completed.stdout),
    }


def inspect_file(path: Path) -> dict:
    payload = path.read_bytes()
    return {
        "schema_version": "1.0",
        "task_type": "file_inspect",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "detected_mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    }


def extract_inert_text(path: Path) -> dict:
    payload = path.read_bytes()
    if b"\x00" in payload[:8_192]:
        raise ValueError("binary content is not accepted by the inert text runner")
    text = payload.decode("utf-8-sig", errors="strict")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("text output exceeds the runner limit")
    return {
        "schema_version": "1.0",
        "task_type": "extract_inert_text",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "text": text,
        "locator": "document",
    }


def extract_legacy_doc(path: Path) -> dict:
    payload = path.read_bytes()
    completed = subprocess.run(
        ["/usr/bin/antiword", "-m", "UTF-8.txt", str(path)],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:2_000]
        raise ValueError(f"antiword failed (exit {completed.returncode}): {detail}")
    text = completed.stdout.decode("utf-8", errors="strict").strip()
    if not text:
        raise ValueError("legacy Word document contains no extractable text")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("text output exceeds the runner limit")
    return {
        "schema_version": "1.0",
        "task_type": "extract_legacy_doc",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "text": text,
        "locator": "document",
        "parser_name": "antiword",
        "parser_version": "0.37",
    }


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument(
        "--task",
        choices=(
            "file_inspect",
            "extract_inert_text",
            "extract_legacy_doc",
            "render_component",
            "mcp_stdio",
            "web_fetch",
        ),
        required=True,
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--spec")
    args = parser.parse_args()
    source = safe_relative(args.input)
    target = safe_relative(args.output)
    if args.task == "web_fetch":
        # The spec is the only input; it is written by the host and carries a
        # content digest. The fixed task never reads a generic agent file.
        result = web_fetch(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return 0
    if not source.is_file():
        raise ValueError("authorized input file does not exist")
    if args.task == "file_inspect":
        result = inspect_file(source)
    elif args.task == "extract_inert_text":
        result = extract_inert_text(source)
    elif args.task == "extract_legacy_doc":
        result = extract_legacy_doc(source)
    elif args.task == "render_component":
        result = render_component(source)
    elif args.task == "mcp_stdio":
        if not args.spec:
            raise ValueError("mcp_stdio requires a launch spec file")
        result = mcp_stdio(source, safe_relative(args.spec), target)
        # ``mcp_stdio`` already wrote the JSON-RPC response to ``target``; the
        # fixed-task summary goes to stdout only, so the caller reads exactly
        # the server response instead of a wrapper artifact.
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    else:
        raise ValueError(f"unknown task: {args.task}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
