# 任务 P1-A｜前端行为测试基础设施

## 元信息块

```text
并行性   : 可并行（独享 frontend 测试配置、测试工具、首批测试文件；不碰 workspace-shell/chat/graph 生产代码）
状态     : 已完成 —— 2026-08-03
主要文件 : frontend/package.json、测试运行器配置、frontend/src/test/ 或等价工具、首批 *.test.*
依赖     : 无（可立即开）
口音标注 : 无
```

## >> 背景与目标

**背景（为什么做这个）**

前端目前 `package.json` 只有 `dev` / `build` / `lint` / `preview`，没有**测试命令、运行器或任何
`*.test.*` 文件**。TypeScript 构建只能证明类型对，无法验证浏览器状态、React Query 缓存、SSE 流、
乐观更新回滚这些**行为**。而 P0 的核心风险（工作区切换可能秀出另一工作区的数据）恰恰是纯行为问题，
没有测试就约等于没人能证明它真的修好了。R-017 的重复文本锚定、关键 loading/error/empty 状态、
乐观更新失败回滚也需要一种「可复现、可回归」的方式去保护它们。

**目标（做成什么样子）**

- 引入适配 Vite + React 的测试运行器和 DOM 环境（Vitest + 适当的 Testing Library / jsdom，或项目当前
  Vite 版本兼容的其它栈），`package.json` 增加独立 `test` 脚本，`npm run test` 一条命令能跑全部。
- 建立**共享测试工具**：隔离 `QueryClient`、Router / workspace 参数注入、API mock、异步 / SSE 状态控制。
- 首批覆盖三类必测场景（ROADMAP P1-A 明确点名）：
  1. **工作区切换缓存隔离**（P0 的核心，等 P0-A 键约定落地后端到肉，见依赖）。
  2. **R-017 重复文本锚定与异常回退**（划词解释的 prefix/suffix 定位 + 异常时漂亮地回退）。
  3. **一个异步 mutation 失败回滚**（乐观更新失败要能回滚到原值）。

**完成标准 / 验收条件**

- `npm run test` 在本地（不依赖真实 Provider 或既有浏览器存储）可运行，且本地和 CI 用同一条命令。
- 至少覆盖：工作区切换、划词解释异常处理、一个异步 mutation 回滚。
- **新增用户可见状态变更**要有行为测试，或在变更说明里写明缺失原因（这是 ROADMAP 的硬约束）。
- 构建、lint、行为测试三条命令彼此独立（不互相依赖别名掩盖问题）。

## 现状与风险

- 无任何测试基建，是绿地。
- 风险：Vitest 版本与前端已有的 Vite / React / TanStack Query 版本要匹配，否则 jsdom 环境可能崩。
- 风险：SSE 用真实接线可能很脆，优先用可注入的假 SSE / 可控异步来控制测试，别把测试写成 flaky。

## 实施范围

- [x] 在 `frontend/` 加测试运行器 + DOM 环境（Vitest + jsdom + Testing Library），改 `package.json` 加 `test` / `test:watch` 脚本。
- [x] 建共享测试工具（隔离 QueryClient、workspace 参数注入、API mock、异步/SSE 状态控制：`frontend/src/test/render.tsx`、`api.ts`、`async.ts`、`setup.ts`）。
- [x] 写首批测试（上列三类核心场景；其中「工作区缓存隔离」用例在 P0-A 打通键约定后已写死）。
- [x] 把 `test` 纳入本地检查入口；确保与 build、lint 独立可运行（`scripts/check.mjs`）。

### 用户可见状态覆盖审计（ROADMAP P1-A 硬约束）

截至 2026-08-03，前端 26 个行为测试覆盖：

- 工作区切换缓存隔离：请求层（`client.test.ts`）+ 生命周期清理（`workspace-cache-isolation.test.ts`）+ mutation 回滚不触碰他租户（`optimistic-mutation.test.tsx`）。
- R-017：重复文本锚定/异常回退（`text-selection.test.ts`）、划词历史分区（`selection-explanation.test.ts`）、设置页清除按钮（`personalization-page.test.tsx`）。
- 异步/SSE：分帧去重/重连（`sse.test.ts`）、真实异步 mutation 回滚（`selection-explanation-panel.test.tsx`）。
- loading/error/empty：`dashboard-page.test.tsx`、`personalization-page.test.tsx`、`selection-explanation-panel.test.tsx`。
- 其余说明：真实 Docker renderer / MCP runner 的用户可见状态受部署环境门控，由 `backend/scripts/verify_sandbox_container_tasks.py` 覆盖；未另行补前端测试。

## 与其他任务的边界（防冲突）

- **只加**测试配置、测试工具、`*.test.*` 文件。改动 `package.json` / 依赖属于本任务的特权；
  其它任务**不要**在同一 PR 里顺手加测试依赖，以免和我撞 `package.json`。
- **不碰** `workspace-shell.tsx`、`chat-pages.tsx`、`graph-pages.tsx` 等生产文件（那些属于 P0-B/C/D），
  哪怕为了修测试也不改它们——发现生产 bug 就记到对应任务，不在本任务里修。
- 与 P0-A 并行：等 P0-A 定稿后，本任务「工作区缓存隔离」用例可直接 import `workspaceQueryKey` 断言。

## 验收条件

- [x] `npm run test` 在本地一键通过，且不依赖真实 Provider。CI 使用同一条命令（`scripts/check.mjs`）。
- [x] 覆盖：工作区切换、划词解释异常、一个异步 mutation 回滚。
- [x] 无 flaky：SSE/异步一律用可注入的受控源，跑 2 次以上稳定（26 用例连续多次全绿）。

## 产出物交付给谁

- 给所有人：从今往后「前端行为正确性」有一个可以回归的闸门。