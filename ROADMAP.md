# LearnGraph 工程路线图

> 本文根据仓库当前代码状态整理工程优先级，而非产品发布日期承诺。只有被标记为“已完成”的事项可以被视为当前已有能力；P0/P1/P2 均为待实施计划。
>
> **协作边界：** 本路线图只安排本地开发、验证与提交；不执行 `git push`，不创建或提交 Pull Request。

## 使用方式与优先级定义

| 级别 | 定义 | 开发顺序 |
| --- | --- | --- |
| **P0** | 可能破坏工作区数据边界、缓存正确性或核心交互一致性的缺口 | 后续跨工作区功能之前优先完成 |
| **P1** | 用于降低回归率、提升中断恢复能力和维护性的基础设施 | 在 P0 完成后推进 |
| **P2** | 受安全边界约束的生态或部署扩展，不降低当前默认安全基线 | 设计、资源和前置条件就绪后推进 |

每项完成时应同时更新实现、针对性测试和本文状态；未实现的设计不得被写作当前可用能力。

## 已完成基线

以下为代码已有的事实，不属于待办：

- **R-017 划词解释本地隐私与回显。** 记录按 `(userId, workspaceId)` 分区，旧全局键会被丢弃；登出、401 和账户删除路径会清理相应记录；重复文本会利用保存的 `prefix` / `suffix` 锚定原始出现位置。参见 `frontend/src/features/chat/selection-explanation.ts`、`frontend/src/features/chat/chat-pages.tsx`。
- **沙箱默认拒绝网络。** 文件和 Agent 沙箱会话均使用 `network_policy={"mode": "none", "allowed_hosts": []}`；这是一项已完成的安全基线，不应被误报为“沙箱网络尚未实现”。参见 `backend/app/services/sandbox.py`。
- **MCP stdio 的宿主机执行被主动拒绝。** 当前适配器不会在 FastAPI 进程启动任意子命令，stdio 能力明确显示不可用。参见 `backend/app/providers/local/mcp.py`、`backend/app/services/mcp_skills.py`。
- **第三方可信组件安全降级。** 内置、固定声明式组件可走可信路径；第三方 Manifest 未配置隔离 renderer 时降级为 `sandbox_artifact`，而非注入主应用 DOM。签名状态当前仅能记录为 `unverified`，不是已建立信任链。参见 `backend/app/services/components.py`。
- **中断状态可识别和持久化。** 客户端断开后 SSE 生产可继续；服务启动会识别遗留流并保留具备检查点的中断信息，但尚未自动续跑。参见 `backend/app/api/routers/chat.py`、`backend/app/services/chat.py`。

## P0：工作区作用域 React Query 缓存隔离

### 目标

将所有工作区资源的 TanStack Query key 统一绑定 `workspaceId`，确保切换工作区后不会读取、乐观更新或失效另一工作区的缓存。

### 现状与风险

后端 API 请求已自动附带 `X-Workspace-ID`，服务端授权边界存在；但前端多处仍使用裸缓存键，例如 `['projects']`、`['sessions']`、`['settings']`、`['graphs']`、`['goals']`、`['mastery']`、`['graph', graphId]`。这会在同一浏览器标签切换工作区时造成短暂的陈旧数据展示，或让 mutation 失效/更新过宽。

主要入口：

- `frontend/src/components/layout/workspace-shell.tsx`
- `frontend/src/features/chat/chat-pages.tsx`
- `frontend/src/features/graph/graph-pages.tsx`
- `frontend/src/features/resources/`
- `frontend/src/api/client.ts`

### 实施范围

- [ ] 新建统一 query-key 工厂，例如 `workspaceQueryKeys.sessions(workspaceId)`、`workspaceQueryKeys.graph(workspaceId, graphId)`、`workspaceQueryKeys.settings(workspaceId)`。
- [ ] 审计并替换所有工作区资源的 `useQuery`、`fetchQuery`、`setQueryData`、`invalidateQueries` 和预取调用；mutation 必须使用同一工厂产出的键。
- [ ] 保留身份级、应用级或明确非工作区资源的键，并在代码附近说明其不需要 `workspaceId` 的依据。
- [ ] 将切换工作区、登出和 401 的缓存清理逻辑与新键约定对齐，避免旧工作区缓存短暂显示。

