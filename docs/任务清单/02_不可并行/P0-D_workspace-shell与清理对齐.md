# 任务 P0-D｜workspace-shell 及上下文轨审计 + 切换/登出/401 清理对齐（不可并行）

## 元信息块

```text
并行性   : 不可并行（包含对 workspace-shell.tsx 的大改动，且与其他域全部耦合，必须最后单窗口）
状态     : 已完成（2026-08-02）
主要文件 : frontend/src/components/layout/workspace-shell.tsx、frontend/src/features/auth/auth-context.tsx、frontend/src/lib/auth-query-cache.ts、frontend/src/lib/query-keys.ts（只读/微调）
依赖     : P0-A + P0-B/P0-C 落定后再做（它要统一起清理逻辑，不能抢跑）
口音标注 : 无
```

> 放在 02_不可并行/，且排在 P0-B/C 之后。原因：它要动 `workspace-shell.tsx`（全仓库最大的前端文件）和
> `auth-query-cache.ts`（清理治理）。如果和 P0-B/C 并行，三者都可能改到 `_auth-query-cache_` 或同一批键，
> 冲突概率极高。所以把它规划成 P0 收口任务。

## 背景

TODO —— 结合 memory `workspace-default-response-mode-race`：`createConversation` 必须 `fetchQuery` 后再 seed，
本任务改 settings 相关键时不要把这条链路搞坏。背景=让切换工作区/登出/401/删除账户的缓存清理与新键约定对齐（P0 范围最后一项），
使旧 workspace 缓存绝不会短暂显示。

## 实施范围

- [x] `workspace-shell.tsx` 内全部裸键收敛到 `workspaceQueryKey(...)`（projects/sessions/settings/graphs/goals/mastery/dashboard 等）。
- [x] 上下文轨（ContextRail / GraphWorkspaceRail / BoundGraphRail / CapabilityGraphRail / ProjectBookshelf）query 键对齐。
- [x] 在 `auth-query-cache.ts` 统一切换/登出/401/账户删除的清理逻辑，与 P0-A 的 `workspaceQueryPrefix` 一致；
      移除旧全局键处理。
- [x] 回归：切换/登出/401 不残留别的 workspace 缓存；`clearWorkspaceClientState` 只清一个租户（`test/workspace-cache-isolation.test.ts`：只清前一租户、不动其它租户与 identity 数据）。

## 边界（防冲突）

- 只改上列文件。**不碰** `chat-pages.tsx`（P0-B）、`graph-pages.tsx`（P0-C）。
- `query-keys.ts` 只读（或极小的注释级微调），不重写。
- 必等 P0-B/C 先落地，别并线。

## 验收条件

- [x] `npm run build` + `tsc` 通过。
- [x] 工作区 A 切换/登出/401 后，绝不显示工作区 B 的残留缓存（`workspace-cache-isolation.test.ts`：切换清前一租户、登出全清）。
- [x] 记忆中的 `workspace-default-response-mode-race` 链路仍成立（createConversation fetchQuery→seed 顺序未断，`test_p0_client_isolation_source.py` 断言 workspace_shell 保留 fetchQuery）。