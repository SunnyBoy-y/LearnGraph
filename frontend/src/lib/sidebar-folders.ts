/**
 * 左侧会话窗「图谱文件夹」（项目文件夹）的开合状态。
 *
 * 口径
 * ----
 * 1. 默认折叠：从未点过的文件夹（含升级前的老工作区）一律按折叠渲染。
 * 2. 状态入库：用户每次手动开合都写回工作区设置 `ui.preferences`
 *    （`sidebar_project_folders` 字段，形如 `{ "<projectId>": false }`），
 *    所以「A、D 折叠，B、C 展开」这种组合重启后仍然成立。
 * 3. 临时自动展开：当前正在看的会话所属文件夹若已折叠，本次访问内自动展开；
 *    用户一旦手动折叠它，本次访问就不再自动展开（手动决定优先，见
 *    `resolveSidebarProjectOpen`）。它不改写入库状态，刷新后回到入库结果。
 *
 * 与 `theme` 共用 `ui.preferences` 一个键，所以写回必须整体合并而不是覆盖。
 */

import type { WorkspaceSetting } from "@/types/settings";

export const UI_PREFERENCES_SETTING_KEY = "ui.preferences";

/** `ui.preferences` 内存项目文件夹开合表的字段名。 */
export const SIDEBAR_PROJECT_FOLDERS_FIELD = "sidebar_project_folders";

/** projectId -> 是否展开；缺键 = 折叠。 */
export type SidebarProjectFolders = Record<string, boolean>;

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
  const preferences = asRecord(
    settings?.find((item) => item.key === UI_PREFERENCES_SETTING_KEY)?.value,
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
