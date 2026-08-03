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

- [x] 新建统一 query-key 工厂（`frontend/src/lib/query-keys.ts`：`workspaceQueryKey(workspaceId, ...parts)` → `["workspace", workspaceId, ...parts]`、`workspaceQueryPrefix`、`identityQueryKey`）。
- [x] 审计并替换所有工作区资源的 `useQuery`、`fetchQuery`、`setQueryData`、`invalidateQueries` 和预取调用；mutation 使用同一工厂产出的键（chat/graph/goal/learning/dashboard/memory/resources/shell 全部走 `workspaceQueryKey`，由 `tests/security/test_p0_client_isolation_source.py::test_workspace_scoped_query_keys_include_workspace_id` 源级固化）。
- [x] 保留身份级、应用级或明确非工作区资源的键，并在代码附近说明其不需要 `workspaceId` 的依据（`identityQueryKey` 仅用于不发 `X-Workspace-ID` 的 current-user/org 端点，`query-keys.ts` 内有取舍说明）。
- [x] 将切换工作区、登出和 401 的缓存清理逻辑与新键约定对齐，避免旧工作区缓存短暂显示（`auth-query-cache.ts::clearWorkspaceClientState` 按 `workspaceQueryPrefix` 清单租户、`clearAuthenticatedClientState` 全清；AuthContext 切换/登出/删号接线；行为测试 `test/workspace-cache-isolation.test.ts`）。

### 验收条件

- [x] 在至少两个工作区之间切换时，不出现另一工作区的项目、会话、图谱、设置、掌握度或活动数据（行为测试 `workspace-cache-isolation.test.ts` + `client.test.ts` 请求层断言）。
- [x] 工作区 A 的 mutation 不会更新或失效工作区 B 的同类资源（`optimistic-mutation.test.tsx` 验证 A 回滚不触碰 B）。
- [x] 不再存在未说明原因的工作区资源裸 query key（P0 源级测试覆盖全部已审计表面，不允许 `["sessions", workspaceId]` 复现）。
- [x] 行为测试覆盖工作区切换与至少一个 mutation 缓存更新场景（依赖 P1-A，已落地）。

## P1：可验证前端行为与可恢复异步执行

### P1-A：前端行为测试基础设施

#### 现状

前端目前只有 `dev`、`build`、`lint`、`preview` 脚本，未配置前端测试命令、测试运行器或项目自有的 `*.test.*` / `*.spec.*` 测试文件。TypeScript 构建不能替代浏览器状态、React Query、SSE 或乐观更新的行为验证。

主要入口：`frontend/package.json`。

#### 实施范围

- [x] 引入适配 Vite + React 的测试运行器和 DOM 环境，提供独立的 `test` 脚本（Vitest + jsdom + Testing Library）。
- [x] 建立共享测试工具：隔离 `QueryClient`、Router/workspace 参数注入、API mock、异步/SSE 状态控制（`frontend/src/test/`）。
- [x] 首批覆盖：P0 工作区缓存隔离、R-017 重复文本锚定与异常回退、关键 loading/error/empty 状态，以及一个乐观更新失败回滚（`client.test.ts`、`sse.test.ts`、`text-selection.test.ts`、`selection-explanation.test.ts`、`optimistic-mutation.test.tsx`）。
- [x] 将前端测试纳入本地检查入口；构建、lint 和行为测试保持独立（`scripts/check.mjs`）。

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

- [x] 定义可替换队列端口，拆分 job 持久化、入队、领取、心跳、取消、重试和幂等语义；保留进程内实现仅作开发/兼容路径（`app/services/durable_queue.py`：`DurableJob` 租约/CAS 状态机、文档 job 适配、启动 worker）。
- [x] 为聊天、文档学习、研究和其他后台流程定义统一 job 状态机及恢复点（文档 parse/index 与研究任务轮询已接入 `DurableJob`；研究轮询改为持久化 `research.poll` 单次刷新 + 活跃时 `rearm`；聊天续跑接入 `chat.continue_stream` durable job）。
- [x] 持久化 continuation payload、所属工作区、尝试次数、lease/heartbeat 与可审计状态变更；敏感 Provider 状态沿用现有加密/脱敏边界（`DurableJob` 持久化 payload/workspace/attempts/lease；`research.poll` 持久化 `research_job_id`/`workspace_id`/`actor_id`；`chat.continue_stream` 持久化 `message_id`/`message_version_id`/`provider_id`，续跑审计写入 `chat.continue_unavailable`）。
- [x] 在新 worker 或服务启动时重新调度有可恢复检查点的中断任务；不可恢复任务保留明确、可操作的 retry 路径（启动 `reconcile_research_polling()` 为在途研究任务补/re-arm `research.poll` 作业；启动 `enqueue_interrupted_chat_resumes()` 把带 `ProviderResponseState` 检查点的 `interrupted` 聊天流重调度为 `chat.continue_stream` job；durable worker 自动回收过期租约；聊天续跑按 `CHAT_CONTINUATION_CAPABLE_PROVIDERS` 能力注册表门控，当前无 provider 支持续跑时审计 `chat.continue_unavailable` 并保留消息 `interrupted` 状态与用户 retry 路径，绝不重跑已提交步骤）。
- [x] 编写故障注入测试：进程中断、服务重启、重复投递、lease 超时、取消与重试竞争（`tests/services/test_durable_queue_faults.py`：租约过期重领/陈旧 worker 被拒、`rearm` 保活、dedupe 幂等、按工作区取消隔离、跨工作区轮询公平、退避不阻塞、启动 reconcile 重建/re-arm、单次轮询终态判定）。

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

