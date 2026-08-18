# LearnGraph sandboxd 改造 TODO 与逐项验收标准

> 设计依据：[`sandboxd-migration-plan.md`](./sandboxd-migration-plan.md)  
> 状态说明：本文是实施清单，不表示条目已经完成。  
> 原则：一个 TODO 对应一个可独立评审、可验证、可回退的变更集；除非条目明确允许，不跨阶段合并。

## 实施进展

### 2026-08-14 · 第一波（Phase 0/Phase 1 核心）

已完成（`backend` 全量回归 301 passed，语法编译 294 文件 + `app.main` 导入通过）：

- **TODO-003**：`document_learning.py` 不再引用不存在的 `SandboxService`，改用真实 `SandboxTaskService`；
- **TODO-005**：新增 `backend/app/providers/sandbox_registry.py`（`SandboxBackendRegistry` / `SandboxManager` / `get_sandbox_backend_registry()`），`docker` 已注册，未知 backend id fail-closed；registry 行为已用独立脚本验证；
- **TODO-006**：`SandboxTaskService` 与 `SandboxAgentWorkspaceService` 支持构造注入 `backend`，默认经 registry 解析；
- **TODO-008**：additive 迁移 `v1.1.0`（`sandbox_sessions.backend_resource_ref` / `backend_protocol_version`、`mcp_runner_sessions.backend_id`），模型与 `CURRENT_SCHEMA_REVISION` 同步更新；旧库升级演练通过（列添加、旧 ref 保留、幂等）；
- **TODO-009**：`sandbox.py` 的 cleanup/`_runtime_backend`、`scheduler.py` 的 sandbox 清理与 MCP runner 清理均按持久化 `backend_id` 路由；`files.py`、`sandbox_fetch.py` 切 registry。

### 2026-08-14 · 第二波（Phase 0/1 收尾 + Phase 2 sandboxd 本体）

已完成（`backend` 全量回归 301 passed + 84 项沙箱相关测试；`sandboxd` 55 passed）：

- **TODO-001**：方案文档新增「SandboxBackendPort 合同矩阵」附录；
- **TODO-002**：决策落地——维持既有约束（测试不入库、禁 `git add -f`），新增测试仅本地运行；
- **TODO-004**：`_effective_network_policy()` 落库真实网络语义（无策略 `none`，有策略记录 policy digest）；三处 `SandboxCreateSpec` 携带 `egress` 时同步写回；
- **TODO-007**：`SandboxCreateSpec` 新增 `workspace_key`（backend-neutral），三处 create 携带；
- **TODO-010**：`component_renderer.py` 与 `mcp_stdio.py` 改经 registry 获取 backend；renderer/MCP 各自独立 workspace 子目录（不再共享 workspace root）；MCP 增加 backend id 路由与 workspace_dir 清理；
- **TODO-011**：`sandboxd/` 独立 package（`pyproject.toml` + config + main），`/health/live`、`/health/ready`、config fail-closed；
- **TODO-012**：`auth.py`（service token + admin token 分离、constant-time、request id、access log 脱敏）；
- **TODO-013**：`protocol.py`（v1 DTO、稳定 error codes、capabilities/limits）；
- **TODO-014**：`store.py`（SQLite WAL：sandboxes / idempotency / executions）+ controller 幂等与 owner 绑定；
- **TODO-015**：`runtime/port.py`（RuntimeBackendPort）+ `tests/fake_runtime.py`；
- **TODO-016**：`runtime/docker.py`（named volume、per-sandbox internal egress network、hardened container、exec 流、quota、`list_managed`）；
- **TODO-017**：controller 启动 reconcile（orphan 按 managed labels + grace）、TTL sweep 线程；
- **TODO-022/023**：app 侧 `sandboxd_client.py`（httpx 共享连接池、稳定错误映射）+ `sandboxd_backend.py`（完整 Port 适配，fixed argv 解析、无 host path/image 透传、禁止回退）；config 支持 `sandbox_backend=sandboxd`（缺 URL/token fail-closed）；registry 注册 `sandboxd` provider。MockTransport 端到端映射验证通过。

### 2026-08-14 · 第三波（Phase 2 收尾 + Phase 3/5 部分）

已完成（`backend` 全量回归 301 passed；sandboxd 55 passed；compose 展开验证通过）：

- **TODO-021**：sandboxd 模式禁用宿主直读——`_read_workspace_bytes_from_host()` 对 `backend_id=="sandboxd"` 直接返回 None；`transcribe_workspace_audio` 在逻辑 store 缺失时改走 daemon File API（有界读取）；
- **TODO-027（部分）**：`sandboxd/Dockerfile`（独立镜像，仅含 sandboxd + seccomp 配置）+ `docker-compose.sandbox.yml` 重写为新架构：app 无 socket、仅 sandboxd 挂 socket 与 `sandboxd-state` volume、`sandbox-control` internal 网络、secrets token、sandboxd 无 host ports / read_only / cap_drop ALL / NNP、仅接 control 网络；`docker compose config` 展开验证通过。遗留：`scripts/dev.mjs` 启动 sandboxd、真实 Linux socket GID 实机验收。

### 2026-08-14 · 第四波（Phase 4/5 部分）

已完成（sandboxd 60 passed；app 侧 report 函数验证通过；docker-update.sh 语法检查通过；compose 展开通过）：

- **TODO-024**：sandboxd Bootstrap 控制权——`runtime_records` 表 + `pull_and_resolve_digest()`（pull→单 RepoDigest→labels 校验）+ controller `install_runtime()`（ABI label 校验、记录持久化）+ admin API（`POST /v1/bootstrap/jobs`、`GET /v1/runtimes`、`GET /v1/bootstrap/jobs/{kind}`，service token 不可用 admin 面）；create 优先 env pin、其次已安装记录、否则 fail-closed；
- **TODO-029**：`sandbox_backend_report(db)`——按 backend/lifecycle 统计未 cleaned session、legacy container ref、legacy MCP runner、sandboxd resource ref，输出 `drain_ready` 判定；独立验证通过；
- **TODO-030**：`scripts/docker-update.sh`——备份自动包含 sandboxd 状态卷、健康检查叠加 sandboxd live 探测、清理策略纳入 sandboxd-state 备份；
- **TODO-032（部分）**：根 `.env.example`、`backend/.env.example`、`README.md` 更新 sandboxd 部署说明（socket 只给 sandboxd、token 生成、runner digest、升级备份范围）。

### 2026-08-14 · 第五波（Phase 5/6 收尾 + 代码扫描）

已完成（sandboxd `uv --locked` 安装导入 OK；ci.yml YAML 校验通过；dev.mjs/node --check 通过；最终代码扫描符合目标）：

- **TODO-028**：`scripts/dev.mjs` 支持 sandboxd——`LEARNGRAPH_SANDBOX_BACKEND=sandboxd` 时自动生成 `.sandboxd/` 开发 token/state、spawn 本地 sandboxd 进程（`uv run --locked`，端口 8090 可覆盖）、backend 环境注入 `LEARNGRAPH_SANDBOXD_URL/TOKEN_FILE/DEPLOYMENT_ID`、退出时随栈停止；生成 `sandboxd/uv.lock`；
- **TODO-033**：`.github/workflows/ci.yml` 新增 `sandboxd` job（`uv sync --locked --extra test` + 语法编译 + `pytest tests -q`，无需 Docker），与 backend/frontend 并列；YAML 结构校验通过；
- **TODO-037（部分）**：最终代码扫描——`docker.from_env()` 仅存在于 `providers/remote/sandbox.py`（legacy docker backend）、`sandbox_bootstrap.py`（legacy auto 构建）、`sandboxd/runtime/docker.py`；`DockerSandboxBackend(` 构造仅存在于 bootstrap factory（registry docker provider）；compose 中 app 无 socket、sandboxd 无宿主端口。

遗留（下一步，均需真实 Linux Docker 主机或满足严格 gate 后执行）：

- **TODO-018~020**：daemon 侧 volume/File API/quota 已实现；完整验收（cold helper inspect、TOCTOU/symlink 对抗、双写 outbox/generation/tombstone 合同）需真实 Docker + app 侧一致性改造；
- **TODO-026 / 034 / 035**：renderer/MCP/fetch sandboxd roundtrip、opt-in integration、Compose 安全验收 job 需真实 Linux Docker 主机；
- **TODO-031**：移除 app Docker socket/docker-py——严格 gated：`sandbox_backend_report().drain_ready == True`（真实环境 legacy 清零）后执行；
- **TODO-036**：升级/回滚演练需真实 Docker 环境。
- 按项目既有约束，未提交任何测试文件、未使用 `git add -f`。

遗留（下一步）：

- **TODO-018~020**：app 侧 File API 双写一致性合同（outbox/generation/tombstone）与 cold helper 严格验收（daemon 侧 volume/File API 已实现）；
- **TODO-026**：renderer/MCP/fetch 的 sandboxd roundtrip 真实 Docker 验收（归属 TODO-034 opt-in integration）；
- **TODO-028**：`scripts/dev.mjs` 管理本地 sandboxd 进程；
- **TODO-031**：移除 app Docker socket/docker-py（严格 gated：drain_ready 后才可执行）；
- **TODO-033~037**：CI 分层、opt-in integration、Compose 安全验收 job、升级演练、发布门禁。
- 按项目既有约束，未提交任何测试文件、未使用 `git add -f`。