### 验收条件

- 在至少两个工作区之间切换时，不出现另一工作区的项目、会话、图谱、设置、掌握度或活动数据。
- 工作区 A 的 mutation 不会更新或失效工作区 B 的同类资源。
- 不再存在未说明原因的工作区资源裸 query key。
- 行为测试覆盖工作区切换与至少一个 mutation 缓存更新场景（依赖 P1-A）。

## P1：可验证前端行为与可恢复异步执行

### P1-A：前端行为测试基础设施

#### 现状

前端目前只有 `dev`、`build`、`lint`、`preview` 脚本，未配置前端测试命令、测试运行器或项目自有的 `*.test.*` / `*.spec.*` 测试文件。TypeScript 构建不能替代浏览器状态、React Query、SSE 或乐观更新的行为验证。

主要入口：`frontend/package.json`。

#### 实施范围

- [ ] 引入适配 Vite + React 的测试运行器和 DOM 环境，提供独立的 `test` 脚本。
- [ ] 建立共享测试工具：隔离 `QueryClient`、Router/workspace 参数注入、API mock、异步/SSE 状态控制。
- [ ] 首批覆盖：P0 工作区缓存隔离、R-017 重复文本锚定与异常回退、关键 loading/error/empty 状态，以及一个乐观更新失败回滚。
- [ ] 将前端测试纳入本地检查入口；构建、lint 和行为测试保持独立。

#### 验收条件

- 本地与 CI 可由单一前端测试命令运行测试，且不依赖真实 Provider 或既有浏览器存储。
- 至少覆盖工作区切换、划词解释异常处理和一个异步 mutation 回滚。
- 新增用户可见状态变更需有行为测试，或在变更说明中说明缺失原因。

### P1-B：持久化异步队列与中断续跑

#### 现状

当前 `InProcessTaskQueue` 使用进程内线程池，不能在多进程或重启后恢复执行。聊天流会持久化事件与部分 Provider continuation state，并可将带检查点的遗留任务标为 `interrupted`；当前恢复入口仍是重新生成/重试，并不等同于从中断点继续。

主要入口：

- `backend/app/core/tasks.py`
- `backend/app/services/chat.py`
- `backend/app/api/routers/chat.py`

#### 实施范围

- [ ] 定义可替换队列端口，拆分 job 持久化、入队、领取、心跳、取消、重试和幂等语义；保留进程内实现仅作开发/兼容路径。
- [ ] 为聊天、文档学习、研究和其他后台流程定义统一 job 状态机及恢复点。
- [ ] 持久化 continuation payload、所属工作区、尝试次数、lease/heartbeat 与可审计状态变更；敏感 Provider 状态沿用现有加密/脱敏边界。
- [ ] 在新 worker 或服务启动时重新调度有可恢复检查点的中断任务；不可恢复任务保留明确、可操作的 retry 路径。
- [ ] 编写故障注入测试：进程中断、服务重启、重复投递、lease 超时、取消与重试竞争。

#### 验收条件

- 进程在任务执行中终止后，具备检查点的任务可恢复，或明确进入可操作且不会丢失上下文的重试状态。
- 同一 job 被重复投递或重新领取时，不产生重复业务事实。
- 恢复、取消、失败、重试和完成均持久化并可审计。
- 一个工作区的积压/失败不会阻塞其他工作区的可执行任务。

## P2：受信扩展与可选网络能力

### P2-A：第三方组件隔离 renderer 与签名信任链

#### 目标

在不允许第三方代码进入主应用 DOM 的前提下，将当前的“Manifest 记录 + 安全降级”扩展为可验证、可撤销的第三方组件发布能力。

#### 实施范围

