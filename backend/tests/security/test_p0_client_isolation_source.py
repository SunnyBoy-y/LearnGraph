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


def test_selection_explanations_partitioned_by_user_and_workspace() -> None:
    """R-017: private selection history must be isolated per (user, workspace)."""

    source = (FRONTEND / "features" / "chat" / "selection-explanation.ts").read_text(
        encoding="utf-8"
    )

    # Storage key is derived from the authenticated user + active workspace so
    # account A's logout cannot surface in account B's partition.
    assert "STORAGE_KEY_PREFIX" in source and "LEGACY_STORAGE_KEY" in source
    assert "namespaceScope" in source
    assert "authStore.getSession" in source
    assert "storageKeyFor" in source
    assert "currentStorageKey" in source
    # Both dimensions of the partition must be encoded into the key.
    assert "userId" in source and "workspaceId" in source
    assert source.count("learngraph:selection-explanations") >= 2  # prefix + legacy

    # Explicit footprint bound, not "keep forever". The cap prevents runaway
    # local accumulation of private document excerpts on phones / shared desktops.
    assert "MAX_TOTAL_RECORDS" in source
    assert "MAX_STORAGE_BYTES" in source
    assert "MAX_PARENT_SESSIONS" in source
    assert "MAX_RECORDS_PER_SESSION" in source
    assert "boundStorage" in source

    # Legacy pre-namespace key must not be silently absorbed into whoever is
    # logged in now: it is swept once on first read and dropped outright.
    assert "sweepLegacyKey" in source
    assert "legacySwept" in source
    assert 'removeItem(LEGACY_STORAGE_KEY)' in source

    # Per-partition clear (logout / 401) plus a device-wide wipe (account delete).
    assert "function clearSelectionExplanations()" in source
    assert "export function clearAllSelectionExplanations()" in source
    assert "removeItem(currentStorageKey())" in source
    # The device-wide wipe prefixes every partition key, not the active one.
    assert "STORAGE_KEY_PREFIX" in source
    assert source.count("STORAGE_KEY_PREFIX") >= 3  # decl + storageKeyFor + clearAll

    # The old un-partitioned read/write path must be gone.
    assert 'localStorage.getItem(STORAGE_KEY)' not in source
    assert 'localStorage.setItem(STORAGE_KEY' not in source
    assert 'localStorage.removeItem(STORAGE_KEY)' not in source


def test_selection_explanations_cleared_on_logout_401_and_account_delete() -> None:
    """R-017: logout / 401 drop the current partition; account delete wipes all."""

    auth_context = (FRONTEND / "features" / "auth" / "auth-context.tsx").read_text(
        encoding="utf-8"
    )
    # Logout clears the logged-in partition while authStore still resolves it.
    logout_idx = auth_context.index("async logout()")
    logout_block = auth_context[logout_idx : logout_idx + 600]
    assert "clearSelectionExplanations()" in logout_block
    # Account deletion wipes every partition on the device, not just the active one.
    delete_idx = auth_context.index("async deleteAccount")
    delete_block = auth_context[delete_idx : delete_idx + 400]
    assert "clearAllSelectionExplanations()" in delete_block
    assert "clearAllSelectionExplanations" in auth_context

    client = (FRONTEND / "api" / "client.ts").read_text(encoding="utf-8")
    # The 401 path clears selection history BEFORE authStore.clear() so the
    # partition still resolves to the invalidated account.
    assert "clearSelectionExplanations" in client
    on401_idx = client.index('response.status === 401')
    on401_block = client[on401_idx : on401_idx + 600]
    assert "clearSelectionExplanations()" in on401_block
    assert on401_block.index("clearSelectionExplanations()") < on401_block.index(
        "authStore.clear()"
    )