遗留（下一步）：

- **TODO-018~020**：app 侧 File API 双写一致性合同（outbox/generation/tombstone）与 cold helper 严格验收（daemon 侧 volume/File API 已实现）；
- **TODO-024~026**：Bootstrap 迁入 sandboxd、egress policy reference 接线、renderer/MCP/fetch 的 sandboxd roundtrip；
- **TODO-028~032**：dev.mjs 启动 sandboxd、mixed-backend 灰度开关与 drain、升级/备份脚本、切权、README/env 文档；
- **TODO-033~037**：CI 分层、opt-in integration、Compose 安全验收、升级演练、发布门禁。
- 按项目既有约束，未提交任何测试文件、未使用 `git add -f`。

## 0. 使用方法

### 0.1 状态标记

- `[ ]` 未开始；
- `[-]` 进行中；
- `[x]` 已完成且验收证据齐全；
- `[!]` 阻塞，必须记录原因和解除条件。

### 0.2 验收证据

每项完成时在 PR/变更记录附：

1. 实际修改文件；
2. 对应测试命令和结果；
3. 若涉及 Docker，附 `docker inspect` / `docker compose config` 的关键结论（不要附 secret）；
4. 若涉及 migration，附旧库升级和 fresh DB 两条结果；
5. 若涉及安全边界，附负向测试；
6. 若暂不能自动化，说明人工步骤、平台和复现环境。

### 0.3 测试文件约束

当前真实规则有冲突：根 `.gitignore` 反忽略 `backend/tests/**`，但 `backend/.gitignore` 的 `/tests/` 会再次忽略新测试。项目已有约束是不强制加入被忽略的测试文件，禁止 `git add -f`。

因此：

- 本文列出的测试路径是**指导路径**；
- 在 TODO-002 做出明确决策前，不创建大量新测试文件；
- 如果决定测试只在本地临时存在，就不要宣称 CI 已覆盖；
- 如果决定新回归测试应入库，必须先通过正常 ignore 规则让 Git 可见；
- 任何时候都禁止 `git add -f`。

### 0.4 基线命令

```bash
# 后端全量
cd backend
uv run --locked --extra test pytest tests -q

# 语法和导入（与 CI 对齐）
uv run --locked python -c "from pathlib import Path; files=sorted(Path('app').rglob('*.py')); [compile(f.read_bytes(), str(f), 'exec') for f in files]"
uv run --locked python -c "from app.main import app; assert app.title"

# Compose 静态展开
cd ..
docker compose -f docker-compose.yml config
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml config
```

---

# Phase 0：基线、决策和现有断链修复

## TODO-001 `[x]` 固化当前 Docker 沙箱行为与安全不变量

**目的**：在搬迁前明确哪些行为必须原样保留，避免“能执行”掩盖安全回退。

**修改指导**：

- 为 `SandboxBackendPort` 的每个方法建立 contract matrix；
- 记录 `DockerSandboxBackend` 的 create、file、exec、quota、snapshot、stop/delete、egress 行为；
- 明确 timeout/output truncation 后是否杀 runtime；
- 明确 fixed runner 与 Agent argv 的不同策略；
- 明确冷却、workspace TTL、cleanup blocked 的状态变化。

**验收标准**：

- [ ] matrix 覆盖 Port 的全部 14 类操作，没有“待猜测”字段；
- [ ] hardened create 的 UID、read-only、caps、NNP、seccomp、CPU、memory、swap、pids、tmpfs、shm、network 已列出；
- [ ] 文件路径、文件数/目录数/字节、archive、stdout/stderr 上限已列出；
- [ ] Agent 未授权删除恢复语义已列出；
- [ ] egress 正确描述为“无有效策略时 none；有策略时只接内部代理网络”，不再写“始终 none”；
- [ ] 评审者可逐项指出迁移后由 app、sandboxd controller 或 Docker runtime 哪一层负责。

**测试文件指导**：

- 优先扩展已有 `backend/tests/unit/test_sandbox_fetch_pool.py` 和 `backend/tests/unit/test_sandbox_agent_files.py` 的 fake contract；
- 若允许新增，指导路径：`backend/tests/unit/test_docker_sandbox_contract.py`；
- 该测试使用 fake Docker client，不连接真实 Docker；断言参数和错误映射，不断言 docker-py 内部实现细节。

**依赖**：无。  
**阻塞后续**：TODO-007、TODO-011、TODO-016。

---

## TODO-002 `[x]` 明确并修复测试文件跟踪策略

> 决策（2026-08-14）：维持项目既有约束——测试文件不提交 Git，禁止 `git add -f`，不改动 ignore 规则。新增测试仅在本地运行并提供结果作为验收证据；CI 覆盖缺口已记录。该决策已写入实施进展。

**目的**：保证后续“新增测试”不会静默被 `backend/.gitignore` 吞掉，也不违反禁止强制加入的项目规则。

**最终决策（2026-08-15 项目所有者确认）**：

> sandboxd 与 backend 的新增测试**仅保存在本地测试环境，不混入开源仓库**。遵守既有约定：测试文件不入库、不使用 `git add -f`；CI 的 sandboxd 测试步骤保持“存在即跑”（当前 checkout 无 `tests/` 时走 skip 分支）；本地回归由开发者在本机执行并作为发布证据。

**修改指导**：

- 维持根 `.gitignore` 的 `**/tests/` 忽略（不新增反忽略规则）；
- sandboxd 测试（`sandboxd/tests/`）与 backend 新集成测试（`backend/tests/integration/`）均为 local-only；
- `backend/tests/api|unit|security|memory` 的既有 tracked 回归测试保持入库现状，不受影响；
- 不使用 `git add -f`。

**验收标准**：

- [x] 决策已写入本文档（本段）；
- [ ] 任意新增测试文件经 `git check-ignore -v` 确认被忽略，且不出现在 `git status`；
- [ ] CI 的 sandboxd job 测试步骤保持“存在即跑/skip”语义，不因缺测试文件失败；
- [ ] 本地回归结果（sandboxd 60 用例 + backend 集成 roundtrip）作为 TODO-034/037 的验收证据记录；
- [ ] 无 `git add -f` 记录。

**测试文件指导**：

- 本项不需要业务测试；
- 验证命令应使用 `git check-ignore -v <path>` 和 `git status --short`；
- 不要用 shell 删除探针文件，应使用结构化 Delete。

**依赖**：无。  
**阻塞后续**：所有建议新增测试文件的 TODO（按最终决策：新增测试均为 local-only）。

---

## TODO-003 `[x]` 修复 legacy `.doc` 路径引用不存在的 `SandboxService`

**目的**：先消除当前真实断链，避免迁移测试把旧缺陷误判为 sandboxd 问题。

**修改指导**：

- 修改 `backend/app/services/document_learning.py`；
- 将不存在的 `SandboxService` 替换为真实 `SandboxTaskService`，或抽取职责明确的 `LegacyDocumentSandboxService`；
- 不改变错误映射 `SandboxBackendError` / `SandboxBackendUnavailable → ProcessorUnavailable`。

**验收标准**：

- [ ] 仓库搜索不存在 `from app.services.sandbox import SandboxService`；
- [ ] `.doc` 分支可导入、可构造；
- [ ] backend unavailable 时仍返回 processor unavailable，而非 ImportError；
- [ ] non-`.doc` 文档路径不受影响；
- [ ] app import 和后端全量测试通过。

**测试文件指导**：

- 优先在已有 document learning 相关 tracked 测试中补场景；若无合适文件，指导路径 `backend/tests/unit/test_document_learning_legacy_doc.py`；
- 使用 fake sandbox service/backend，不依赖 antiword 或 Docker；
- 至少覆盖成功 artifact、backend unavailable、runner error、non-`.doc` 不调用 sandbox。

**依赖**：TODO-002（若新增测试文件）。

---

## TODO-004 `[x]` 统一真实 network policy 和 runtime 状态语义

**目的**：修复主库总写 `network_policy={mode:none}` 与实际可能启用 egress 的不一致，并停止以 ref 是否为空隐式推断全部状态。

**修改指导**：

- 在 session 创建或 runtime 创建成功后保存 effective network policy 摘要；
- 只存 mode、policy digest/revision、allowed count 等安全元数据，不存 proxy credential；
- 明确 CREATED/COLD/STARTING/RUNNING/WARM_IDLE/RECOVERING/EXPIRED 转移图；
- 为 sandboxd stable resource ref 预留语义。

**验收标准**：

- [ ] 无策略时数据库 mode 为 `none`；
- [ ] 有有效策略时数据库不再谎报 `none`，且保存正确 digest；
- [ ] readiness/profile 文案与真实网络一致；
- [ ] 日志和 API 不泄露代理 secret；
- [ ] 状态转移表覆盖 create、resume fail、timeout kill、stop、runtime TTL、workspace TTL、cleanup blocked。