- [ ] 设计受信发行者、公钥/证书、密钥轮换、撤销、包哈希、签名覆盖范围和算法约束。
- [ ] 服务端验证发行者、签名和包哈希；信任状态不得由客户端或 Agent 自声明。
- [ ] 建设隔离 renderer：独立 origin 或强隔离 iframe、严格 CSP/权限策略、最小消息协议和最小数据暴露。
- [ ] 定义 trusted-bundle、sandbox 与降级规则；签名失效、发行者撤销或 renderer 健康检查失败时自动禁用或降级。
- [ ] 增加信任验证、撤销、降级、跨工作区和 renderer 消息边界的端到端测试。

#### 验收条件

- 只有登记发行者、有效签名、匹配包哈希和已授权工作区同时满足时，组件才能走可信 renderer。
- 第三方代码不能读取宿主 DOM、认证令牌、Provider 凭据或非授权工作区数据。
- 任何验证失败均不会放宽当前安全降级基线，并产生可审计原因。

### P2-B：隔离 MCP stdio runner 与 OAuth 生命周期

#### 目标

在继续禁止 FastAPI 主进程启动任意命令的前提下，让经审核的 MCP stdio Server 在独立受限环境中运行，并支持 OAuth 凭据生命周期。

#### 实施范围

- [ ] 保持 `UnavailableStdioMCPAdapter` 的默认拒绝语义，新增独立 runner 合约，禁止在 Web/API 进程调用 `subprocess`。
- [ ] 为 runner 实施最小镜像/命令白名单、非 root、资源配额、只读根文件系统、工作区临时目录、默认禁网和审计。
- [ ] 将 Server 注册与实际运行分离；注册记录经审核的启动规范、版本/哈希和权限 envelope。
- [ ] 实现 OAuth 授权码流程、动态客户端注册、加密保存、作用域限制、刷新、撤销和失效回收；令牌仅注入隔离 runner。
- [ ] 通过受控 IPC 接入现有 MCP provider port，施加超时、参数/结果大小限制、调用配额、脱敏和健康检查。

#### 验收条件

- 主 API 进程不启动第三方 MCP 命令。
- 未审核、哈希不匹配、无授权或已撤销的 MCP Server 无法执行。
- OAuth token 不出现在 API 响应、Agent 工具输入、前端状态或普通审计正文。
- runner 崩溃、超时、资源超限或令牌失效时返回结构化、可审计且不泄密的失败。
- 不同工作区的 MCP 凭据、能力快照和执行记录严格隔离。

### P2-C：沙箱 allow-host 出站策略（可选）

这不是当前安全基线缺口：默认完全离线已经实现。只有出现经过评估的产品需求时，才扩展 allow-host 能力。

#### 实施范围

- [ ] 保持默认 `network_mode="none"`；网络启用必须是显式工作区/任务级策略。
- [ ] 定义 allowlist 审核、主机规范化、DNS 重绑定防护、私网/环回/链路本地/云元数据地址拒绝、端口与协议限制、审计和过期机制。
- [ ] 使用可执行的出站代理或网络策略层，而不是只把 host 列表写入容器元数据。
- [ ] 覆盖 IPv4/IPv6、DNS 变化、重定向链和策略绕过的安全测试。

#### 验收条件

- 未显式授权的沙箱始终离线。
- allow-host 不能访问环回、私网、链路本地、云元数据地址或重定向绕过目标。
- 每次允许的访问均可关联工作区、任务和审批/策略记录；策略不确定时一律拒绝。

## 已知范围与非目标

- 根 `README.md` 的“后续规划”是产品方向，不应被误读为所有功能均未实现；本文件只记录可由代码验证的工程缺口和实施顺序。
- 远程模型、搜索、研究、ASR、Docker 沙箱等能力可能因凭据或部署环境而不可用；这属于条件性功能，不等于其实现缺失。
- 不将已完成的沙箱默认拒绝网络、MCP stdio 拒绝执行、第三方组件安全降级或 R-017 隔离列为待修复漏洞。
- 路线图不构成发布时间、供应商覆盖范围或生产可用性承诺。

## 变更记录

- **2026-08-02**：基于当前代码、`README.md` 与开发者文档建立首版 P0/P1/P2 路线图。
