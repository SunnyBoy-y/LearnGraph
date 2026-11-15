# Skill、MCP 与定时任务执行链路验收

本轮交付是代码修复与可复现验证，不修改生产数据库、线上授权或现有第三方凭据；没有替用户创建业务定时任务。

## 结论与原始断点

原实现不能视为完整的真实执行链路：

- Agent Skill 包只能注入指令/读取文件，模型没有调用包内脚本的专用执行入口。
- 沙箱试运行只复制部分 UTF-8 文本，遗漏 assets、二进制、根目录 helper 与 package.json。
- 包根 cwd 与现有 root-only 沙箱策略冲突；真实容器验证发现并修复此问题，而没有放宽 Docker workdir 安全限制。
- stdio 的 probe 是空能力占位，原调用没有 initialize/initialized 会话；固定任务额外传递的 --spec 也不兼容 sandboxd 的语义接口。
- MCP resources/prompts 曾只有发现目录，没有完整的业务调用入口。
- Host Bridge token 可能被附加到普通公网 MCP 请求，endpoint 解析还会改写 ORM 中的逻辑地址。
- 没有独立、持久化的扩展定时计划和管理页面。

这些断点已修复，关键路径由真实 Docker + sandboxd 测试覆盖，不依赖回显工具伪装执行。

## Skill 真实代码执行

入口：模型 `lg_skill_run_script`；扩展中心“运行包内脚本”；REST `POST /api/v1/skills/{id}/sandbox-run`。

1. 模型按原有渐进披露流程发现/读取 SKILL.md，然后用 skill_key、scripts/ 下的脚本路径、argv 数组调用执行工具。
2. 模型入口绑定当前会话，不接收模型传入的 chat_session_id；执行时检查 workspace.manage、沙箱授权和当前内容哈希对应的持久授权。
3. 包文件按原始 bytes 读取并验证 blob SHA256、文件大小和整包树哈希。包含二进制资源、references、assets、helper、package.json，不重写技能代码。
4. 每次调用有独立的包目录，防止旧版本残留和共用缓存被脚本污染。
5. 支持 Python `.py`、Node `.js/.mjs/.cjs`。参数为 argv，不自动执行宿主 shell；未知脚本类型明确拒绝。
6. 默认 cwd_mode=package。服务端固定启动器在隔离进程内切换到包根，Docker workdir 仍为会话工作区根，保留 root-only 防护。相对 helper/资源可正常使用。
7. cwd_mode=workspace 适用于直接操作会话相对文件的技能。包根模式提供 LEARNGRAPH_SKILL_ROOT、LEARNGRAPH_WORKSPACE_ROOT、LEARNGRAPH_OUTPUT_DIR。
8. 实际执行复用持久化 sandbox job，返回 invocation_id、job_id、command_id、包根、stdout/stderr 和真实状态。运行中不伪装失败/成功，可查询和请求取消。

状态与取消：
- `GET /api/v1/skills/{skill_id}/sandbox-runs/{invocation_id}`
- `POST /api/v1/skills/{skill_id}/sandbox-runs/{invocation_id}/cancel`
- 模型也可用已有 sandbox_get_job / sandbox_cancel_job。取消请求与最终终止是不同状态。

手动试运行是管理员明确触发的沙箱动作，可以用于授权前审阅；模型调用和定时执行仍要求当前版本的持久授权。没有沙箱时明确返回 unavailable，不降级到宿主执行。

## MCP

### 协议与业务能力

