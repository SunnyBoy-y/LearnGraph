# 任务 P1-A｜前端行为测试基础设施

## 元信息块

```text
并行性   : 可并行（独享 frontend 测试配置、测试工具、首批测试文件；不碰 workspace-shell/chat/graph 生产代码）
状态     : 待开始 —— 可与 P0-A 并行
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

- [ ] 在 `frontend/` 加测试运行器 + DOM 环境（Vitest + jsdom 或等价），改 `package.json` 加 `test` 脚本。
- [ ] 建共享测试工具（隔离 QueryClient、workspace 参数注入、API mock、异步/SSE 状态控制）。
- [ ] 写首批测试（上列三类核心场景；其中「工作区缓存隔离」用例在 P0-A 打通键约定后可写死）。
- [ ] 把 `test` 纳入本地检查入口；确保与 build、lint 独立可运行。

## 与其他任务的边界（防冲突）

- **只加**测试配置、测试工具、`*.test.*` 文件。改动 `package.json` / 依赖属于本任务的特权；
  其它任务**不要**在同一 PR 里顺手加测试依赖，以免和我撞 `package.json`。
- **不碰** `workspace-shell.tsx`、`chat-pages.tsx`、`graph-pages.tsx` 等生产文件（那些属于 P0-B/C/D），
  哪怕为了修测试也不改它们——发现生产 bug 就记到对应任务，不在本任务里修。
- 与 P0-A 并行：等 P0-A 定稿后，本任务「工作区缓存隔离」用例可直接 import `workspaceQueryKey` 断言。

## 验收条件

- [ ] `npm run test` 在本地一键通过，且不依赖真实 Provider。CI 使用同一条命令。
- [ ] 覆盖：工作区切换、划词解释异常、一个异步 mutation 回滚。
- [ ] 无 flaky：SSE/异步一律用可注入的受控源，跑 2 次以上稳定。

## 产出物交付给谁

- 给所有人：从今往后「前端行为正确性」有一个可以回归的闸门。