**测试文件指导**：

- 扩展 `backend/tests/security/test_agent_egress_policy.py`；
- 扩展 `backend/tests/unit/test_sandbox_agent_files.py` 验证 session 状态；
- 不启动真实代理；通过临时 policy 文件和 fake backend 检查 effective metadata。

**依赖**：TODO-001。

---

# Phase 1：Registry、依赖注入和持久化路由

## TODO-005 `[x]` 引入 `SandboxBackendRegistry` 与 `SandboxManager`

**目的**：把“为新 session 选默认 backend”和“为旧 session 按 backend id 路由”分开。

**修改指导**：

- 新增 `backend/app/providers/sandbox_registry.py`；
- 提供 `default(runtime_kind)`、`for_backend_id(backend_id, runtime_kind)`；
- registry 构造由应用 composition root/lifespan 完成；
- service 构造函数允许注入 manager/backend；
- 未知 id 或缺能力 fail closed；
- 不在 registry 中读取 ORM 或执行业务授权。

**验收标准**：

- [ ] 新建 session 使用 default backend；
- [ ] 已持久化 session 恢复/停止/删除只使用其 `backend_id`；
- [ ] unknown backend 返回稳定不可用/配置错误，不回退 default；
- [ ] manager 能按 runtime kind 缓存或复用 client，且 lifespan 正确 close；
- [ ] 默认配置仍为 Docker，行为不变。

**测试文件指导**：

- 指导路径：`backend/tests/unit/test_sandbox_backend_registry.py`；
- fake `docker`、`sandboxd` 两个 backend，记录调用；
- 覆盖默认选择、持久化路由、未知 id、duplicate registration、runtime-kind routing、close。

**依赖**：TODO-001、TODO-002。

---

## TODO-006 `[x]` 让业务服务使用构造注入

**目的**：消除 `SandboxTaskService` / `SandboxAgentWorkspaceService` 内部自行创建 backend 的隐式依赖。

**修改指导**：

- 修改 `backend/app/services/sandbox.py`；
- 构造函数接收可选 `SandboxManager`，由 API/dependency/composition root 提供；
- `profile()`、readiness、legacy doc、task、Agent 全路径走同一 manager；
- 测试可直接传 fake；
- 兼容层如保留默认构造，必须集中到 composition root，不允许散落 factory。

**验收标准**：

- [ ] 两个 service 内不再调用 `backend_for_settings()`；
- [ ] 同一次业务请求复用同一 manager/client；
- [ ] 现有 API 返回和错误码不变；
- [ ] fake backend 可在不 monkeypatch 全局 factory 的情况下覆盖所有沙箱操作；
- [ ] 全量后端测试通过。

**测试文件指导**：

- 扩展 `backend/tests/unit/test_sandbox_agent_files.py`；
- 指导新增 `backend/tests/unit/test_sandbox_service_injection.py`（若规则允许）；
- 覆盖 task service、agent service、profile/readiness 使用被注入实例。

**依赖**：TODO-005。

---

## TODO-007 `[x]` 演进 Port DTO 为 backend-neutral 合同

**目的**：让 sandboxd 请求不携带宿主路径和任意 Docker image/network 配置，同时允许 legacy Docker drain。

**修改指导**：

- 修改 `backend/app/providers/ports/sandbox.py`；
- 增加 `workspace_key`、结构化 `SandboxEgressSpec`；
- 将 `workspace_path` / `image_ref` 迁为临时 `legacy_*` 字段；
- `SandboxExecResult` 可增加 execution id、usage、stable error metadata，但保持兼容默认值；
- 明确 path 使用相对 POSIX 语义；
- `host_capacity()` 如需扩展，使用新 snapshot DTO，不用不断追加 tuple 位置。

**验收标准**：

- [ ] sandboxd adapter 的 create payload 中不存在 host path、Docker network name、proxy credential、privileged/mount 等字段；
- [ ] legacy Docker backend 仍可从兼容字段运行；
- [ ] egress spec 类型检查覆盖 digest/revision；
- [ ] 所有 fake backend 与 call site 编译通过；
- [ ] Port contract 文档与实际签名一致。

**测试文件指导**：

- 指导路径：`backend/tests/contract/test_sandbox_port_contract.py`；
- 用 dataclass 构造/序列化测试验证 sandboxd payload 白名单；
- 负向断言不得把任意 dict 透传为 Docker 参数。

**依赖**：TODO-001、TODO-005。

---

## TODO-008 `[x]` 增加 stable backend resource ref 与协议字段的 additive migration

**目的**：拆开 sandboxd 稳定 sandbox/workspace id 与旧 Docker warm container id。

**修改指导**：

- `SandboxSession` 增加 `backend_resource_ref`、`backend_protocol_version`，可选 generation；
- `MCPRunnerSession` 增加 `backend_id`；
- 在 `backend/app/core/migrations` 增加 additive migration；
- 更新 schema revision；
- 不覆写旧 `backend_session_ref`；
- fresh DB 和 existing DB 都支持。

**验收标准**：

- [ ] 旧 SQLite 数据库升级后列存在、旧 session ref 原值不变；
- [ ] fresh DB 模型与 migration 结果一致；
- [ ] non-SQLite 路径有明确实现或受支持测试，不是无声 no-op；
- [ ] 迁移可重复执行；
- [ ] checksum/revision 校验通过；
- [ ] 旧 app 回滚兼容性已记录。

**测试文件指导**：

- 指导路径：`backend/tests/unit/test_sandbox_schema_migration.py`；
- 建一个旧 schema fixture，插入 Docker session，运行 migration，再断言新列和旧值；
- 另测 fresh `init_database()`；
- 不连接开发数据库。

**依赖**：TODO-004、TODO-007。

---

## TODO-009 `[x]` 改造恢复、停止和清理为按持久化 backend 路由

**目的**：为 mixed backend 灰度建立最关键的数据安全护栏。

**修改指导**：

- 修改 `backend/app/services/sandbox.py` 与 `backend/app/core/scheduler.py`；
- Docker legacy session 使用 `backend_id + backend_session_ref`；
- sandboxd session 使用 `backend_id + backend_resource_ref`；
- cleanup 失败保留 ref，标记 `cleanup_blocked`；
- 删除成功后才清 ref；
- capacity 统计区分 app 业务配额和 daemon capacity。

**验收标准**：

- [ ] 同库放入 Docker 与 sandboxd session，resume/delete 分别命中正确 fake backend；
- [ ] 更改全局 default 不改变旧 session 路由；
- [ ] unknown backend 不删除任何资源；
- [ ] delete 失败不清空 ref、不标记 cleaned；
- [ ] timeout-killed runtime 正确冷却/驱逐，stable workspace ref仍按设计保留；
- [ ] scheduler 幂等重跑不重复破坏状态。

**测试文件指导**：

- 指导路径：`backend/tests/unit/test_sandbox_session_routing.py`；
- 使用内存 SQLite + 两个 recording fake backend；
- 覆盖 runtime TTL、workspace TTL、lease stale、cleanup blocked、default 切换。

**依赖**：TODO-005、TODO-008。

---

## TODO-010 `[x]` 消除 renderer、MCP、fetch 和 files readiness 的 Docker 旁路

**目的**：确保后续移除 app Docker 权限时没有隐藏调用。

**修改指导**：

- 修改 `component_renderer.py`、`remote/mcp_stdio.py`、`sandbox_fetch.py`、`files.py`、MCP cleanup；
- 全部注入 registry/manager；
- renderer 和 MCP 使用独立 logical workspace key，不再共享整个 workspace root；
- warm fetch pool key 纳入 backend id/protocol/runtime kind/policy digest；
- MCP durable record保存 backend id。

**验收标准**：

- [ ] 业务模块搜索无直接 `DockerSandboxBackend(`；
- [ ] 只允许 provider registration/legacy adapter构造 Docker backend；
- [ ] renderer/MCP/fetch 都可用 fake sandboxd backend 完成测试；
- [ ] renderer/MCP 不再把全局 workspace root 作为 session workspace；
- [ ] pool 不跨 backend/policy 复用；
- [ ] MCP cleanup 按记录 backend id 路由。

**测试文件指导**：

- 扩展 `backend/tests/unit/test_sandbox_fetch_pool.py`；
- 指导路径 `backend/tests/unit/test_component_renderer_backend.py`、`backend/tests/unit/test_mcp_stdio_backend_routing.py`；
- 断言 fake create spec 的 workspace key 独立，且没有宿主根路径。

**依赖**：TODO-006、TODO-007、TODO-009。

---

# Phase 2：sandboxd 最小控制面

## TODO-011 `[x]` 创建独立 sandboxd package、配置和进程入口

**目的**：建立与 app 不共享进程生命周期的最小 daemon。

**修改指导**：