- [x] 设计受信发行者、公钥/证书、密钥轮换、撤销、包哈希、签名覆盖范围和算法约束（`app/services/component_trust.py`：ed25519、key_id、旋转、撤销）。
- [x] 服务端验证发行者、签名和包哈希；信任状态不得由客户端或 Agent 自声明（`verify_component_signature`，缺信任库/未注册/撤销一律非信任并保持降级）。
- [x] 建设隔离 renderer：服务端 inert 模板 + 严格 CSP，经既有 opaque-origin iframe 交付（`app/services/component_renderer.py`：`default-src 'none'`、`script-src 'none'`、`connect-src 'none'`、HTML 转义、无组件注入的 HTML/JS/CSS）。
- [x] 定义 trusted-bundle、sandbox 与降级规则；签名失效、发行者撤销或 renderer 健康检查失败时自动禁用或降级（`tests/services/test_component_trust.py`、`test_component_renderer.py`）。
- [x] 增加信任验证、撤销、降级、跨工作区和 renderer 消息边界的端到端测试（`tests/services/test_component_trusted_renderer.py`；真实 Docker 容器 render 经 `scripts/verify_sandbox_container_tasks.py` 在离线容器内验证，镜像已重建含 `render_component` 任务）。

#### 验收条件

- 只有登记发行者、有效签名、匹配包哈希和已授权工作区同时满足时，组件才能走可信 renderer。
- 第三方代码不能读取宿主 DOM、认证令牌、Provider 凭据或非授权工作区数据。
- 任何验证失败均不会放宽当前安全降级基线，并产生可审计原因。

### P2-B：隔离 MCP stdio runner 与 OAuth 生命周期

#### 目标

在继续禁止 FastAPI 主进程启动任意命令的前提下，让经审核的 MCP stdio Server 在独立受限环境中运行，并支持 OAuth 凭据生命周期。

#### 实施范围

- [x] 保持 `UnavailableStdioMCPAdapter` 的默认拒绝语义，新增独立 runner 合约（`app/providers/ports/mcp_runner.py`），禁止在 Web/API 进程调用 `subprocess`。
- [x] 为 runner 实施最小镜像/命令白名单、非 root、资源配额、只读根文件系统、工作区临时目录、默认禁网和审计（`app/providers/remote/mcp_stdio.py`：`egress=None` 强制 `network_mode="none"`、non-root、cap_drop ALL、参数上限；`mcp_stdio_runner_enabled` 默认关闭）。
- [x] 将 Server 注册与实际运行分离；注册记录经审核的启动规范、版本/哈希和权限 envelope（`register_stdio_launch_spec` / `approve_stdio_launch_spec`，注册与运行分离，未审批恒不可执行；`POST /mcp/servers/{id}/stdio-launch-spec[/approve]`）。
- [x] 实现 OAuth 授权码流程、动态客户端注册、加密保存、作用域限制、刷新、撤销和失效回收；令牌仅注入隔离 runner（`app/services/mcp_oauth.py`：PKCE S256 + state 常量时间比对、exchange/refresh/revoke、`register_oauth_client` 动态客户端注册、加密持久化、单飞锁刷新）。
- [x] 通过受控 IPC 接入现有 MCP provider port，施加超时、参数/结果大小限制、调用配额、脱敏和健康检查（`StdioIsolatedMCPAdapter.invoke` 接线：自建一次性隔离容器 one-shot JSON-RPC；超时→`MCPRunnerTimeout`/配额→`MCPRunnerResourceExceeded` 映射到 `ExtensionInvocation` 结构化审计；OAuth 活 token 经 `runner_only_token` 仅注入容器 workspace、绝不外泄；`MCPRunnerSession` 持久化 + `run_mcp_runner_cleanup_sweep` 清理 scheduler 回收孤儿容器）。

#### 验收条件

- 主 API 进程不启动第三方 MCP 命令。
- 未审核、哈希不匹配、无授权或已撤销的 MCP Server 无法执行。
- OAuth token 不出现在 API 响应、Agent 工具输入、前端状态或普通审计正文。
- runner 崩溃、超时、资源超限或令牌失效时返回结构化、可审计且不泄密的失败。
- 不同工作区的 MCP 凭据、能力快照和执行记录严格隔离。

### P2-C：沙箱 allow-host 出站策略（可选）

默认完全离线仍是安全基线；此项在通过评审后补齐了“受检出站”能力。启用必须是显式部署决策（`LEARNGRAPH_SANDBOX_EGRESS_ENABLED=true`）并配合已审批的按工作区策略；未授权/过期/损坏的策略一律保持离线。

#### 实施范围

