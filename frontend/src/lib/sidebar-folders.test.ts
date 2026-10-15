/**
 * 左侧会话窗「图谱文件夹」折叠状态。
 *
 * 三条口径必须有测试钉住，否则默认值一改就会静默回退成「全展开」：
 *   1. 缺省 = 折叠（含设置没加载、值是脏数据的情况）；
 *   2. 开合写回 `ui.preferences` 时必须与 theme 等字段合并，不能整键覆盖；
 *   3. 当前会话所在文件夹临时展开，但用户手动折叠后本次访问不再自动展开。
 */

import { describe, expect, it } from "vitest";

import {
  readSidebarFolderState,
  readSidebarProjectFolders,
  resolveSidebarProjectOpen,
  setSidebarFolderOpen,
  sidebarFolderStateKey,
  SIDEBAR_FOLDER_STATE_STORAGE_KEY,
  SIDEBAR_PROJECT_FOLDERS_FIELD,
  UI_PREFERENCES_SETTING_KEY,
  withSidebarProjectFolder,
  writeSidebarFolderState,
} from "./sidebar-folders";

import type { WorkspaceSetting } from "@/types/settings";

const settings = (value: unknown): WorkspaceSetting[] =>
  [{ key: UI_PREFERENCES_SETTING_KEY, value } as WorkspaceSetting];

describe("sidebar-folders · 读取入库状态", () => {
  it("设置 key 与字段名固定", () => {
    expect(UI_PREFERENCES_SETTING_KEY).toBe("ui.preferences");
    expect(SIDEBAR_PROJECT_FOLDERS_FIELD).toBe("sidebar_project_folders");
  });

  it("设置未加载 / 无该键 / 字段缺失都视为全部折叠", () => {
    expect(readSidebarProjectFolders(undefined)).toEqual({});
    expect(readSidebarProjectFolders([])).toEqual({});
    expect(readSidebarProjectFolders(settings({ theme: "dark" }))).toEqual({});
    expect(
      readSidebarProjectFolders(settings({ [SIDEBAR_PROJECT_FOLDERS_FIELD]: null })),
    ).toEqual({});
  });

  it("只看布尔值，脏数据（字符串 / 数组 / 非对象）一律丢弃", () => {
    expect(
      readSidebarProjectFolders(
        settings({
          [SIDEBAR_PROJECT_FOLDERS_FIELD]: {
            A: false,
            B: true,
            C: "true",
            D: 1,
          },
        }),
      ),
    ).toEqual({ A: false, B: true });
    expect(
      readSidebarProjectFolders(
        settings({ [SIDEBAR_PROJECT_FOLDERS_FIELD]: ["A"] }),
      ),
    ).toEqual({});
    expect(readSidebarProjectFolders(settings("dark"))).toEqual({});
  });
});

describe("sidebar-folders · 写回合并", () => {
  it("保留同键下的其它字段（theme）", () => {
    expect(
      withSidebarProjectFolder({ theme: "dark" }, "A", false),
    ).toEqual({ theme: "dark", [SIDEBAR_PROJECT_FOLDERS_FIELD]: { A: false } });
  });

  it("保留其它文件夹的既有状态", () => {
    expect(
      withSidebarProjectFolder(
        { [SIDEBAR_PROJECT_FOLDERS_FIELD]: { A: false, D: false } },
        "B",
        true,
      ),
    ).toEqual({
      [SIDEBAR_PROJECT_FOLDERS_FIELD]: { A: false, D: false, B: true },
    });
  });

  it("当前值非法（未配置 / 数组 / 字符串）也能写出合法对象", () => {
    expect(withSidebarProjectFolder(undefined, "A", true)).toEqual({
      [SIDEBAR_PROJECT_FOLDERS_FIELD]: { A: true },
    });
    expect(withSidebarProjectFolder(["x"], "A", true)).toEqual({
      [SIDEBAR_PROJECT_FOLDERS_FIELD]: { A: true },
    });
    expect(
      withSidebarProjectFolder({ [SIDEBAR_PROJECT_FOLDERS_FIELD]: "oops" }, "A", false),
    ).toEqual({ [SIDEBAR_PROJECT_FOLDERS_FIELD]: { A: false } });
  });
});