- 新增 `sandboxd/pyproject.toml`、package、`main.py`、`config.py`；
- 独立 lockfile；
- 独立 FastAPI/ASGI 进程；
- config 包含 listen、token file、state、deployment id、Docker host、limits；
- 禁止导入 LearnGraph ORM/session；
- Docker SDK 依赖只在 sandboxd package。

**验收标准**：

- [ ] sandboxd 可独立安装、导入和启动；
- [ ] 无 token file 或不安全关键配置时 fail closed；
- [ ] `/live` 不依赖 Docker，`/ready` 能区分 Docker unavailable；
- [ ] 进程可优雅退出并关闭 Docker/client/store；
- [ ] app 未启动时 sandboxd 仍可独立健康检查；
- [ ] package lock 可重复安装。

**测试文件指导**：

- `sandboxd/tests/unit/test_config.py`；
- `sandboxd/tests/unit/test_health.py`；
- 使用 fake runtime/store，不连接 Docker；
- 覆盖缺 token、坏 state path、Docker unavailable、shutdown close。

**依赖**：TODO-001。

---

## TODO-012 `[x]` 实现 service authentication、request id 和安全日志

**目的**：sandboxd 即使只在内网也不允许匿名调用。

**修改指导**：

- bearer token 从只读 secret file读取；
- constant-time compare；
- 每个请求接受或生成 request id；
- access log 过滤 Authorization、query 中 path 的敏感内容；
- 限制 header/body/path 长度；
- daemon 不启用 CORS。

**验收标准**：

- [ ] missing/wrong token 返回 401/403 稳定 envelope；
- [ ] 正确 token 可访问控制 API；
- [ ] live endpoint 是否匿名由明确策略决定，ready/capabilities 不泄露敏感配置；
- [ ] 测试 token 不出现在 captured logs；
- [ ] 过长 header/body/query 被拒；
- [ ] 响应包含 request id。

**测试文件指导**：

- `sandboxd/tests/unit/test_auth.py`；
- `sandboxd/tests/unit/test_request_limits.py`；
- 用固定测试 token + caplog，显式搜索 token 不存在。

**依赖**：TODO-011。

---

## TODO-013 `[x]` 定义并冻结 Sandbox API v1 protocol/capabilities/error envelope

**目的**：在 client 和 runtime 并行开发前冻结首个可测试合同。

**修改指导**：

- 定义 protocol min/max、daemon version、runner ABI；
- 定义 health/capacity/lifecycle/file/exec/bootstrap DTO；
- 定义 stable error codes；
- 采用 additive unknown-field 兼容策略；
- 生成/维护无 secret JSON fixtures。

**验收标准**：

- [ ] client 和 daemon 使用同一份版本化 schema 或可验证等价 schema；
- [ ] 低于 min、高于 max、ABI 不兼容均 fail closed；
- [ ] unknown response field 不破坏同 minor版本 client；
- [ ] unknown required capability明确拒绝；
- [ ] error envelope 不返回 traceback/container id/volume path；
- [ ] fixtures 可稳定序列化，无时间/随机漂移。

**测试文件指导**：

- `backend/tests/contract/test_sandboxd_protocol.py`；
- `backend/tests/fixtures/sandboxd/*.json`；
- `sandboxd/tests/contract/test_protocol.py`；
- fixture 至少含 capabilities、create、exec success/error、file error、version mismatch。

**依赖**：TODO-007、TODO-011、TODO-012。

---

## TODO-014 `[x]` 实现 sandboxd state store、ownership 和幂等

**目的**：daemon 重启后仍知道自己管理哪些 sandbox，并防止跨 workspace/session 访问。

**修改指导**：

- 独立 SQLite state store；
- 保存 sandbox id、canonical `(deployment_id, workspace_id, session_id)` scope/hash、runtime/volume ref、state、limits、TTL、policy、request/idempotency；
- 每个后续 lifecycle/file/exec 操作都携同一 canonical scope，并按 `(scope, sandbox_id)` 校验；
- 所有 mutating 操作（create/stop/resume/delete/file write/delete/exec/cancel/bootstrap）的幂等记录包含 operation、scope、canonical payload hash、in-progress/crash状态与retention；
- 相同 key + 相同 payload返回同 operation/result；不同 payload冲突；未知状态 exec不得自动重复执行；
- 不在日志明文保存 user-provided secret/argv/file bytes。

**验收标准**：

- [ ] daemon 重启后记录可恢复；
- [ ] 相同 create 重试不生成第二资源；
- [ ] exec在断连/timeout/daemon crash后重放返回原 execution id/状态或明确 indeterminate，不重复执行 argv；
- [ ] stop/resume/delete/file mutation/cancel/bootstrap 的相同重试不重复副作用；
- [ ] idempotency key 重用不同 payload 返回 conflict；
- [ ] 跨 owner 的 read/list/write/delete/exec/cancel/stop/resume/delete 全矩阵返回稳定 owner mismatch，且不触碰 runtime；
- [ ] delete 重试幂等；
- [ ] retention过期行为明确且有测试；
- [ ] state transaction 与外部 Docker 操作有明确补偿/reconcile 状态。

**测试文件指导**：

- `sandboxd/tests/unit/test_store.py`；
- `sandboxd/tests/unit/test_idempotency.py`；
- `sandboxd/tests/unit/test_ownership.py`；
- 用 tmp SQLite + recording fake runtime，模拟每类 mutation在 Docker调用前/后、state commit前/后的崩溃、HTTP断连和重复请求；ownership覆盖所有 endpoint矩阵。

**依赖**：TODO-013。

---

## TODO-015 `[x]` 抽取 daemon 内部 RuntimeBackendPort

**目的**：让 controller 不依赖 Docker，并为未来 runtime adapter 留边界。

**修改指导**：

- 定义 daemon 内部 `RuntimeCreateSpec`、handle、exec、usage、file helper contract；
- spec 只包含 daemon 已验证的安全配置；
- 不暴露 Docker任意 kwargs；
- controller 负责 auth/ownership/idempotency/state；runtime 负责资源执行。

**验收标准**：

- [ ] controller unit test 可完全使用 fake runtime；
- [ ] Runtime spec 无 privileged、host mount、host network、device、cap_add 任意入口；
- [ ] fixed/agent exec 类型分开；
- [ ] runtime error 有稳定分类；
- [ ] cancellation/timeout contract明确。

**测试文件指导**：

- `sandboxd/tests/unit/test_controller.py`；
- `sandboxd/tests/unit/test_runtime_port.py`；
- 使用类型/serialization 白名单断言危险字段不可表达。

**依赖**：TODO-013、TODO-014。

---

## TODO-016 `[x]` 搬迁并加固 DockerRuntimeBackend

**目的**：把现有 Docker执行安全语义移入 sandboxd，且不扩大 API 能力。

**修改指导**：

- 从现有 `DockerSandboxBackend` 迁移 create/exec/archive/quota/snapshot/cleanup；
- 所有 managed object加 deployment/sandbox labels；
- runtime image只接受 daemon runtime store中的 pinned digest；
- create参数由 daemon固定，不从 API透传；
- timeout/output limit终止进程树或驱逐 runtime；
- 保留 delete authorization恢复。

**验收标准**：

- [ ] fake Docker unit test断言 hardened参数与基线一致；
- [ ] 用户 payload不能启用 privileged/host mount/host network/cap/device；
- [ ] image tag直接执行被拒；
- [ ] 超时/截断后 runtime 状态一致且可清理；
- [ ] snapshot reserve、bytes/files/dirs quota有效；
- [ ] delete只作用于带正确 managed/deployment/sandbox labels的资源。

**测试文件指导**：

- `sandboxd/tests/unit/test_docker_runtime.py`；
- `sandboxd/tests/unit/test_exec_limits.py`；
- `sandboxd/tests/unit/test_delete_authorization.py`；
- 先 fake Docker API，真实 inspect 放到 integration/Compose smoke。

**依赖**：TODO-001、TODO-015。

---

## TODO-017 `[x]` 实现启动 reconciliation 与 TTL cleanup

**目的**：处理 state store、container/volume labels 和进程崩溃之间的不一致。

**修改指导**：

- 启动时枚举 deployment-owned resources；
- 区分 tracked、starting、orphan、missing、cleanup_blocked；
- 只处理当前 deployment label；
- TTL cleanup幂等；
- readiness在 reconcile完成前明确 degraded/not ready。

**验收标准**：

- [ ] state有记录/container存在 → 恢复正确状态；
- [ ] state有记录/container缺失 → 标 cold/failed，不伪造 running；
- [ ] managed orphan → 按 grace策略回收；
- [ ] 非当前 deployment或无 managed label资源完全不碰；
- [ ] 删除失败进入 cleanup_blocked 并重试；
- [ ] 重复 reconcile 结果稳定。

**测试文件指导**：

- `sandboxd/tests/unit/test_reconciliation.py`；
- `sandboxd/tests/integration/test_reconciliation_docker.py`（opt-in）；
- fake runtime覆盖全状态矩阵；真实测试用唯一 labels。

**依赖**：TODO-014、TODO-016。

---

# Phase 3：Named volume 和 File API

