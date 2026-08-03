# LearnGraph 工程任务清单

> 本文档是把 [ROADMAP.md](../../ROADMAP.md) 的 P0/P1/P2 工程项拆成**可直接开工的任务**，
> 并按「能否与其他窗口并行修改」分成两个文件夹，供多窗口 / 多 Claude 会话并行协作。
>
> **协作边界（延续 ROADMAP 约定）：** 只安排本地开发、验证与提交；不 `git push`，不创建或提交 PR。

## 两个文件夹

| 文件夹 | 含义 | 多窗口可同时改吗 |
| --- | --- | --- |
| [01_可并行](01_可并行/) | 每个任务独享一组文件，互不重叠。可开多个窗口同步改。 | ✅ 可以，只要各窗口遵守「自己的文件自己改」 |
| [02_不可并行](02_不可并行/) | 若干任务修改**同一批文件**（尤其 `workspace-shell.tsx` / `chat-pages.tsx` / `graph-pages.tsx`），并发改会互相覆盖。 | ❌ 一次只开一个 |

## 决策逻辑（为什么这样分）

JS/TS 提交没有脏文件锁。`1_可并行` 里的任务各改各的文件，只是**文字上**可能同吊一个小工具
（`query-keys.ts` / `auth-query-cache.ts`），这不会互相覆盖，只是提交顺序要先后落盘（见 `01_可并行/README.md`）。

不可并行的原因集中在几个大文件：

- `frontend/src/features/chat/chat-pages.tsx` —— 会话、SSE 流、消息、乐观更新、会话删除全在这。
- `frontend/src/features/graph/graph-pages.tsx` —— 图谱、掌握度、对齐、目标绑定全在这。
- `frontend/src/components/layout/workspace-shell.tsx` —— 布局、侧栏、上下文轨全在这。

这几个文件只要有两个任务在改，就会冲突，所以一次只推进一个（P0-D → P0-B → P2-A-B）。

## 推荐的多窗口推进顺序

写进 `01_可并行/任务_00_总调度.md`，两个文件夹按下述顺序交错：

```
第一波（并行 4 窗口，全部都只碰自己的文件）：
  P1-A  前端测试基础设施        （window A）
  P1-B  后端持久化队列          （window B）
  P0-A  query-key 工厂          （window C）
  P2-C  受检沙箱出站策略        （window D）

P0-A 落盘后，切 P0-B（chat 页 query key 审计，单窗口）；
P0-B 落盘后，切 P0-C（graph 页 query key 审计，单窗口）；
P0-C 落盘后，切 P0-D（workspace-shell / 上下文轨 query key + 清理对齐，单窗口）；
P0-D 落盘后，切 P2-A / P2-B（与 P2-C 并行，单窗口逐项）。
```

> 判定规则：**依赖 P0-A 产出物（query-keys.ts）的任务**，必须等 P0-A 提交后再做；
> 只有 P1-A、P1-B、P2-C 三个任务（在可并行里它们被明确标注为「允许不依赖 P0-A」）可以先开。

## 约定

- 每个任务一个 Markdown 文件，结构见 `01_可并行/_模板/任务模板.md`。
- 任务文件头部固定有元信息块：`并行性`、`状态`、`主要文件`、`依赖`、`口音标注`。
- 真并行任务文件把「背景与目标」放在最前面并加 `>>` 引导，方便多窗口开工前一次性看清。
- 改动时遵守仓库记忆里的约定（见下），并在完成后向 `任务_00_总调度.md` 追加一行状态。

## 与仓库内存/文档的关联

- **多窗口并发**：见 memory `parallel-sessions-on-this-repo` —— 改文件前先重读、每次改动后重跑 `tsc`/import 自检、不要覆盖他人改动。
- **工作区默认响应模式竞态**：`workspace-shell.tsx` 里 `createConversation` 必须先 `fetchQuery` 再 seed —— 改设置的依赖任务（P0-D/chat 相关）要留意，别顺手破坏这条链路。
- 后端开发（P1-B）见 memory `backend-dev-workflow`：`uv run --with pytest` 跑测试、`backend/.venv` 跑脚本、SQLite 在 `backend/data/learngraph.db`。

## 任务清单（汇总）

### 可并行（`01_可并行/`）

| 任务 | 文件 | 依赖 | 状态 |
| --- | --- | --- | --- |
| [P1-A 前端行为测试基础设施](01_可并行/P1-A_前端行为测试基础设施.md) | `frontend/` 测试配置+工具+首批测试 | 无（可立即开） | 已完成 |
| [P1-B 后端持久化异步队列](01_可并行/P1-B_后端持久化异步队列.md) | `backend/` | 无（可立即开） | 已完成 |
| [P0-A query-key 工厂与身份/工作区键约定](01_可并行/P0-A_query-key工厂与键约定.md) | `frontend/src/lib/query-keys.ts`（+扩 `auth-query-cache.ts` 治理） | 无（可立即开） | 已完成 |
| [P2-C 受检沙箱出站网络策略](01_可并行/P2-C_受检沙箱出站网络策略.md) | `backend/`（sandbox/proxy/策略） | 无（可立即开） | 已完成（默认仍离线，受检出站经代理） |

### 不可并行（`02_不可并行/`）

| 任务 | 文件 | 依赖 | 状态 |
| --- | --- | --- | --- |
| [P0-B chat 域 query key 审计](02_不可并行/P0-B_chat域query-key审计.md) | `chat-pages.tsx` 等 chat 域 | 依赖 P0-A | 已完成 |
| [P0-C graph 域 query key 审计](02_不可并行/P0-C_graph域query-key审计.md) | `graph-pages.tsx`、`goal-*`、`learning-*`、`dashboard` | 依赖 P0-A | 已完成 |
| [P0-D workspace-shell 及上下文轨 + 清理对齐](02_不可并行/P0-D_workspace-shell与清理对齐.md) | `workspace-shell.tsx`、`auth-context.tsx`、`auth-query-cache.ts` | 依赖 P0-A + P0-B/C 落定 | 已完成 |
| [P2-A 第三方组件隔离 renderer 与签名信任链](02_不可并行/P2-A_第三方组件隔离Renderer与信任链.md) | `components.py` 等后端组件 | 可先开（设计），实现依赖 P1-B 队列 | 已完成 |
| [P2-B 隔离 MCP stdio runner 与 OAuth 生命周期](02_不可并行/P2-B_隔离MCP_stdioRunner与OAuth.md) | `mcp.py`、runner、OAuth | 可先开（设计），实现依赖 P1-B | 已完成 |