describe("sidebar-folders · 开合判定", () => {
  const none = {};

  it("没有任何记录时默认折叠", () => {
    expect(
      resolveSidebarProjectOpen({
        projectId: "A",
        activeProjectId: undefined,
        overrides: none,
        persisted: none,
      }),
    ).toBe(false);
  });

  it("入库的展开 / 折叠都生效", () => {
    expect(
      resolveSidebarProjectOpen({
        projectId: "A",
        overrides: none,
        persisted: { A: true },
      }),
    ).toBe(true);
    expect(
      resolveSidebarProjectOpen({
        projectId: "A",
        overrides: none,
        persisted: { A: false },
      }),
    ).toBe(false);
  });

  it("当前会话所在文件夹即使入库为折叠也临时展开", () => {
    expect(
      resolveSidebarProjectOpen({
        projectId: "A",
        activeProjectId: "A",
        overrides: none,
        persisted: { A: false },
      }),
    ).toBe(true);
  });

  it("手动决定优先于自动展开：折叠当前会话所在文件夹后本次访问保持折叠", () => {
    expect(
      resolveSidebarProjectOpen({
        projectId: "A",
        activeProjectId: "A",
        overrides: { A: false },
        persisted: { A: true },
      }),
    ).toBe(false);
  });

  it("手动展开一个没在看的文件夹，本次访问保持展开", () => {
    expect(
      resolveSidebarProjectOpen({
        projectId: "B",
        activeProjectId: "A",
        overrides: { B: true },
        persisted: none,
      }),
    ).toBe(true);
  });
});

describe("sidebar-folders · 前端本地状态", () => {
  it("按文件夹类型生成互不冲突的 key", () => {
    expect(sidebarFolderStateKey("project", "same-id")).toBe(
      "project:same-id",
    );
    expect(sidebarFolderStateKey("session", "same-id")).toBe(
      "session:same-id",
    );
  });

  it("按工作区隔离，并保留项目与父会话两类文件夹", () => {
    writeSidebarFolderState("workspace-a", {
      [sidebarFolderStateKey("project", "p1")]: false,
      [sidebarFolderStateKey("session", "s1")]: true,
    });
    writeSidebarFolderState("workspace-b", {
      [sidebarFolderStateKey("project", "p1")]: true,
    });

    expect(readSidebarFolderState("workspace-a")).toEqual({
      [sidebarFolderStateKey("project", "p1")]: false,
      [sidebarFolderStateKey("session", "s1")]: true,
    });
    expect(readSidebarFolderState("workspace-b")).toEqual({
      [sidebarFolderStateKey("project", "p1")]: true,
    });
    expect(window.localStorage.getItem(SIDEBAR_FOLDER_STATE_STORAGE_KEY)).toBeTruthy();
  });

  it("单个切换会写回 localStorage，并忽略损坏的存储值", () => {
    window.localStorage.setItem(
      SIDEBAR_FOLDER_STATE_STORAGE_KEY,
      JSON.stringify({
        "workspace-a": {
          [sidebarFolderStateKey("project", "p1")]: "yes",
          [sidebarFolderStateKey("session", "s1")]: true,
        },
      }),
    );

    expect(readSidebarFolderState("workspace-a")).toEqual({
      [sidebarFolderStateKey("session", "s1")]: true,
    });
    expect(
      setSidebarFolderOpen("workspace-a", "project", "p1", false),
    ).toEqual({
      [sidebarFolderStateKey("session", "s1")]: true,
      [sidebarFolderStateKey("project", "p1")]: false,
    });
    expect(readSidebarFolderState("workspace-a")).toEqual({
      [sidebarFolderStateKey("session", "s1")]: true,
      [sidebarFolderStateKey("project", "p1")]: false,
    });
  });
});