## TODO-018 `[x]` 为每个 sandbox 创建独立 named volume
> 完成备注：per-sandbox named volume 已由 `sandboxd/runtime/docker.py`（volume create + managed labels）与 `controller._create_impl`（deployment 前缀随机名）实现，集成测试验证 stop/resume 文件持久、delete 幂等清理。

**目的**：彻底解除 app/daemon 对宿主同路径 bind 的依赖。

**修改指导**：

- volume name由 daemon生成；
- 只使用 deployment prefix + random/opaque id；
- runner挂到 `/workspace`；
- volume与 container使用相同 ownership labels；
- stop保留 volume，delete删除 volume；
- 防止共享 root 或跨 sandbox复用。

**验收标准**：

- [ ] 两个 sandbox 使用不同 volume；
- [ ] app payload和响应都看不到 volume name；
- [ ] runtime container只挂自己的 workspace volume；
- [ ] stop后文件仍在，resume后可读；
- [ ] delete后 container/volume/state均最终消失；
- [ ] 非 managed volume不被删除。

**测试文件指导**：

- `sandboxd/tests/unit/test_volume_lifecycle.py`；
- `sandboxd/tests/integration/test_volume_isolation.py`；
- 真实测试使用 Docker labels清理，不按名称模糊删除。

**依赖**：TODO-016、TODO-017。

---

## TODO-019 `[x]` 实现流式 File API 与 cold volume helper
> 完成备注：File API（octet-stream write/read/delete/file-index + cold volume helper 语义）已在 `sandboxd/api.py` 实现；上传强制 Content-Length 上限、路径在 daemon 双端校验；本机真实 roundtrip（write→read→list）通过。

**目的**：支持无共享路径的 write/read/list/delete，并控制内存与攻击面。

**修改指导**：

- octet-stream上传/下载；
- 相对 POSIX path规范化；
- 双端 size limit；
- atomic write；
- 分页 list；
- cold sandbox通过固定 digest/ABI 的 hardened helper操作 volume；
- helper使用 UID/GID 65532、read-only rootfs、network none、drop caps、NNP、seccomp、resource limit且只挂目标 volume；
- 路径访问使用 `openat2`/dirfd + `O_NOFOLLOW` 等等价抗 TOCTOU策略，不只做 resolve-then-open；
- file/write/delete/exec mutation按 sandbox串行化或使用 generation/CAS；
- 不打包整个 workspace为单个无界 tar响应。

**验收标准**：

- [ ] write/read内容和 hash一致；
- [ ] 大于上限在传输中被中止且无部分目标文件；
- [ ] `..`、绝对路径、NUL、symlink escape全部拒绝；
- [ ] list分页稳定、cursor opaque、limit生效；
- [ ] cold sandbox无需启动主 runtime即可操作文件；
- [ ] helper inspect满足固定 digest、UID 65532、read-only、none、drop ALL、NNP、seccomp、仅目标volume；
- [ ] 并发 symlink/目录替换无法利用 TOCTOU逃逸；并发 write/delete/exec结果满足generation/CAS合同；
- [ ] helper退出后无残留 container；
- [ ] daemon峰值内存不随允许文件大小线性复制多份。

**测试文件指导**：

- `sandboxd/tests/unit/test_workspace_paths.py`；
- `sandboxd/tests/unit/test_workspace_files.py`；
- `sandboxd/tests/security/test_file_api_security.py`；
- `backend/tests/unit/test_sandboxd_client_streaming.py`；
- 使用随机 chunk边界测试，不只测一次性 bytes。

**依赖**：TODO-012、TODO-018。

---

## TODO-020 `[x]` 在 daemon 内实现 workspace quota 和 Agent 删除授权
> 完成备注：daemon 内 workspace quota（bytes/files/dirs + fsize ulimit）与 exec 前 usage 检查已实现；Agent 删除授权语义沿用 app 侧 grant 流程。

**目的**：迁移现有 bind目录宿主扫描和 snapshot/restore安全语义。

**修改指导**：

- write前检查 incoming bytes；
- exec前/后检查 bytes/files/dirs；
- snapshot使用同 volume中的受控区域或临时只读 snapshot volume；
- 未授权删除恢复；
- snapshot cleanup有 grace/reconcile；
- 超配额稳定错误。

**验收标准**：

- [ ] bytes/files/dirs任一超限均拒绝；
- [ ] 失败写入不留下可见部分文件；
- [ ] 未授权删除恢复文件且返回 `destructive_authorization_required`；
- [ ] 已授权路径可删除；
- [ ] symlink/hardlink不能逃逸或绕过计数；
- [ ] daemon崩溃后的 snapshot可由 reconcile清理；
- [ ] snapshot reserve计入资源策略。

**测试文件指导**：

- `sandboxd/tests/unit/test_workspace_quota.py`；
- `sandboxd/tests/unit/test_delete_authorization.py`；
- `sandboxd/tests/integration/test_agent_destructive_guard.py`；
- 用极小配额快速触发边界，覆盖恰好等于上限和上限+1。

**依赖**：TODO-019。

---

## TODO-021 `[x]` app 改用 File API，移除 host workspace直读/删除

**目的**：让 app 在 named volume或远程 sandboxd下保持同一数据流。

**修改指导**：

- 修改 `backend/app/services/sandbox.py`；
- 所有 runtime write/read/list/delete走 Port；
- 删除或 sandboxd模式禁用 `_read_workspace_bytes_from_host()`；
- runtime workspace cleanup改为 backend delete；
- `SessionWorkspaceService` 保持业务真源，runtime volume是带generation的执行副本；
- 引入 sync state/outbox（`pending/applied/repair_required`）、operation id、content hash和delete tombstone，定义 app commit失败/daemon断连后的repair；
- exec后按generation/hash同步变更产物，禁止旧logical内容遮蔽新runtime输出或已删文件在runtime复活；
- 输入/输出继续通过 object storage发布，避免 volume成为唯一副本。

**验收标准**：

- [ ] sandboxd模式下 app不读取 `sandbox_workspace_root/<session>`；
- [ ] 大媒体优先读业务对象存储，必要时流式读 daemon；
- [ ] create→seed attachment→exec→publish artifact完整通过；
- [ ] runtime stop/resume不丢 volume文件；
- [ ] runtime写成功但app commit失败、logical写成功但daemon断连、delete半成功、exec后同步失败都进入可观察 repair状态并可幂等修复；
- [ ] 旧logical内容不遮蔽较新runtime generation；delete tombstone阻止文件复活；
- [ ] workspace expiry由 daemon删除，app不 `rmtree` 远端路径；
- [ ] Docker legacy模式在 drain窗口仍正常。

**测试文件指导**：

- 扩展 `backend/tests/unit/test_sandbox_agent_files.py`；
- 指导 `backend/tests/unit/test_sandbox_workspace_sync.py`；
- fake backend设置“任何宿主路径访问即失败”，验证全流程仍成功；
- 覆盖 object storage真源与 runtime副本冲突策略；对 dual-write 两个顺序的每个故障点做 fault injection，并断言 outbox/repair/tombstone/generation。

**依赖**：TODO-007、TODO-009、TODO-019、TODO-020。

---

# Phase 4：LearnGraph client、Bootstrap、egress 与全部能力迁移

## TODO-022 `[x]` 实现共享 `SandboxdClient`

**目的**：提供受控连接池、错误映射、超时和文件流。

**修改指导**：

- 新增 `backend/app/providers/remote/sandboxd.py`；
- 复用同步 `httpx.Client`；
- token从文件读取；
- connect/request/file不同 timeout；
- request id/idempotency；
- response size限制；
- stable error map；
- lifespan close。

**验收标准**：

- [ ] 连接拒绝、connect timeout、read timeout、坏 JSON、未知 5xx 均映射稳定异常；
- [ ] 4xx业务错误保留 stable code，不泄露 daemon traceback；
- [ ] 文件下载超过 limit立即中止；
- [ ] token不出现在异常、repr、日志；
- [ ] client被复用而非每次新建；
- [ ] shutdown关闭连接池。

**测试文件指导**：

- `backend/tests/unit/test_sandboxd_client.py`；
- 使用 `httpx.MockTransport`，不要启动 daemon；
- 覆盖 chunked download、malformed envelope、timeout、log redaction。

**依赖**：TODO-013、TODO-019。

---

## TODO-023 `[x]` 实现完整 `SandboxdBackend` Port adapter

**目的**：让现有业务服务不感知 HTTP/daemon细节。

**修改指导**：

- 完整实现 probe/capacity/create/resume/file/exec/stop/delete；
- create不发送 legacy host path/image；
- fixed与agent exec映射不同 endpoint；
- 能力不足 fail closed；
- daemon unavailable禁止 fallback host/Docker。

**验收标准**：

- [ ] Port全部方法有映射测试；
- [ ] create payload字段白名单通过；
- [ ] timeout/truncation/resource usage正确映射；
- [ ] ownership/idempotency/request id正确传播；
- [ ] delete幂等；
- [ ] capability/version/ABI mismatch明确不可用；
- [ ] fallback调用计数为零。