def test_workspace_scoped_query_keys_include_workspace_id() -> None:
    """R-018: workspace-scoped useQuery keys must carry workspaceId to prevent
    cross-workspace cache confusion on the same browser tab."""

    workspace_shell = (
        FRONTEND / "components" / "layout" / "workspace-shell.tsx"
    ).read_text(encoding="utf-8")
    # Both SidebarNav and ContextRail sessions queries include workspaceId.
    assert 'queryKey: ["sessions", workspaceId]' in workspace_shell

    chat_pages = (FRONTEND / "features" / "chat" / "chat-pages.tsx").read_text(
        encoding="utf-8"
    )
    assert 'queryKey: ["sessions", workspaceId]' in chat_pages

    dashboard = (FRONTEND / "features" / "dashboard" / "dashboard-page.tsx").read_text(
        encoding="utf-8"
    )
    assert 'queryKey: ["sessions", workspaceId]' in dashboard

    memory_page = (FRONTEND / "features" / "memory" / "memory-page.tsx").read_text(
        encoding="utf-8"
    )
    assert 'queryKey: [\'memory\', \'active\', workspaceId]' in memory_page
    assert 'queryKey: [\'sessions\', workspaceId]' in memory_page

    sel_panel = (
        FRONTEND / "features" / "chat" / "selection-explanation-panel.tsx"
    ).read_text(encoding="utf-8")
    assert 'queryKey: ["sessions", workspaceId]' in sel_panel

    doc_chat = (
        FRONTEND / "features" / "resources" / "document-chat-panel.tsx"
    ).read_text(encoding="utf-8")
    assert 'queryKey: ["sessions", workspaceId]' in doc_chat

    concept_branch = (
        FRONTEND / "features" / "resources" / "concept-branch-workspace.tsx"
    ).read_text(encoding="utf-8")
    assert 'queryKey: ["sessions", workspaceId]' in concept_branch

    # setQueryData writes to the same partitioned key, not the bare global one.
    assert 'setQueryData(["sessions"],' not in workspace_shell
    assert 'setQueryData<Session[]>(["sessions"],' not in workspace_shell
    assert 'setQueryData(["sessions"],' not in chat_pages
    assert 'setQueryData<Session[]>(["sessions"],' not in chat_pages
    assert 'setQueryData(["sessions"],' not in sel_panel
    assert 'setQueryData<Session[]>(["sessions"],' not in sel_panel
    assert 'setQueryData(["sessions"],' not in doc_chat
    assert 'setQueryData<Session[]>(["sessions"],' not in doc_chat


def test_sandbox_quota_limits_file_and_directory_count() -> None:
    """R-008: workspace quota must also limit file count and directory count,
    not just disk bytes, to prevent inode/directory-bomb DoS."""

    sandbox_adapter = (
        ROOT / "backend" / "app" / "providers" / "remote" / "sandbox.py"
    ).read_text(encoding="utf-8")

    # Structured usage stats replace the old scalar return.
    assert "_WorkspaceUsageStats" in sandbox_adapter
    assert "class _WorkspaceUsageStats" in sandbox_adapter
    assert "bytes: int" in sandbox_adapter
    assert "file_count: int" in sandbox_adapter
    assert "directory_count: int" in sandbox_adapter

    # Quota check consults all three dimensions.
    assert "workspace_limit_files" in sandbox_adapter
    assert "workspace_limit_dirs" in sandbox_adapter
    assert "usage.file_count > limit_files" in sandbox_adapter
    assert "usage.directory_count > limit_dirs" in sandbox_adapter

    # Container labels carry the new limits.
    container_create_idx = sandbox_adapter.index("client.containers.create")
    create_block = sandbox_adapter[container_create_idx : container_create_idx + 1200]
    assert 'workspace_limit_files": "20000"' in create_block
    assert 'workspace_limit_dirs": "5000"' in create_block

    config = (ROOT / "backend" / "app" / "core" / "config.py").read_text(
        encoding="utf-8"
    )
    assert "sandbox_file_count" in config
    assert "sandbox_directory_count" in config
    assert "sandbox_snapshot_reserve_bytes" in config


def test_selection_explanations_has_settings_page_clear_button() -> None:
    """R-017: the settings page must provide a 'clear learning traces on this
    device' button that calls clearSelectionExplanations."""

    personalization = (
        FRONTEND / "features" / "settings" / "personalization-page.tsx"
    ).read_text(encoding="utf-8")
    assert "清除本设备学习痕迹" in personalization
    assert "clearSelectionExplanations" in personalization
    assert "清除记录" in personalization
    assert "variant=\"destructive\"" in personalization
