/**
 * 左侧会话窗文件夹（项目与父会话）的开合状态。
 *
 * 口径
 * ----
 * 1. 默认折叠：从未点过的文件夹一律按折叠渲染。
 * 2. 项目文件夹与父会话文件夹共用 `localStorage` 状态，并按工作区隔离，
 *    所以「A、D 折叠，B、C 展开」这种组合刷新后仍然成立。
 * 3. 当前会话或搜索结果可以临时把相关文件夹展示出来；用户明确折叠后，
 *    本地记录优先，不会被自动展开逻辑顶回去。
 *
 * 文件中仍保留 `ui.preferences` 的读写辅助函数，兼容已有设置数据和旧测试；
 * 侧栏运行时使用下方的本地状态 API，不再为折叠操作发起设置接口请求。
 */

import type { WorkspaceSetting } from "@/types/settings";

export const UI_PREFERENCES_SETTING_KEY = "ui.preferences";

/** `ui.preferences` 内存项目文件夹开合表的字段名。 */
export const SIDEBAR_PROJECT_FOLDERS_FIELD = "sidebar_project_folders";

/** projectId -> 是否展开；缺键 = 折叠。 */
export type SidebarProjectFolders = Record<string, boolean>;

/**
 * 所有左侧会话文件夹共用的本地状态。
 *
 * 项目文件夹和带附属会话的父会话都使用同一个布尔映射，只通过 key
 * 前缀区分类型。这样侧栏不需要为不同层级维护两套折叠逻辑。
 */
export type SidebarFolderState = Record<string, boolean>;

export type SidebarFolderKind = "project" | "session" | "section";

export type SidebarFolderRef = {
  kind: SidebarFolderKind;
  id: string;
};

/** localStorage 中保存侧栏文件夹状态的根键。 */
export const SIDEBAR_FOLDER_STATE_STORAGE_KEY =
  "learngraph:sidebar-folder-state";

/** Same-tab notification used when both desktop and mobile drawers are mounted. */
export const SIDEBAR_FOLDER_STATE_EVENT =
  "learngraph:sidebar-folder-state-changed";

/** 生成一个不会与另一类文件夹冲突的本地状态 key。 */
export function sidebarFolderStateKey(
  kind: SidebarFolderKind,
  id: string,
): string {
  return `${kind}:${id}`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/**
 * 读取入库的开合表。设置未加载、值非法或字段缺失都返回空表（= 全部折叠），
 * 绝不因为读到脏数据就把文件夹默认展开。
 */
export function readSidebarProjectFolders(
  settings: WorkspaceSetting[] | undefined,
): SidebarProjectFolders {
  if (!Array.isArray(settings)) return {};
  const preferences = asRecord(
    settings.find((item) => item.key === UI_PREFERENCES_SETTING_KEY)?.value,
  );
  const raw = asRecord(preferences?.[SIDEBAR_PROJECT_FOLDERS_FIELD]);
  if (!raw) return {};
  const folders: SidebarProjectFolders = {};
  for (const [projectId, open] of Object.entries(raw)) {
    if (typeof open === "boolean") folders[projectId] = open;
  }
  return folders;
}

/**
 * 把一次开合合并回 `ui.preferences`，保留同键下的其它字段（theme 等）。
 * 返回值即 `PUT /settings/ui.preferences` 的请求体。
 */
export function withSidebarProjectFolder(
  preferences: unknown,
  projectId: string,
  open: boolean,
): Record<string, unknown> {
  const current = asRecord(preferences) ?? {};
  const folders: Record<string, unknown> = {
    ...(asRecord(current[SIDEBAR_PROJECT_FOLDERS_FIELD]) ?? {}),
  };
  folders[projectId] = open;
  return { ...current, [SIDEBAR_PROJECT_FOLDERS_FIELD]: folders };
}

/**
 * 侧栏某一刻的开合判定，优先级从高到低：
 *   1. `overrides`：本次访问内用户手动开合过（已同步入库，但本地立即生效用它）；
 *   2. 当前会话所在文件夹 → 自动展开；
 *   3. `persisted`：入库状态，缺省折叠。
 *
 * 手动折叠当前会话所在文件夹会让 `overrides[projectId] = false` 在第 1 步直接
 * 命中，因此自动展开不会再把它顶开。
 */
export function resolveSidebarProjectOpen(options: {
  projectId: string;
  activeProjectId?: string | null;
  overrides: SidebarProjectFolders;
  persisted: SidebarProjectFolders;
}): boolean {
  const override = options.overrides[options.projectId];
  if (typeof override === "boolean") return override;
  if (options.activeProjectId && options.projectId === options.activeProjectId) {
    return true;
  }
  return options.persisted[options.projectId] === true;
}

function readLocalStorageRoot(): Record<string, unknown> {
  try {
    if (typeof window === "undefined" || !window.localStorage) return {};
    const raw = window.localStorage.getItem(SIDEBAR_FOLDER_STATE_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    return asRecord(parsed) ?? {};
  } catch {
    // Private browsing, malformed JSON, and quota/security errors are all
    // best-effort cases: the caller can still keep the current state in RAM.
    return {};
  }
}

function normalizeBooleanMap(value: unknown): SidebarFolderState {
  const record = asRecord(value);
  if (!record) return {};
  const normalized: SidebarFolderState = {};
  for (const [key, open] of Object.entries(record)) {
    if (typeof open === "boolean") normalized[key] = open;
  }
  return normalized;
}

/**
 * Read the current workspace's local folder state.
 *
 * The workspace id is part of the persisted namespace so changing workspaces
 * never makes one workspace's project/session folders affect another one.
 */
export function readSidebarFolderState(
  workspaceId: string,
): SidebarFolderState {
  if (!workspaceId) return {};
  const root = readLocalStorageRoot();
  const scoped = root[workspaceId];
  return normalizeBooleanMap(scoped);
}

/** Persist one workspace's complete folder state without touching others. */
export function writeSidebarFolderState(
  workspaceId: string,
  state: SidebarFolderState,
): void {
  if (!workspaceId) return;
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    const root = readLocalStorageRoot();
    root[workspaceId] = { ...state };
    window.localStorage.setItem(
      SIDEBAR_FOLDER_STATE_STORAGE_KEY,
      JSON.stringify(root),
    );
  } catch {
    // Quota/private-mode failures intentionally leave the in-memory state
    // usable for the current page.
  }
}

/**
 * Update one folder and return the complete next state. The helper is useful
 * to callers that want a synchronous state/ref update before React re-renders.
 */
export function setSidebarFolderOpen(
  workspaceId: string,
  kind: SidebarFolderKind,
  id: string,
  open: boolean,
): SidebarFolderState {
  const next = {
    ...readSidebarFolderState(workspaceId),
    [sidebarFolderStateKey(kind, id)]: open,
  };
  writeSidebarFolderState(workspaceId, next);
  try {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new Event(SIDEBAR_FOLDER_STATE_EVENT));
    }
  } catch {
    // Event dispatch is only a same-tab convenience; persistence already ran.
  }
  return next;
}
