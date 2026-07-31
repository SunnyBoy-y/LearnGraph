from __future__ import annotations

"""Static/source regressions for frontend P0 client isolation helpers."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "frontend" / "src"


def test_auth_logout_clears_query_cache_and_selection_privacy() -> None:
    auth_context = (FRONTEND / "features" / "auth" / "auth-context.tsx").read_text(
        encoding="utf-8"
    )
    assert "clearAuthenticatedClientState" in auth_context
    assert "clearSelectionExplanations" in auth_context
    assert "async logout()" in auth_context
    # logout finally block must clear both selection privacy and query cache
    logout_idx = auth_context.index("async logout()")
    logout_block = auth_context[logout_idx : logout_idx + 500]
    assert "clearSelectionExplanations()" in logout_block
    assert "clearAuthenticatedClientState()" in logout_block
    assert "authStore.clear()" in logout_block


def test_workspace_route_guard_syncs_url_workspace_before_render() -> None:
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert "function WorkspaceRouteGuard" in app
    assert "setWorkspaceId(workspaceId)" in app
    assert "workspaceId !== activeWorkspaceId" in app
    assert "X-Workspace-ID" in (FRONTEND / "api" / "client.ts").read_text(encoding="utf-8")


def test_browser_file_hash_skips_large_files() -> None:
    source = (FRONTEND / "lib" / "file-hash.ts").read_text(encoding="utf-8")
    assert "MAX_BROWSER_HASH_BYTES = 16 * 1024 * 1024" in source
    assert "if (file.size > MAX_BROWSER_HASH_BYTES) return null" in source


def test_preview_download_caps_large_files_via_range() -> None:
    """R-012: in-browser preview must not load >16 MiB files into memory."""

    files_api = (FRONTEND / "api" / "files.ts").read_text(encoding="utf-8")
    assert "downloadFileForPreview" in files_api
    assert "PREVIEW_MAX_BYTES = 16 * 1024 * 1024" in files_api
    assert "getBlobRange" in files_api

    client = (FRONTEND / "api" / "client.ts").read_text(encoding="utf-8")
    assert "getBlobRange" in client
    assert "Range" in client

    learning_page = (
        FRONTEND / "features" / "resources" / "document-learning-page.tsx"
    ).read_text(encoding="utf-8")
    assert "downloadFileForPreview" in learning_page
    assert "file.size_bytes" in learning_page


def test_dev_listen_defaults_to_loopback() -> None:
    dev = (ROOT / "scripts" / "dev.mjs").read_text(encoding="utf-8")
    vite = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert "options.lan ? '0.0.0.0' : '127.0.0.1'" in dev
    assert "LEARNGRAPH_LISTEN_HOST" in dev
    assert "host: process.env.LEARNGRAPH_LISTEN_HOST?.trim() || '127.0.0.1'" in vite


def test_agentic_send_does_not_hard_block_without_docker() -> None:
    """R-007: agentic conversation must degrade, not refuse, when sandbox is down."""

    chat_pages = (FRONTEND / "features" / "chat" / "chat-pages.tsx").read_text(
        encoding="utf-8"
    )
    ensure_idx = chat_pages.index("const ensureAgentSandboxReady")
    ensure_block = chat_pages[ensure_idx : ensure_idx + 1200]
    assert "return true" in ensure_block
    assert "沙箱工具暂不可用" in ensure_block or "sandbox tools" in ensure_block.lower()
    # Hard-fail path must not remain as the only branch.
    assert ensure_block.count("return false") == 0

    selection = (
        FRONTEND / "features" / "chat" / "selection-explanation-panel.tsx"
    ).read_text(encoding="utf-8")
    assert "沙箱工具暂不可用" in selection
    doc = (FRONTEND / "features" / "resources" / "document-chat-panel.tsx").read_text(
        encoding="utf-8"
    )
    assert "文档问答仍可继续" in doc
