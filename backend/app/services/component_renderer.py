from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.providers.remote.sandbox import DockerSandboxBackend
from app.providers.ports.sandbox import SandboxCreateSpec
from app.services.sandbox_runtime import resolve_sandbox_image_for_runtime

# The preview document is a fixed, server-owned template. Third-party component
# data is schema-validated inert JSON rendered into an HTML-escaped <pre>; no
# component-supplied HTML, JavaScript, CSS or URL is ever embedded. The host
# renders this through the existing opaque-origin iframe
# (``sandbox="allow-scripts"``, no ``allow-same-origin``, ``connect-src 'none'``).
COMPONENT_PREVIEW_CSP = (
    "default-src 'none'; "
    "img-src data: blob:; media-src data: blob:; font-src data:; "
    "style-src 'unsafe-inline'; "
    "script-src 'none'; worker-src 'none'; connect-src 'none'; "
    "frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
)
MAX_PREVIEW_BYTES = 100_000
RENDER_TASK = ("python", "/opt/learngraph/runner.py")
RUNTIME_KIND = "python-node-browser"


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def build_component_preview_html(
    *,
    component_id: str,
    version: str,
    display_name: str,
    data: dict[str, Any],
    csp: str = COMPONENT_PREVIEW_CSP,
) -> str:
    """Build the inert server-owned preview document for one component artifact."""

    json_text = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    return (
        "<!doctype html>\n"
        "<html lang=\"zh-CN\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\">\n"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{_escape(csp)}\">\n"
        "<style>\n"
        ":root{color-scheme:light dark;font-family:system-ui,sans-serif;}\n"
        "body{margin:0;padding:16px;}\n"
        "header{font-size:14px;opacity:.85;margin-bottom:12px;}\n"
        "pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:13px;}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        f"<header>{_escape(component_id)} v{_escape(version)} — {_escape(display_name)}</header>\n"
        f"<pre>{_escape(json_text)}</pre>\n"
        "</body>\n"
        "</html>\n"
    )


class ComponentRendererService:
    """Render authorized component data through the offline Docker sandbox.

    The HTML template is generated host-side as a fixed inert document; the
    sandbox only validates the document and may produce a bounded screenshot.
    If the sandbox image is not pinned or the backend is unavailable, the
    caller keeps the existing ``sandbox_artifact`` unavailable baseline.
    """

    def __init__(self, workspace_id: str, actor_id: str) -> None:
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = get_settings()

    def renderer_available(self) -> bool:
        if not self.settings.component_renderer_enabled:
            return False
        image_ref = resolve_sandbox_image_for_runtime(self.settings, RUNTIME_KIND)
        if not image_ref:
            return False
        backend = DockerSandboxBackend(
            enabled=self.settings.sandbox_enabled,
            image_ref=image_ref,
            runtime_kind=RUNTIME_KIND,
            archive_bytes=self.settings.sandbox_agent_archive_bytes,
        )
        capability = backend.probe()
        return bool(capability.available)

    def render(self, manifest: Any, data: dict[str, Any]) -> dict[str, Any]:
        """Return the bounded ``sandbox_artifact`` payload for a component."""

        preview_html = build_component_preview_html(
            component_id=manifest.component_id,
            version=manifest.version,
            display_name=manifest.display_name,
            data=data,
        )
        preview_html = preview_html[: self.settings.component_render_preview_chars]

        if not self.renderer_available():
            return {
                "runtime_status": "unavailable",
                "sandbox_executed": False,
                "sandbox_origin": None,
                "screenshot_available": False,
                "reason": "isolated_browser_renderer_not_configured",
                "preview_html": preview_html,
                "csp": COMPONENT_PREVIEW_CSP,
            }

        try:
            screenshot_available = self._sandbox_render(preview_html, manifest)
        except Exception as error:  # noqa: BLE001 - degrade, never break the caller
            return {
                "runtime_status": "unavailable",
                "sandbox_executed": False,
                "sandbox_origin": None,
                "screenshot_available": False,
                "reason": f"isolated_browser_renderer_failed:{type(error).__name__}",
                "preview_html": preview_html,
                "csp": COMPONENT_PREVIEW_CSP,
            }

        return {
            "runtime_status": "rendered",
            "sandbox_executed": True,
            "sandbox_origin": "opaque-iframe",
            "screenshot_available": screenshot_available,
            "reason": None,
            "preview_html": preview_html,
            "csp": COMPONENT_PREVIEW_CSP,
        }

    def _sandbox_render(self, preview_html: str, manifest: Any) -> bool:
        image_ref = resolve_sandbox_image_for_runtime(self.settings, RUNTIME_KIND)
        if not image_ref:
            return False
        backend = DockerSandboxBackend(
            enabled=self.settings.sandbox_enabled,
            image_ref=image_ref,
            runtime_kind=RUNTIME_KIND,
            archive_bytes=self.settings.sandbox_agent_archive_bytes,
        )
        capability = backend.probe()
        if not capability.available:
            return False

        workspace_root = Path(self.settings.sandbox_workspace_root).expanduser().resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        session_id = f"component-render-{manifest.id}"
        handle = backend.create(
            SandboxCreateSpec(
                session_id=session_id,
                image_ref=image_ref,
                memory_bytes=256 * 1024 * 1024,
                memory_swap_bytes=256 * 1024 * 1024,
                cpu_count=1.0,
                pids_max=64,
                disk_bytes=16 * 1024 * 1024,
                workspace_path=str(workspace_root),
                runtime_kind=RUNTIME_KIND,
                egress=None,
            )
        )
        try:
            backend.write(handle, "render.html", preview_html.encode("utf-8"))
            result = backend.exec_fixed(
                handle,
                (*RENDER_TASK, "--task", "render_component", "--input", "render.html", "--output", "render.out.json"),
                timeout_seconds=self.settings.sandbox_wall_time_seconds,
                output_limit=64 * 1024,
            )
            if result.exit_code != 0 or result.timed_out:
                return False
            raw = backend.read(handle, "render.out.json", limit_bytes=64 * 1024)
            rendered = json.loads(raw.decode("utf-8", errors="replace"))
            return bool(rendered.get("status") == "ok" and rendered.get("valid"))
        finally:
            try:
                backend.delete(handle)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
