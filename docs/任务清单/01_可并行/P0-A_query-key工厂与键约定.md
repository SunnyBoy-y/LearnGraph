# 任务 P0-A｜query-key 工厂与身份/工作区键约定

## 元信息块

```text
并行性   : 可并行（独享 frontend/src/lib/query-keys.ts；有机会改 auth-query-cache.ts 但建议缓一缓，见边界）
状态     : 已完成（2026-08-02）
主要文件 : frontend/src/lib/query-keys.ts
依赖     : 无（可立即开）
口音标注 : 无
```

## >> 背景与目标

**背景（为什么做这个）**

Roapmap P0 已经把「工作区作用域 React Query 缓存隔离」定为最高优先级缺口：后端每个请求都带
`X-Workspace-ID`，服务端授权边界存在；但前端大量 query 仍是**裸键**（`["projects"]`、`["sessions"]`、
`["settings"]`、`["graphs"]`……）。裸键在「同一标签页切换工作区」时会读到/乐观更新到**另一个工作区**
的缓存，造成短暂的陈旧数据显示，或让 mutation 失效/更新过宽。现在已经加了一个[`query-keys.ts`](docs/任务清单/01_可并行/../../frontend/src/lib/query-keys.ts)
雏形（`workspaceQueryKey` / `workspaceQueryPrefix` / `identityQueryKey`），本任务把它定稿成**规范约定**，
让后续所有审计任务（P0-B/C/D）都能 import 同一套工具，不再各写各的。

**目标（做成什么样子）**

- `query-keys.ts` 成为**唯一**的工作区/身份 query-key 工厂，导出清晰的函数：
  - `workspaceQueryKey(workspaceId, ...parts)` → `["workspace", workspaceId, ...parts]`
  - `workspaceQueryPrefix(workspaceId)` → `["workspace", workspaceId]`（用于清理一个租户）
  - `identityQueryKey(userId, ...parts)`（身份级、明确不带 workspace 头的端点用）
  - 一个**资源族前缀**小助手 `workspaceResourcePrefix(workspaceId, resource)`（可选）
- 命名上让「工作区段在最前」，保证 `clearWorkspaceClientState`（按前缀 remove）不会跨租户误删。

**完成标准 / 验收条件**

- `frontend/src/lib/query-keys.ts` 无裸键，类型收窄正确，`npm run build` + `tsc` 通过。
- 后续 P0-B/C/D 都能 import 它，工作区段在最前，可以被 `["workspace", workspaceId]` 前缀一次清掉。
- 不要顺手做大范围 grep 替换（那是 P0-B/C/D 的事），本任务只把**工厂与注释约定**做对。

## 现状与风险

- 已有雏形（见 [query-keys.ts](docs/任务清单/01_可并行/frontend/src/lib/query-keys.ts)，工作区段在最前，OK）。
- 风险：如果有人把 `identityQueryKey` 用错（给带 workspace 头的请求用），会导致跨租户缓存串味。
  注释里要把「带 `X-Workspace-ID` 的请求 → 用 workspace 键；明确不带 → 用 identity 键」写清楚。

## 实施范围

- [x] 复核 `query-keys.ts` 现有 4 个函数、注释、段落格式，补齐缺失（如为 `identityQueryKey` 加
      「此类端点原因」的注释模板）。
- [x] 在文件顶部写一段「本文件是不可并行的共享基石，改动优先提交」的维护约定（简短，不啰嗦）。
- [x] 确认没有别处 inline 重复实现；把 `workspace-shell.tsx` 已 import 的 `workspaceQueryKey` 对齐到本工厂（只对齐 import，不做全文件替换）。
- [x] 可选项：`frontend/src/lib/auth-query-cache.ts` 的 `clearWorkspaceClientState` 目前按
      `["workspace", workspaceId]` remove —— 确认与 `workspaceQueryPrefix` 一致；若本次有冲突则**暂不动它**，
      留到 P0-D 统一，只因它属于清理治理范畴（见边界）。

## 与其他任务的边界（防冲突）

- **只改** `frontend/src/lib/query-keys.ts`。
- 不要碰 `chat-pages.tsx` / `graph-pages.tsx` / `workspace-shell.tsx` 里的裸键（那是 P0-B/C/D）。
- `auth-query-cache.ts` 只在「同步工作区前缀约定」这层做小改；凡涉及登出/401/账户清理大改，归 P0-D。

## 验收条件

- [x] `frontend` `npm run build` 与 `tsc` 通过。
- [x] `clearWorkspaceClientState` 的 remove 前缀与 `workspaceQueryPrefix` 一致（或在文件中说明差异原因）。
- [x] 文件注释足以让 P0-B/C/D 的鼠标手一眼确认该用哪个键。

## 产出物交付给谁

- 后续所有审计任务（P0-B / P0-C / P0-D）。所以「优先落盘」很重要。