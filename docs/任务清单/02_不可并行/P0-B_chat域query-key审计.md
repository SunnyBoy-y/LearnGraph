# 任务 P0-B｜chat 域 query-key 审计（不可并行）

## 元信息块

```text
并行性   : 不可并行（集中改 frontend/src/features/chat/chat-pages.tsx 这一大文件，必须单窗口）
状态     : 已完成（2026-08-02）
主要文件 : frontend/src/features/chat/chat-pages.tsx、frontend/src/features/chat/selection-explanation-panel.tsx、frontend/src/features/resources/document-chat-panel.tsx、frontend/src/features/resources/concept-branch-workspace.tsx
依赖     : 必须先等 P0-A 落盘（import 到定稿的 workspaceQueryKey / identityQueryKey）
口音标注 : 无
```

> 位置：放在 02_不可并行/，因为它和 P0-C（graph 域）共用 `workspace-shell` 之外的同一批大文件（chat-pages），
> 而 chat-pages 本身太大，两个窗口同时改必冲突。

## 背景

TODO — 复制 ROADMAP P0「现状与风险」中 chat 相关要点，再按 P0-A 定稿的键做替换。要点：
把 `README.md` 汇总里 chat 域所有裸键（`["sessions", workspaceId]`、`["messages", sessionId]`、
`["graph", activeGraphId]`、`["mastery"]`、`["dashboard", workspaceId]`、`["sessions"]` 等）统一收进工厂。

## 实施范围

- [x] 列出 `chat-pages.tsx` 等 chat 域文件里所有 query 键，分类：
      workspace 资源 → `workspaceQueryKey(workspaceId, ...)`；身份级 → `identityQueryKey(...)`。
- [x] 逐个替换 `useQuery` / `fetchQuery` / `setQueryData` / `invalidateQueries` / 预取；mutation 用同一工厂键。
- [x] 对「明确不需要 workspaceId」的键（如 `["agent-sandbox-readiness"]`、`["sandbox-bootstrap-status"]`、
      `["providers"]`、`["provider-models", id]`、`["message-versions", ...]`）在代码旁注释说明依据，不改成 workspace。
- [x] 回归验证：至少两个工作区切换不再互串；会话增删/乐观更新失效范围正确（`tests/security/test_p0_client_isolation_source.py` 源级固化 + `test/workspace-cache-isolation.test.ts` 行为测试）。

## 边界（防冲突）

- 只改 chat 域文件。**绝对不碰** `workspace-shell.tsx`（P0-D）、`graph-pages.tsx`（P0-C）。
- 若发现需要改 `auth-query-cache.ts` 的清理逻辑，记到 P0-D，不要在这里动。
- 与 P0-A 并行时，等它定稿再 import，别先自己造键。

## 验收条件

- [x] `npm run build` + `tsc` 通过；chat 域不再有无依据的裸 workspace 键。
- [x] 工作区切换时 chat 侧不显示别的 workspace 的会话/消息。
- [x] P1-A（若已就绪）的「工作区缓存隔离」测试在此域打点通过（`workspace-cache-isolation.test.ts`、`client.test.ts`、`optimistic-mutation.test.tsx`）。