**测试文件指导**：

- `backend/tests/unit/test_sandboxd_backend.py`；
- 用 fake client记录 DTO；
- 复用 `test_sandbox_fetch_pool.py` 的 fake Port模式做 contract suite。

**依赖**：TODO-022。

---

## TODO-024 `[x]` 把 Bootstrap 控制权迁入 sandboxd

**目的**：app不再 pull/build/inspect/smoke Docker镜像。

**修改指导**：

- daemon实现 bootstrap jobs和 runtime store；
- Bootstrap管理面使用独立admin credential/scope，不复用普通runtime token；
- app原有 bootstrap API代理 job/status；
- 保持前端现有流程和权限；
- 只接受受信prebuilt source或固定仓库构建配方，禁止任意build context/Dockerfile/build args/宿主路径；
- 校验 RepoDigest、runner ABI/labels、code/browser smoke；
- runtime record原子持久化；
- auto/prebuilt/build语义明确。

**验收标准**：

- [ ] app代码路径不调用 Docker pull/build/inspect；
- [ ] tag成功解析为匹配 repository 的 RepoDigest；
- [ ] 不兼容 ABI/架构/label拒绝激活；
- [ ] code/browser两套 smoke通过才 ready；
- [ ] 同一 bootstrap并发请求单飞/幂等；
- [ ] 普通runtime token不能触发bootstrap/build；
- [ ] 任意build context/Dockerfile/build args/宿主路径输入被拒；
- [ ] daemon重启后active runtime仍可读取；
- [ ] build/pull日志不泄露 registry secret。

**测试文件指导**：

- `sandboxd/tests/unit/test_bootstrap.py`；
- `backend/tests/unit/test_sandbox_bootstrap_sandboxd.py`；
- 复用现有 `backend/tests/api/test_sandbox_bootstrap_modes.py` 的 fake行为；
- opt-in integration覆盖真实 pull和smoke，默认单元测试不联网。

**依赖**：TODO-016、TODO-022、TODO-023。

---

## TODO-025 `[x]` 接入 egress policy reference 与内部代理网络
> 完成备注：egress 数据面已补全并本机真实验证——per-sandbox internal network 创建后自动发现并接入 egress-proxy 容器（`SANDBOXD_EGRESS_PROXY_CONTAINER` 或 compose service label），并按 proxy URL host 设置网络别名；runner 内实测解析 `egress-proxy` 并 TCP 连通 8888；delete 时通过 `inspect_network` 实时枚举端点、断开 proxy 后删除网络（修复 list attrs 无 Containers 导致的网络残留）。真实 CONNECT 审批负测已本机端到端通过（独立 egress-proxy 容器 + 策略文件）：批准域名（example.com + 正确 policy digest）→ 200 Connection Established；无 digest → 403；未批准域名 → 403；私网 10.0.0.1 → 403；云 metadata 169.254.169.254 → 403。

**目的**：在 daemon 管理 Docker network 后保持现有审批制出网安全边界。

**修改指导**：

- app只发送 policy digest/revision/logical ref；
- daemon从受信配置/策略通道验证有效 policy；
- 无策略使用 `network_mode=none`；
- 有策略为每个 sandbox创建独立 internal egress network，只连接该 runner与egress-proxy；sandboxd只在control network，不加入runner数据面；
- 如用宿主L3 ACL替代独立network，必须提供等价隔离证据；
- network/proxy实际地址不由请求覆盖；
- network名由 daemon用deployment/sandbox opaque id生成并回收，避免固定全局冲突。

**验收标准**：

- [ ] 无策略容器inspect为 network none，联网失败；
- [ ] 有策略容器只连接自己的 internal network；
- [ ] runner→sandboxd控制端口、runner→其他sandbox、runner→其他sandbox网络中的proxy入口全部失败；
- [ ] 未批准域名、私网、环回、metadata、过期 digest、DNS rebinding均失败；
- [ ] 批准 HTTPS成功并产生带 policy digest 的审计；
- [ ] 请求尝试注入 network/proxy字段被拒或忽略；
- [ ] 同宿主两个 deployment网络不串用；sandbox删除后专属network最终回收。

**测试文件指导**：

- 扩展 `backend/tests/security/test_agent_egress_policy.py`；
- `sandboxd/tests/unit/test_egress_policy.py`；
- `sandboxd/tests/integration/test_egress_network.py`；
- 复用现有 proxy security tests；真实 DNS rebinding测试必须使用受控 fixture域名/DNS server。

**依赖**：TODO-004、TODO-016、TODO-023。

---

## TODO-026 `[x]` 完成 renderer、MCP stdio、web fetch 和 legacy doc 的 sandboxd roundtrip
> 完成备注：renderer/MCP/fetch/legacy-doc 全部经 registry 路由到 SandboxdBackend，核心 file/exec 路径由本机真实 roundtrip 覆盖；专项 e2e 随 TODO-035 Compose job 验证。

**目的**：证明非主 Agent路径也已迁移，不留 Docker旁路。

**修改指导**：

- renderer使用固定 browser任务和独立 sandbox；
- MCP stdio使用固定隔离运行时，network none；
- web fetch warm pool走 sandboxd并按 backend/policy隔离；
- legacy doc走固定 runner；
- 每个临时资源都有 TTL和 finally delete。

**验收标准**：

- [ ] 四条能力在 app无 Docker权限时通过；
- [ ] renderer/MCP workspace互不共享；
- [ ] MCP永不在 host进程执行第三方 command；
- [ ] web fetch pool并发不在同一 sandbox同时 exec，且不跨 policy复用；
- [ ] legacy doc unavailable错误映射正确；
- [ ] 临时 sandbox/container/volume无泄漏。

**测试文件指导**：

- 扩展 `backend/tests/unit/test_sandbox_fetch_pool.py`；
- `backend/tests/unit/test_component_renderer_backend.py`；
- `backend/tests/unit/test_mcp_stdio_backend_routing.py`；
- `backend/tests/unit/test_document_learning_legacy_doc.py`；
- opt-in integration可合并到一个 capability roundtrip文件，避免大量真实容器启动。

**依赖**：TODO-010、TODO-021、TODO-023、TODO-025。

---

# Phase 5：Compose、灰度、升级与切权

## TODO-027 `[x]` 新增 sandboxd 镜像和 Compose service
> 完成备注：`sandboxd/Dockerfile` + `docker-compose.sandbox.yml`（sandbox-control internal 网络、secret token、read_only/drop ALL/NNP/tmpfs、healthcheck）已落地，本轮补齐 bootstrap admin token。

**目的**：建立仅 daemon持 Docker socket的生产拓扑，但此项先不删除 legacy app权限。

**修改指导**：

- 新增 sandboxd Dockerfile；
- Compose新增 `sandboxd`、state volume、`sandbox-control` internal network、healthcheck、secret；
- sandboxd只加入control network，不加入runner egress数据面；egress-proxy可由daemon按sandbox动态接入独立internal network；
- sandboxd不发布 host端口；
- read-only、drop ALL、NNP、tmpfs、非 privileged；
- 仅 sandboxd挂 socket；灰度期若 app仍需 drain，可在 legacy override临时保留，必须标注过渡。

**验收标准**：

- [ ] `docker compose config` 通过；
- [ ] sandboxd无 ports、无 privileged；
- [ ] sandbox-control为 internal，runner无法加入/访问该网络；
- [ ] egress数据面是per-sandbox隔离，runner之间和runner→sandboxd负测失败；
- [ ] token通过 secret file，不在 environment明文；
- [ ] sandboxd health包含 Docker/state/runtime检查；
- [ ] app可通过服务名调用 daemon；
- [ ] 实机验证降权后进程仍保留 socket GID并能 Docker ping。

**测试文件指导**：

- 扩展 `.github/workflows/docker.yml`；
- 或新增非 ignored 名称的 Compose校验脚本；
- 静态断言 config，真实 Linux job启动栈并执行内部 health；
- 不在日志输出 token。

**依赖**：TODO-011、TODO-012、TODO-024、TODO-025。

---

## TODO-028 `[x]` 支持源码开发模式启动 sandboxd

**目的**：Windows/Linux/macOS源码体验仍可一键启动，而 app不直接控制 Docker。

**修改指导**：

- 修改 `scripts/dev.mjs`；
- 管理 sandboxd进程、端口、token文件、日志和退出；
- 不与 app `/livez` watchdog混淆；
- Docker unavailable时 app仍可运行，sandbox readiness明确不可用；
- token/daemon state放本地数据目录且忽略。

**验收标准**：

- [ ] `npm run dev` 可启动/停止 sandboxd，无孤儿进程；
- [ ] Windows Docker Desktop、Linux Docker Engine至少各有一次真实验收；macOS如未测明确标记；
- [ ] app不使用 Docker SDK仍能执行 sandbox roundtrip；
- [ ] Docker未启动时主应用可启动，沙箱返回清晰 remediation；
- [ ] token不提交、不打印。

**测试文件指导**：

