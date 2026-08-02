# 任务 P0-C｜graph／learning／goal／dashboard 域 query-key 审计（不可并行）

## 元信息块

```text
并行性   : 不可并行（跨越 graph-pages.tsx 等多个大文件，与 P0-B 共用存量的会再次冲突，须排队）
状态     : 已完成（2026-08-02）
主要文件 : frontend/src/features/graph/graph-pages.tsx、frontend/src/features/learning/learning-pages.tsx、frontend/src/features/learning/roadmap-page.tsx、frontend/src/features/goals/goal-pages.tsx、frontend/src/features/goals/goal-chat-flow.tsx、frontend/src/features/dashboard/dashboard-page.tsx、frontend/src/features/memory/memory-page.tsx
依赖     : 必须先等 P0-A 落盘；建议在 P0-B 之后
口音标注 : 无
```

> 位置：放在 02_不可并行/。它改到 `graph-pages.tsx`，而该文件是 P0-B/chat、P0-D/workspace-shell 之外
> 最重要的构图文件；跨窗口并改会覆盖。所以与 P0-B、P0-D 排队成一串单窗口补丁。

## 背景

TODO —— 复制 ROADMAP P0「现状与风险」graph 相关要点 + 各文件现状。要点是本域裸键很多：
`["graph", graphId]`、`["graphs"]`、`["goals"]`、`["mastery"]`、`["mastery-schedules"]`、
`["evidence"]`、`["exercises", {...}]`、`["roadmap", goalId]`、`["dashboard", workspaceId]`、
`["actions"]`、memory 域 `["memory", ...]`/`["memory-profile"]`。

## 实施范围

- [ ] 列出各文件 query 键，分类：workspace 资源 → `workspaceQueryKey(...)`；身份级 → `identityQueryKey(...)`。
- [ ] 替换 `useQuery`/`fetchQuery`/`setQueryData`/`invalidateQueries`/预取；mutation 用同工厂键。
- [ ] 对明确非工作区的键注释依据（例如 `["current-user"]`、`["provider-models", id]`）。
- [ ] 回归：目标/图谱/掌握度/练习在不同 workspaces 间不互串；失效范围正确。

## 边界（防冲突）

- 只改 graph/learning/goal/dashboard/memory 域文件。**不碰** `chat-pages.tsx`（P0-B）、`workspace-shell.tsx`（P0-D）。
- 涉及清理治理的改动记给 P0-D。
- 等 P0-A 定稿再 import。

## 验收条件

- [ ] `npm run build` + `tsc` 通过；本域不再有无依据的裸 workspace 键。
- [ ] 图谱/掌握度/目标/练习切换 workspace 不互串。
- [ ] P1-A 若就绪，在 graph 域打点的隔离测试通过。