- [x] 保持默认 `network_mode="none"`；网络启用必须是显式工作区/任务级策略（`SandboxCreateSpec.egress` 缺省强制离线）。
- [x] 定义 allowlist 审核、主机规范化（IDNA/尾点、拒 IP 字面量）、DNS 重绑定防护、私网/环回/链路本地/云元数据地址拒绝、端口与协议限制、审计和过期机制（`app/services/sandbox_network_policy.py`）。
- [x] 使用可执行的出站 CONNECT 代理（`app/services/sandbox_egress_proxy.py`），而不是只把 host 列表写入容器元数据。
- [x] 覆盖 IPv4/IPv6、DNS 变化/重绑定、重定向链和策略绕过的安全测试（`tests/security/test_sandbox_network_policy.py`、`test_sandbox_egress_proxy.py`）。

#### 验收条件

- 未显式授权的沙箱始终离线（默认路径测试保留 `network_mode="none"`）。
- allow-host 不能访问环回、私网、链路本地、云元数据地址或重定向绕过目标（连接时对解析结果重分类）。
- 每次允许的访问均可关联工作区、审批/策略记录；策略不确定时一律拒绝。

## 已知范围与非目标

- 根 `README.md` 的“后续规划”是产品方向，不应被误读为所有功能均未实现；本文件只记录可由代码验证的工程缺口和实施顺序。
- 远程模型、搜索、研究、ASR、Docker 沙箱等能力可能因凭据或部署环境而不可用；这属于条件性功能，不等于其实现缺失。
- 不将已完成的沙箱默认拒绝网络、MCP stdio 拒绝执行、第三方组件安全降级或 R-017 隔离列为待修复漏洞。
- 路线图不构成发布时间、供应商覆盖范围或生产可用性承诺。

## 变更记录

- **2026-08-03**：推进 P1-A 前端行为测试基建、P1-B 持久化租约队列第一切片、P2-C 受检出站策略实现（默认仍离线）、P2-A 签名信任库与 P2-B runner/OAuth 骨架。
- **2026-08-03**：P2-A 隔离 renderer runtime（服务端 inert 模板 + 严格 CSP + 离线沙箱固定任务校验）与 P2-B Docker stdio runner 容器后端（`mcp_stdio_runner_enabled` 默认关闭）落地，均保持默认降级/离线基线。
- **2026-08-03**：P2-B stdio 启动规范注册/审批流（注册与运行分离，未审批恒不可执行，`mcp_servers` 增量迁移列）落地。
- **2026-08-03**：P1-B 研究任务轮询迁至持久化队列（`research.poll` 单次刷新 + 活跃 `rearm`、启动 `reconcile_research_polling` 恢复在途任务、跨工作区公平）并落地故障注入测试。
- **2026-08-03**：可并行波次一完成——P1-A 前端补齐异步 mutation 回滚与 loading/error/empty 行为测试（26 通过）；P2-A 可信 renderer 通道（消费 `trusted_bundle_eligible`，新增 `ComponentCapabilityToken` + 版本化 postMessage 协议 + 短生命周期单 render token）；P2-B OAuth 授权码生命周期（PKCE/state/scope 绑定、加密持久化、refresh 单飞锁、撤销、动态客户端注册，并修复共享 SQLAlchemy Session 跨线程并发）。
- **2026-08-03**：重建沙箱镜像（含 `render_component`/`mcp_stdio` 任务）并经真实 Docker 离线容器端到端验证（`scripts/verify_sandbox_container_tasks.py`）；修复 runner `mcp_stdio` 输出被摘要覆盖的契约 bug；补 P2-C 部署冒烟脚本（`scripts/verify_sandbox_egress.py`）、P0 文档勾选与前端 R-017 设置页清除行为测试。
- **2026-08-03**：P1-B 聊天续跑接入 durable 队列——重启后带 `ProviderResponseState` 检查点的 `interrupted` 聊天流重调度为 `chat.continue_stream` job，按能力注册表门控续跑；当前无 provider 支持时审计 `chat.continue_unavailable` 并保留上下文与 retry 路径（`chat_durable.py`；后端全量测试 332 通过）。
- **2026-08-03**：P2-B runner 结构化审计与清理接线完成——`StdioIsolatedMCPAdapter.invoke` 自建隔离容器 one-shot JSON-RPC（修复无 session 接线），超时/配额映射到 `ExtensionInvocation` 审计；OAuth 活 token 经 `runner_only_token` 仅注入容器、revoke/过期 fail-closed；`MCPRunnerSession` 持久化 + `mcp_runner_cleanup_scheduler` 回收孤儿容器（后端全量测试 339 通过）。
- **2026-08-03**：P2-A 前端消费可信 renderer 通道——`TrustedComponentRenderer` 将 `sandbox_artifact` 第三方部件委托给 `SandboxArtifact`（替代 JSON dump），消费服务端 sealed envelope（可信徽标 / 降级原因 / `renderer.unlock` 握手，`targetOrigin='*'`），不读 iframe DOM、不放宽 CSP（前端 35 测试通过）。
- **2026-08-02**：基于当前代码、`README.md` 与开发者文档建立首版 P0/P1/P2 路线图。