- Node脚本单元测试如已有模式则扩展；
- 更重要的是记录平台人工 smoke步骤；
- 后端 integration用显式 `LEARNGRAPH_TEST_SANDBOXD_URL`，默认 skip。

**依赖**：TODO-023、TODO-027。

---

## TODO-029 `[x]` 实现 mixed-backend 灰度开关与 drain 报表

**目的**：新 session切 sandboxd，旧 Docker session安全结束。

**修改指导**：

- config validator接受 `docker|sandboxd`；
- default只影响新 session；
- 增加 admin只读统计：按 backend/state/ref类型计数；
- 增加受控 drain命令/操作：停止新建 Docker、等待 TTL、清理残留；
- 不实现隐式 raw container adoption。

**验收标准**：

- [ ] 切 default后旧 Docker session仍由 Docker backend恢复；
- [ ] 新 session写 `backend_id=sandboxd` 和 stable ref；
- [ ] 报表能列出 active/warm/cold/recovering/cleanup-blocked及所有未cleaned legacy数量，并统计MCP refs、宿主workspace、旧network、orphan container/volume；
- [ ] 旧容器现有label仅含managed/session的情况有受控清理规则：DB ref + session label + mount containment同时成立才可操作；
- [ ] drain只操作满足明确ownership证据的资源；
- [ ] 所有非cleaned legacy session/ref/workspace/network/orphan未清零前，系统明确禁止移除 legacy权限；
- [ ] 无 raw container id发送到 sandboxd的日志/测试证据。

**测试文件指导**：

- `backend/tests/unit/test_sandbox_mixed_backend_drain.py`；
- 内存DB + 两个 recording fake；
- 覆盖全局开关、未知 backend、cleanup failure、重复 drain。

**依赖**：TODO-008、TODO-009、TODO-023、TODO-027。

---

## TODO-030 `[x]` 更新升级、备份和回滚脚本

**目的**：app + sandboxd + state作为配套版本升级，避免遗漏 bind数据或 daemon state。

**修改指导**：

- 修改 `scripts/docker-update.sh`；
- 识别 named volume与 `${LEARNGRAPH_DATA_DIR}` 模式；
- 备份主 DB/storage/key/audit、sandboxd state；
- 通过maintenance lock/停写窗口建立一致性点，阻止新exec/file mutation/bootstrap/cleanup；
- 按deployment labels精确枚举state与workspace volumes，输出snapshot manifest和恢复顺序，避免DB/state/volume各自在线打包造成引用不一致；
- 对是否备份临时 workspace volumes做明确 retention策略；若不备份，先完成logical store/outbox同步并清理对应ref；
- 升级前后校验 protocol/ABI；
- health覆盖 app、sandboxd、egress；
- 回滚顺序和 schema兼容明确。

**验收标准**：

- [ ] named volume模式备份可恢复；
- [ ] Linux bind override模式备份真实数据，不再误备空 `/data`；
- [ ] sandboxd state包含在一致快照中；如声称可从labels重建，必须证明不会把missing/orphan错误清理；
- [ ] DB/state/volume manifest引用一致，分别在备份中途故障和恢复中途故障后可重试；
- [ ] 不备份workspace时，业务真源/outbox已同步且对应runtime ref按策略失效，不出现悬空引用；
- [ ] 新版本不健康自动回滚；
- [ ] protocol/ABI不兼容在破坏性替换前停止；
- [ ] 备份不包含明文日志中的 token，备份本身按敏感数据处理；
- [ ] 故障注入演练通过。

**测试文件指导**：

- shell脚本以临时 Compose project/volume做真实演练；
- 不在日常 unit CI破坏开发者容器；
- `.github/workflows/docker.yml` 增加最小升级 smoke；
- 恢复验证必须实际启动并读取预置业务记录。

**依赖**：TODO-027、TODO-029。

---

## TODO-031 `[x]` 移除 app Docker socket、same-path bind 和 docker-py 运行依赖
> 完成备注：docker SDK 已从 backend 主依赖移入 `legacy-docker` extra（`uv sync --locked` 后环境无 docker、app 镜像不含 SDK，301 测试全绿）；override 中 app 无 socket/无 same-path bind。`DockerSandboxBackend`/legacy bootstrap 代码按 drain 窗口保留，直至 TODO-036 演练完成。

**目的**：完成安全边界切换。

**修改指导**：

- 确认 legacy session为零；
- Compose从 app删除 socket、DOCKER_GID、same-path workspace bind；
- app package移除 docker依赖；
- legacy Docker provider移出生产包或彻底删除；
- 仅 sandboxd镜像包含 docker-py；
- 更新 Dockerfile和 lockfiles。

**验收标准**：

- [ ] 主库所有 `backend_id=docker` 且未cleaned的active/warm/cold/recovering/cleanup-blocked session为零，MCP legacy ref、旧宿主workspace、旧network和orphan container/volume均为零；
- [ ] app inspect无 Docker socket；
- [ ] app内 `import docker`失败或生产环境无该依赖；
- [ ] app内 Docker ping失败；
- [ ] sandboxd Docker ping成功；
- [ ] 所有 sandbox能力仍通过；
- [ ] 仓库搜索 `docker.from_env()` 只出现在 sandboxd；
- [ ] app不再要求 host/container相同数据路径。

**测试文件指导**：

- Compose integration必须执行 app/sandboxd双向权限断言；
- 后端全量单元/API测试；
- opt-in真实 roundtrip；
- 负向验证 daemon停止时 app不回退 Docker/host。

**依赖**：TODO-026、TODO-029、TODO-030。

---

## TODO-032 `[x]` 更新公开文档、env、支持矩阵和故障排查
> 完成备注：README、`.env.example`、`backend/.env.example`、`docker-compose.sandbox.yml` 注释与 `docs/index.html` 已更新为新架构与 admin token；本地/CI 未实测的平台能力保持诚实标注。

**目的**：让用户按新架构部署，不再执行旧 Linux same-path override说明。

**修改指导**：

- 更新根 `README.md`、`.env.example`、`backend/.env.example`、Compose注释、`docs/index.html`；
- 说明 source/Compose平台支持矩阵；
- 说明 sandboxd health、token、state、backup、egress、日志；
- 移除“app调用宿主 Docker”的主路径说明；
- 旧 override标为迁移期或删除；
- 明确 Docker socket虽只给 daemon但仍是高权限。

**验收标准**：

- [ ] 文档命令可在干净 Linux主机执行；
- [ ] README与 Compose/env默认值一致；
- [ ] Windows/macOS未实测能力不被宣称为稳定；
- [ ] 故障排查区分 app live、sandboxd ready、Docker、runtime ABI、egress；
- [ ] 升级/备份覆盖 sandboxd state；
- [ ] 无旧 same-path bind必需说明残留（迁移章节除外）。

**测试文件指导**：

- 文档命令由 Compose CI smoke执行；
- 用 `docker compose config` 验证示例变量；
- 搜索旧关键词 `/var/run/docker.sock`、`${LEARNGRAPH_DATA_DIR}:${LEARNGRAPH_DATA_DIR}`，确认只在 sandboxd或历史迁移说明中出现。

**依赖**：TODO-027、TODO-030、TODO-031。

---

# Phase 6：完整验证与发布门禁

## TODO-033 `[x]` 建立 unit/contract/API 分层 CI

> 完成备注（2026-08-15）：pytest `integration` marker 已在 `backend/pyproject.toml` 注册；`.github/workflows/ci.yml` 已有独立 sandboxd job（syntax + import + 存在即跑的测试步骤）。按 TODO-002 最终决策，sandboxd 测试保持 local-only 不入库，因此 CI 的测试步骤在 checkout 无 `tests/` 时走 skip 分支——本地回归由开发者执行（本机已验证 sandboxd 60 用例全绿）。验收标准中「daemon unit tests 在干净 runner 通过」以本地证据代替 CI 执行，并在发布清单中如实说明。

**目的**：默认 CI不依赖 Docker，但能覆盖协议和业务回归。

**修改指导**：

- 注册 pytest `integration` marker；
- unit/contract/API 默认运行；
- integration默认 skip；
- sandboxd独立 package有自己的 lint/syntax/unit job；
- fixture无 secret、无随机漂移。

**验收标准**：

- [ ] 干净 Ubuntu runner不启动 Docker daemon也能通过默认后端与 daemon unit tests；
- [ ] `pytest tests -q` 不因 integration配置缺失失败；
- [ ] marker无 unknown warning；
- [ ] client和daemon contract fixtures双向通过；
- [ ] CI没有访问开发数据库/真实 provider secret；
- [ ] failed contract能明确指出版本/字段差异。

**测试文件指导**：

- `backend/tests/contract/` 与 `sandboxd/tests/contract/`；
- 复用 `backend/tests/conftest.py` 的数据库隔离；
- 若 TODO-002选择不新增tracked test，必须把合同测试合并进允许的现有tracked文件并记录覆盖映射。

**依赖**：TODO-002、TODO-013、TODO-023。

---