- 支持现代 `2026-07-28` 的 server/discover、逐请求 _meta、HTTP 路由头，以及旧版 `2025-11-25`、`2025-06-18`、`2025-03-26` 的 initialize/initialized 流程。
- 现代探测失败只在识别为旧端点时回退。认证失败或可识别的现代协议错误不会触发降级，更不会重放业务工具调用。
- HTTP/stdio 均执行真实工具发现/调用、resources/read、prompts/get；资源模板进入快照并按审核过的 RFC6570 模板扩展。
- 支持仅含 resources 或 prompts 的服务，不再强制至少一个 tool。
- tools/resources/prompts 有界分页，拒绝非法/重复 cursor、超大响应、错误响应 ID、result/error 同时存在等错误。
- SSE 收到对应最终响应就返回，不等待长连接 EOF；操作有总截止时间，旧 HTTP 会话结束时做 best-effort DELETE。
- x-mcp-header 有安全编码、嵌套属性提取和无效工具剔除。声明 outputSchema 的工具校验 structuredContent。
- 对不曾声明的采样、elicitation、roots 等客户端交互明确拒绝；不偷偷授予客户端能力、不自动接受用户交互、不自动重试有副作用的操作。

### 隔离与授权

- stdio 在审批过的不可变镜像中执行，启动的实际 digest 必须匹配审批 digest。探测和调用都初始化真实服务，并始终清理独立容器。
- 单输入的私有 launch/request envelope 同时兼容 Docker 和 sandboxd 固定任务接口。
- 更新启动规范撤销原授权；审批可绑定 expected_launch_spec_hash，防止审阅后启动定义被替换。
- HTTP 连接时重新验证解析结果，并连接到已验证数字 IP，保留原 HTTP origin/TLS SNI，封堵 DNS 检查与连接之间的重绑定窗口。
- Bridge token 仅发送给实际桥接的一跳，不发送给普通远程端点；解析不再污染 server.endpoint_url。
- OAuth 过期/撤销的凭据在 HTTP/stdio 均失败关闭；服务输出中回显的已知凭据也会被脱敏。
- 一次性授权通过数据库条件更新消费；跨进程执行槽限制 MCP 并发，覆盖 API、模型与定时任务调用。
- 资源/提示入口同样复用当前快照、授权、输入/输出预算和审计，不能用任意 URI 绕过审核范围。

扩展中心新增资源/提示调用、审核范围编辑、stdio 传输注册与隔离启动审批。扩展查询缓存按工作区隔离。

可选旧 Host Companion 的 `/rpc` 是 `{launch_id,message}` 兼容封装，而非标准 Streamable HTTP 端点；正常 MCP 使用上述 HTTP/隔离 stdio 适配器。旧 Companion 已补上显式 enabled、启动锁、内部唯一请求 ID、通知、stderr 分离、环境隔离、注册表变更停机及进程收尾，不作为默认宿主执行回退。

## 定时任务

导航：工作区“定时任务”，路径 `/w/{workspaceId}/schedules`。模型入口：`lg_scheduled_tasks`。

- 持久化五字段 cron 与 IANA 时区，服务端计算 next_run_at。
- 支持 Skill 脚本、声明式 Skill、已审核 MCP 工具。
- 页面支持创建、编辑、启用/暂停、单次手动执行、脚本停止请求、执行记录与结果会话链接。
- 运行中可以暂停后续周期，但不能修改执行定义/删除活跃任务；暂停不是伪装取消。
- 工作区隔离与 workspace.manage；每次执行重新验证创建者仍有效、当前成员权限、扩展当前版本授权和会话 ACL；维护迁移期间不执行。
- 数据库租约、revision CAS、计划槽唯一键和独立 dispatch CAS，避免多进程、重复回调或同一周期重复执行。
- 长脚本接入 sandbox job 并回收真实终态；超出租约先请求取消，观察到终止后才释放，避免新旧执行重叠。
- 停机错过的计划合并为一次；中断或失败不会自动重放潜在外部副作用。应用服务需运行，非操作系统级 cron。

API：
- `GET/POST /api/v1/scheduled-tasks`
- `GET/PATCH/DELETE /api/v1/scheduled-tasks/{id}`
- `POST /api/v1/scheduled-tasks/{id}/run`
- `POST /api/v1/scheduled-tasks/{id}/cancel`
- `GET /api/v1/scheduled-tasks/{id}/runs`