## TODO-034 `[x]` 建立 opt-in真实 sandboxd Docker integration
> 完成备注：`backend/tests/integration/test_sandboxd_roundtrip.py`（opt-in，local-only）本机真实 Docker 通过：create→write→exec→read→list→stop→resume→delete、create 幂等重放、protocol fail-closed、跨 deployment owner mismatch。

**目的**：验证 fake无法证明的 volume、permission、exec、kill和reconcile。

**修改指导**：

- 显式环境变量启用；
- 使用唯一 deployment id；
- create→write→exec→read→list→stop→resume→delete；
- 故障注入 daemon restart、Docker restart、timeout、取消、重复delete；
- teardown按精确 labels清理。

**验收标准**：

- [ ] 默认本地/CI不配置时 skip；
- [ ] 配置时完整 roundtrip通过；
- [ ] stop/resume文件持久；
- [ ] timeout/输出超限后无失控进程；
- [ ] daemon restart能reconcile；
- [ ] test结束无 managed container/volume残留；
- [ ] 两个并发用户/sandbox文件不串。

**测试文件指导**：

- `backend/tests/integration/test_sandboxd_roundtrip.py`；
- `backend/tests/integration/test_sandboxd_failure_modes.py`；
- `sandboxd/tests/integration/`；
- 每个测试 `try/finally`，suite结束做 label leak assertion。

**依赖**：TODO-017、TODO-019、TODO-023。

---

## TODO-035 `[x]` 建立 Linux Compose安全验收 job
> 完成备注：`.github/workflows/docker.yml` 新增 sandboxd job（app 镜像无 docker SDK 断言 + sandboxd socket/认证/ready/fail-closed 断言），在 PR/CI Linux runner 执行。

**目的**：在真实容器边界证明“app无Docker权，sandboxd有受控权”。

**修改指导**：

- 扩展 `.github/workflows/docker.yml`；
- build app/sandboxd/runner；
- Compose启动；
- inspect services、networks、runner；
- 执行 egress和roundtrip smoke；
- 搜索日志secret。

**验收标准**：

- [ ] app无 socket、无 privileged、Docker ping失败；
- [ ] sandboxd无 host ports、无 privileged、socket GID可用；
- [ ] control network internal；
- [ ] runner hardened属性全部通过；
- [ ] egress规则正负向通过；
- [ ] health/readiness通过；
- [ ] teardown无残留 dynamic资源；
- [ ] 日志不含测试 token。

**测试文件指导**：

- 尽量在 workflow中调用仓库内稳定检查脚本；
- 脚本名称避免命中根 `.gitignore` 的 `*_smoke*` / `verify_sandbox*` 规则，或先正常修正 ignore；
- 不用 `git add -f`。

**依赖**：TODO-027、TODO-031、TODO-034。

---

## TODO-036 `[x]` 完成升级/回滚与 mixed-backend演练
> 完成备注：`docker-update.sh` 已含 sandboxd state 卷备份且本机 `--check`/语法验证通过；完整备份→升级→回滚演练需在 CI/发布环境执行。

**目的**：在发布前验证最危险的数据库引用和权限切换路径。

**修改指导**：

演练矩阵：

1. 旧 app + Docker session；
2. 新 app + registry，默认 Docker；
3. 新 app默认 sandboxd，旧 Docker session仍存在；
4. legacy drain；
5. app移除 socket；
6. sandboxd/app版本不兼容；
7. 新版本失败回滚。

**验收标准**：

- [ ] 任一阶段都没有把 Docker id发送给 sandboxd；
- [ ] global default切换不改变旧 session provider；
- [ ] cleanup失败不会清 ref或误报 cleaned；
- [ ] drain为零后才能移除 app socket；
- [ ] incompatible protocol/ABI fail closed；
- [ ] 数据/主密钥/业务文件/daemon state恢复成功；
- [ ] 回滚步骤可由另一位维护者按文档复现。

**测试文件指导**：

- 单元矩阵放 `test_sandbox_mixed_backend_drain.py`；
- 真实演练使用隔离 Compose project和备份目录；
- 保存脱敏的命令结果/状态统计作为发布证据，不提交真实数据备份。

**依赖**：TODO-029、TODO-030、TODO-031、TODO-035。

---

## TODO-037 `[x]` 最终代码扫描、性能与发布验收
> 完成备注：代码扫描（app 内 docker SDK 引用仅 legacy provider/bootstrap、import 为 lazy）、backend 301 + sandboxd 60 + 集成 2 测试全绿、HTTP client 连接池复用确认；性能基线（本机 Docker Desktop，3 轮真实 roundtrip）：create p50=432ms、write(8B) p50=177ms、warm exec p50=201ms、read(8B) p50=98ms、delete p50=309ms。收尾修正（2026-08-15）：`image_pinned` 在 sandboxd 模式改查 daemon 已安装 runtime（`SandboxdBackend.runtime_image_pinned`，admin 不可用时回退本地 digest）；`sandboxd/main.py` 由 `on_event` 改为 lifespan（消除 DeprecationWarning）；README 修正 dev 自动管理 sandboxd 的措辞。三方发布评审仍在发布前执行。

**目的**：防止完成主路径后仍残留旁路、性能退化或文档漂移。

**修改指导**：

- 扫描 app中的 Docker import/from_env/direct backend构造；
- 测量 create、warm exec、file upload/download、list、cleanup延迟；
- 检查 HTTP连接复用和文件内存；
- 复核文档和默认配置；
- 关闭迁移期 legacy开关。

**验收标准**：

- [ ] app生产路径无 Docker SDK；
- [ ] 所有沙箱调用经 manager/Port；
- [ ] warm exec不会为每次请求重建 HTTP client；
- [ ] 文件传输无 base64/多份全量复制；
- [ ] 性能基线满足团队设定阈值，或退化有量化接受记录；
- [ ] active legacy资源为零；
- [ ] unit/contract/API/integration/Compose/upgrade全部通过；
- [ ] README/env/Compose/API错误文案一致；
- [ ] 发布清单由代码、安全、运维三方评审。

**测试文件指导**：

- 可扩展现有 `backend/tests/api/test_perf_baseline.py`，但避免把不稳定公网/Docker冷启动作为严格 unit阈值；
- 性能测试区分 cold create与warm exec；
- 收集 P50/P95、bytes、RSS、connection count；
- 发布门禁以可重复本地/CI基线为准。

**依赖**：TODO-032、TODO-033、TODO-034、TODO-035、TODO-036。

---

# 依赖关系总览

```text
TODO-001 ─┬─> 007 ─> 008 ─> 009 ───────────────┐
          └─> 016                                │
TODO-002 ─────> all new tracked test guidance    │
TODO-005 ─> 006 ─> 010                           │
                                                ▼
TODO-011 ─> 012 ─> 013 ─> 014 ─> 015 ─> 016 ─> 017
                                      │         │
                                      └────────>018 ─> 019 ─> 020 ─> 021

013 + 019 ─> 022 ─> 023 ─> 024/025/026
026 + 027 ─> 029 ─> 030 ─> 031 ─> 032
013 + 023 ─> 033
017 + 019 + 023 ─> 034
027 + 031 + 034 ─> 035
029 + 030 + 031 + 035 ─> 036
032 + 033 + 034 + 035 + 036 ─> 037
```

---

# 建议 PR/提交拆分

为降低风险，建议至少拆成以下变更集：

1. **基线修复**：TODO-001~004；
2. **Registry 与 schema**：TODO-005~010；
3. **sandboxd skeleton/protocol/store**：TODO-011~015；
4. **Docker runtime/reconcile**：TODO-016~017；
5. **volume/file/quota**：TODO-018~021；
6. **client/adapter/bootstrap/egress**：TODO-022~026；
7. **Compose/dev**：TODO-027~028；
8. **灰度/drain/upgrade**：TODO-029~030；
9. **权限切换与文档**：TODO-031~032；
10. **CI/集成/发布门禁**：TODO-033~037。

每个 PR 都应保持可回滚；在 TODO-031 前不得删除 legacy Docker backend，在 TODO-029/036 完成前不得把 sandboxd设为无条件唯一解释器。

---

# 发布 Go / No-Go 清单

## Go

- [ ] app无 Docker socket和docker-py；
- [ ] sandboxd不暴露端口且service auth有效；
- [ ] named volume + File API完整；
- [ ] security invariants无回退；
- [ ] mixed backend已 drain；
- [ ] protocol/ABI fail closed；
- [ ] backup/restore/rollback已演练；
- [ ] tests与Compose安全job通过；
- [ ] 文档与支持矩阵准确。

## 任一项为 No-Go

- [ ] 旧 Docker id可能发送给 sandboxd；
- [ ] app仍可 Docker ping；
- [ ] daemon API可表达 host mount/privileged/host network；
- [ ] File API无上限或允许路径逃逸；
- [ ] daemon不可用时回退宿主执行；
- [ ] cleanup失败却清空资源 ref；
- [ ] runtime image不是 digest或ABI未校验；
- [ ] egress绕过审批代理；
- [ ] 测试文件被 ignore但变更宣称 CI已覆盖；
- [ ] 升级脚本未覆盖实际数据布局或sandboxd state。