数据库新增 scheduled_extension_tasks 与 scheduled_extension_runs，由现有数据库初始化/元数据建表流程接入。

## 部署

1. 同步后端依赖与 lock，重新构建前端，重启 API。依赖新增 croniter、uri-template，并显式约束 httpcore。
2. 更新生产沙箱镜像：`backend/sandbox/Dockerfile` 会复制 runner.py 与 mcp_session.py；审批 stdio 服务时使用新镜像的不可变 digest。不要把本文测试镜像替代生产沙箱镜像。
3. 保留沙箱、镜像审批、扩展授权和默认隔离策略。第三方 Python/Node 依赖应预装在审核过的镜像中；离线限制不是通过偷偷联网 pip/npm 安装绕过的。
4. `LEARNGRAPH_SCHEDULED_TASKS_ENABLED=true`（默认）；`LEARNGRAPH_SCHEDULED_TASKS_POLL_SECONDS=15`（默认，1–300 秒）。stdio 仍由原 `LEARNGRAPH_MCP_STDIO_RUNNER_ENABLED` 显式启用。
5. 不需要创建系统 CronTaskManager 条目；这是 LearnGraph 应用内持久调度，不是修改当前开发机器的个人任务。

## 验证

最终验证结果：

| 检查 | 结果 |
| --- | --- |
| 后端相关专项与安全回归 | 189 passed |
| 前端任务/扩展页面与 API 回归 | 24 passed |
| 真实 Docker + sandboxd（池化、非池化） | 2 passed |
| 前端 TypeScript 类型检查 | 通过 |
| 前端生产构建 | 通过 |

后端剩余提示为既有依赖的弃用警告；未出现失败、跳过核心验证或未回收测试容器/卷。容器测试使用的本地测试镜像与生产镜像隔离。

专项测试覆盖旧用例和新增回归，包含授权边界、工作区隔离、原字节物化、资源模板、现代/旧协议、SSE、DNS 重绑定、凭据回显脱敏、真实任务 CRUD/执行历史、重复 dispatch 与暂停正在运行的计划。

真实容器测试使用随机 deployment、独立临时数据库/daemon 状态与受控容器/卷；不读取生产数据库或第三方凭据。两种模式（池化 / 非池化）验证：
- 模型运行入口执行原包 Python 与 Node 代码；
- 二进制 assets、相对 helper 和脚本输出文件；
- 持久化定时任务到真实沙箱 job，再归并到 succeeded 终态；
- 固定 MCP task 的真实旧协议握手、能力发现与工具调用。

复现（仓库根目录，POSIX/Git Bash）：

```sh
uv run --project backend --extra test pytest backend/tests/unit/test_extension_runtime_repairs.py backend/tests/unit/test_mcp_modern_protocol.py backend/tests/unit/test_mcp_content.py backend/tests/unit/test_scheduled_tasks.py -q
npm --prefix frontend test -- src/api/schedules.test.ts src/features/schedules/schedules-page.test.tsx src/features/settings/mcp-advanced-controls.test.tsx src/features/settings/skill-script-controls.test.tsx
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

隔离容器复现，BASE 指向本地已有的后端镜像（测试镜像只补 Node 与固定 runner）：

```sh
docker build --pull=false -f backend/sandbox/Dockerfile.extension-test --build-arg BASE=<existing-backend-image> -t learngraph-extension-test:local backend/sandbox
LEARNGRAPH_TEST_EXTENSIONS_DOCKER=1 uv run --project backend --extra test --with docker pytest backend/tests/integration/test_extension_execution_docker.py -q -s
```

测试不声称已验证所有第三方服务的私有依赖、账号或收费行为；这些仍需相应服务配置与有效授权。未宣称支持未广告的 MCP 客户端扩展，也未把默认拒绝/离线隔离移除当作“满血”。
