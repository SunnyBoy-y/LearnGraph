# LearnGraph 沙箱控制面拆分（sandboxd）详细修改方案

> 文档状态：设计方案，尚未实施  
> 代码审计基线：`397811308a00` 及工作区当前未提交修改  
> 审计日期：2026-08-14  
> 配套执行清单：[`sandboxd-migration-todo.md`](./sandboxd-migration-todo.md)

## 1. 摘要

本方案把 LearnGraph 从“FastAPI 业务进程直接控制 Docker、宿主路径和沙箱生命周期”迁移为“FastAPI 仅消费版本化 Sandbox API，由独立 `sandboxd` 独占 Docker 控制权”。

目标形态：

```text
Browser
   │
   ▼
LearnGraph app / FastAPI
   │  SandboxBackendPort
   ▼
SandboxdBackend（HTTP client）
   │  internal control network / authenticated API
   ▼
sandboxd
   ├─ protocol / ownership / idempotency / quota / audit
   ├─ workspace volume + file streaming
   ├─ lifecycle + reconciliation
   ├─ bootstrap + immutable runtime digest
   └─ DockerRuntimeBackend
          │
          ▼
      Docker Engine
```

这不是简单地“把 `DockerSandboxBackend` 文件搬到另一个目录”。真实代码中还存在以下必须一起处理的耦合：

1. `SandboxTaskService` 和 `SandboxAgentWorkspaceService` 在构造函数内自行创建当前 Docker backend；
2. 清理调度器、MCP stdio、组件渲染器、网页抓取池等存在旁路 Docker 调用；
3. Bootstrap、镜像 pull/build/digest/smoke/persist 全部在 FastAPI 进程内；
4. `SandboxCreateSpec.workspace_path` 是宿主路径，业务服务负责创建和删除宿主目录；
5. 数据库虽然已有 `backend_id`，但恢复和清理没有按该字段路由；
6. 现有 `backend_session_ref` 同时承担“Docker container id”和“是否有热运行时”的语义，不能直接改存 sandboxd 的稳定 sandbox id；
7. 文件路径中存在宿主直读旁路，无法直接迁到远程或 named-volume sandboxd；
8. 基础 Compose 没有 Docker socket，Linux override 才给 `app` 挂 socket 和同路径数据目录。

因此必须采用“先抽象路由与持久化语义，再引入 daemon，再迁移文件/Bootstrap/旁路调用，最后移除 app Docker 权限”的分阶段方式。禁止直接把全局 `sandbox_backend` 从 `docker` 切成 `sandboxd`。

---

## 2. 审计范围与事实来源

本方案核对了以下真实代码和部署入口：

- `backend/app/providers/ports/sandbox.py`
- `backend/app/providers/remote/sandbox.py`
- `backend/app/services/sandbox.py`
- `backend/app/services/sandbox_bootstrap.py`
- `backend/app/services/sandbox_runtime.py`
- `backend/app/core/config.py`
- `backend/app/core/scheduler.py`
- `backend/app/domain/models.py`
- `backend/app/domain/extension_models.py`
- `backend/app/providers/sandbox_fetch.py`
- `backend/app/providers/sandbox_fetch_pool.py`
- `backend/app/providers/remote/mcp_stdio.py`
- `backend/app/services/component_renderer.py`
- `backend/app/services/document_learning.py`
- `backend/app/services/sandbox_network_policy.py`
- `backend/app/services/sandbox_egress_proxy.py`
- `docker-compose.yml`
- `docker-compose.sandbox.yml`
- `Dockerfile`
- `backend/sandbox/Dockerfile`
- `docker/entrypoint.sh`
- `scripts/docker-update.sh`
- `.github/workflows/ci.yml`
- `.github/workflows/docker.yml`
- `backend/tests/**`
- 根 `.gitignore` 与 `backend/.gitignore`

本文把“已验证代码事实”“目标设计”和“尚需实机验证的假设”分开描述，不能把设计目标当成现状。

---

## 3. 当前真实状态

### 3.1 已有 Port，但还不是可远程化的完整合同

`backend/app/providers/ports/sandbox.py` 已定义同步 `SandboxBackendPort`，包含：

- `probe()` / `host_capacity()`；
- `create()` / `resume()`；
- `write()` / `write_agent_file()` / `delete_agent_file()`；
- `exec_fixed()` / `exec_agent()`；
- `read()` / `list_files()`；
- `stop()` / `delete()`。

现有 DTO：

- `SandboxCapabilitySnapshot`；
- `SandboxCreateSpec`；
- `SandboxSessionHandle`；
- `SandboxExecResult`；
- `SandboxWorkspaceFile`。

但当前合同仍携带基础设施细节：

- `SandboxCreateSpec.image_ref` 由 LearnGraph 解析；
- `SandboxCreateSpec.workspace_path` 是可由 Docker daemon 解析的宿主绝对路径；
- `egress` 是无 schema 的 `dict[str, Any]`；
- 没有 protocol/version/capability negotiation；
- 没有 request id、幂等键、daemon ownership、租约/取消确认；
- `host_capacity()` 只返回 CPU/内存二元组；
- `SandboxExecResult` 没有结构化 usage、daemon error code、execution id；
- 当前接口为同步调用，适合先用同步 `httpx.Client` 适配，避免一次性重写业务服务为 async。

结论：Port 可保留为业务边界，但需要增量演进，不能只“照原样套 HTTP”。

### 3.2 Docker 控制权当前在 FastAPI 进程

`backend/app/providers/remote/sandbox.py:275` 的 `DockerSandboxBackend` 直接使用 docker-py：

- `_client()` 调用 `docker.from_env()`；
- `create()` 创建并启动容器；
- 使用 bind mount 把 `spec.workspace_path` 挂到 `/workspace`；
- 默认 `network_mode="none"`；
- 有有效 egress envelope 时加入内部 egress 网络并注入代理；
- 以 `65532:65532` 运行；
- `read_only=True`、`cap_drop=["ALL"]`、`no-new-privileges`、seccomp；
- 设置 memory/cpu/pids/tmpfs/shm/resource limits；
- 文件传输通过 Docker archive API；
- `exec_agent()` 在宿主侧做 workspace snapshot、配额遍历和未授权删除恢复；
- 输出超限或超时时会终止整个容器，调用方随后把运行时驱逐。

这些安全语义在迁移后必须由 sandboxd 等价或更严格地保留。

### 3.3 真实业务服务不是名为 `SandboxService` 的单一类

`backend/app/services/sandbox.py` 中真实主服务是：

- `SandboxTaskService`（固定 runner 文件任务）；
- `SandboxAgentWorkspaceService`（Agent session/file/command/tool bridge）。

两者构造函数都直接调用 `backend_for_settings(settings)`，没有构造注入或统一 manager。

此外，`backend/app/services/document_learning.py:601` 导入并实例化不存在的 `SandboxService`。仓库中没有该类或兼容别名。这是与 sandboxd 迁移无关、但会阻断 `.doc` 隔离解析路径的现有缺陷，应在第一阶段修为 `SandboxTaskService` 或显式的 legacy-doc service。

### 3.4 数据模型已部分为多后端准备，但恢复路由未完成

`SandboxSession` 已有：

- `backend_id`，默认 `docker`；
- `backend_session_ref`，当前保存 raw Docker container id；
- `runtime_kind`、`lifecycle_state`、`status`；
- `workspace_relative_path`；
- TTL、cleanup、lease、heartbeat、active command 等字段。

问题：

1. `SandboxAgentWorkspaceService._runtime_backend()` 根据**当前 settings**创建 backend，没有按 `session.backend_id` 选择；
2. scheduler 删除容器时同样调用当前 `backend_for_settings()`；
3. 切换全局 backend 后，旧数据库中的 Docker container id 可能被错误交给 sandboxd；
4. `backend_session_ref` 是否为空被用来推断 warm/cold，不能直接替换为长期存在的 sandboxd workspace id；
5. `MCPRunnerSession` 只有 `backend_ref`，没有 `backend_id`，迁移后无法正确清理混合后端资源。

### 3.5 Workspace 仍是宿主目录，不是后端中立资源

`backend/app/services/sandbox.py` 会：

- 在 `sandbox_workspace_root` 下创建 session 目录；
- 把该宿主绝对路径写入 `SandboxCreateSpec.workspace_path`；
- 清理时直接 `rmtree`；
- 通过 `SessionWorkspaceService` 管理数据库/对象存储中的逻辑文件树，并向运行时做双写；
- `_read_workspace_bytes_from_host()` 会绕过 archive 限制，直接从 bind mount 读取大媒体文件。

目标为 named volume + File API 后：

- `workspace_relative_path` 只能保留为 LearnGraph 逻辑工作区标识，不能再解释为 daemon 宿主路径；
- `_read_workspace_bytes_from_host()` 必须替换为有上限、可流式的 sandboxd 文件读取；
- cleanup 必须调用 backend 删除远端 workspace，不能由 app 删除宿主目录；
- app 的逻辑文件区仍可保留，作为用户文件的持久化真源；sandboxd volume 是执行工作区，不应成为唯一业务数据真源。

### 3.6 Bootstrap 当前也是 FastAPI 的 Docker 职责

`backend/app/services/sandbox_bootstrap.py` 当前负责：

- prebuilt/build/auto 模式；
- pull 或本地 build；
- tag 解析为匹配 repository 的 `RepoDigest`；
- code/browser 两类 hardened smoke；
- 写 `sandbox-runtime.json`；
- 进程内 singleton + thread job。

`backend_for_settings()` 固定返回 `DockerSandboxBackend`，配置 validator 只允许 `docker`。

目标形态中 pull/build/digest/smoke/runtime store 必须归 sandboxd；LearnGraph 只发起 Bootstrap job、展示进度和读取结果。

### 3.7 旁路调用必须纳入迁移

已验证的直接或间接 Docker 旁路包括：

| 路径 | 现状 | 必须修改 |
| --- | --- | --- |
| `backend/app/core/scheduler.py` | session cleanup 使用当前 backend；MCP cleanup 直接 new Docker backend | 按持久化 backend id 路由 |
| `backend/app/services/component_renderer.py` | 两次直接 new `DockerSandboxBackend` | 注入 manager，独立 session/workspace |
| `backend/app/providers/remote/mcp_stdio.py` | `_backend()` 固定 Docker；持久化无 backend id | 使用 registry，补数据字段 |
| `backend/app/providers/sandbox_fetch.py` | 通过 helper 取当前 backend | 使用 registry；warm pool key 包含 backend id/protocol |
| `backend/app/services/files.py` | readiness 调当前 factory | 使用 manager capability |
| `backend/app/services/sandbox.py` | 多处当前 factory + 宿主目录 | 按 session backend 路由 |
| `backend/app/services/sandbox_bootstrap.py` | 直接 Docker bootstrap | 改为 sandboxd job client |

特别风险：组件渲染和 MCP stdio 当前把整个 `sandbox_workspace_root` 作为 bind source，不是独立会话子目录。迁移时必须顺便消除该共享根挂载。

### 3.8 Compose 现状需要准确区分

- `docker-compose.yml` 的 `app` 只挂 `learngraph-data:/data`，不挂 Docker socket；
- `docker-compose.sandbox.yml` 明确是 Linux-only override；
- override 给 `app` 挂 `/var/run/docker.sock`；
- override 还把 `${LEARNGRAPH_DATA_DIR}` 以相同宿主/容器绝对路径 bind，满足 dockerd 对 `workspace_path` 的解析；
- 因此基础 Compose 即使默认 `sandbox_enabled=true`，也无法直接使用当前 Docker backend；
- “Linux-only”是当前 same-path Compose override 的限制，不是沙箱产品能力本身；源码模式在 Linux、Windows Docker Desktop/WSL2、macOS Docker Desktop 上由 `docker.from_env()` 适配。

### 3.9 当前测试和 ignore 规则存在冲突

- 后端使用 pytest；CI 命令是 `cd backend && uv run --locked --extra test pytest tests -q`；
- 已有 `backend/tests/api|unit|security|memory`；
- 根 `.gitignore` 明确反忽略 `backend/tests/**`；
- 但 `backend/.gitignore` 最后一行 `/tests/` 会再次忽略 `backend/tests` 下的新文件；
- 已 tracked 测试仍可修改，新文件可能静默不出现在 Git 状态；
- 当前项目约束是不强制加入被忽略的测试文件，禁止 `git add -f`。

因此本文只“指导测试文件”，不创建测试文件；实施者必须先完成配套 TODO 的测试策略门禁，再决定是仅本地临时测试还是调整 ignore 规则。任何情况下都不得用 `git add -f` 绕过规则。

---

## 4. 对原建议的保留、修正与补充

### 4.1 保留的核心判断

以下方向正确并作为目标：

1. `Backend → Docker` 改为 `Backend → sandboxd → Docker`；
2. Docker socket 仅授予 sandboxd；
3. `Host Path bind` 改为 daemon 管理的 volume + File API；
4. Docker、镜像、seccomp、网络和宿主平台差异下沉到 runtime adapter；
5. LearnGraph 只持久化 opaque backend resource ref，不识别 container id/volume name；
6. 将配额、生命周期、审计和 reconcile 集中在 sandboxd。

### 4.2 必须修正的过度简化

1. **已有 Port 不等于无需重设合同。** `workspace_path`、`image_ref`、无 schema egress dict 都需要演进。
2. **不能只搬 `DockerSandboxBackend`。** Bootstrap、scheduler、renderer、MCP、fetch pool 同样持有 Docker 语义。
3. **Linux/Windows 差异不会消失。** 差异被限制在 sandboxd 的启动和 Docker adapter；Windows 全 Compose 是否可靠必须实机验收，不能仅凭架构图宣布支持。
4. **named volume 不是自动跨主机。** 单节点 Docker named volume 仍是节点本地资源；远程/multi-node 需要调度和文件传输，不能把 volume 当共享存储。
5. **File API 需要流式、配额、路径和 ownership 设计。** 不能使用无上限 JSON base64。
6. **sandboxd 有 Docker socket仍是高权限控制面。** 安全收益来自更小代码面、不可表达危险 Docker 参数、私有网络、认证和策略校验，而不是 socket 本身变安全。
7. **旧 session 不能自动切换。** raw Docker container id 必须按原 `backend_id` 处理并 drain，不能被新 backend 解释。

---

## 5. 目标与非目标

### 5.1 目标

- app 镜像和容器不包含 Docker SDK 运行依赖、不挂 Docker socket；
- app 不读取或拼装宿主 Docker 路径、container id、volume name；
- sandboxd 是唯一 Docker API client；
- sandboxd API 私有、认证、版本化、幂等、有限表达能力；
- 每个 sandbox 使用独立 named volume；
- 文件写入/读取/列举/删除通过有上限的流式 File API；
- 新旧 backend 可以在数据库中并存并正确 drain；
- Bootstrap 和 runtime digest 真源迁移到 sandboxd；
- 现有 hardened runtime、egress 和 Agent 删除授权语义不回退；
- source mode、Linux Compose mode 使用同一 API 合同；
- 为未来远程 worker/runtime adapter 留接口，但不在首个版本实现多节点调度。

### 5.2 非目标

首个 sandboxd 版本不实现：

- Kubernetes 调度；
- gVisor/Firecracker；
- 多节点高可用控制平面；
- 实时 WebSocket 终端；
- 任意 `docker run` 或 Docker API 代理；
- 将业务对象存储搬入 sandboxd；
- 自动迁移所有旧 bind-mounted workspace 为 named volume；
- Windows 全 Compose 的未经验证承诺。

---

## 6. 不可回退的安全与行为不变量

迁移每一阶段都必须满足：

1. **Fail closed**：sandboxd 不可用、协议不兼容、能力缺失时，不得回退到宿主执行，也不得静默回退到 app Docker。
2. **镜像不可变**：执行镜像必须为 `@sha256:` digest；tag 只能作为 Bootstrap source。
3. **默认离线**：无有效 policy digest 时 runtime 为 `network_mode=none`。
4. **审批出网**：有 policy 时只加入内部 egress 网络；代理继续重解析并拒绝私网/环回/metadata/DNS rebinding。
5. **进程权限**：runner 使用 UID/GID 65532、只读 rootfs、drop ALL、NNP、seccomp、CPU/memory/pids/tmpfs/shm 限制。
6. **无 shell 边界**：Agent 命令仍为 argv；daemon 再做一次路径、参数、cwd 和可执行文件校验。
7. **文件 containment**：禁止绝对路径、`..`、NUL、symlink escape、hardlink/device/FIFO archive entries。
8. **配额**：写前和执行后都检查 bytes/files/directories；超限必须稳定失败并可回收。
9. **输出上限**：stdout/stderr 超限或超时时必须终止执行进程树；若无法可靠只杀 exec，则驱逐整个 runtime。
10. **删除授权**：`exec_agent()` 的未授权删除 snapshot/restore 语义必须保留或用更强的 overlay/diff 机制替代。
11. **租户归属**：sandbox id、文件和 exec 操作必须校验 canonical `(deployment_id, workspace_id, session_id)` ownership；每个后续请求都必须携带同一 scope，不能仅凭猜到 id 访问。
12. **数据面隔离**：runner 不得访问 sandboxd 控制面，也不得与其他 sandbox 共享可互访的 bridge；审批出网使用每 sandbox 独立 internal network（或有等价 L3 隔离证明的实现），只允许该 runner 到 egress-proxy。
13. **审计脱敏**：不记录原始 secret、完整 credential envelope、未截断 stdout/stderr 或 bearer token。

---

## 7. 目标组件设计

### 7.1 LearnGraph app 侧

新增：

```text
backend/app/providers/ports/sandbox.py        # 演进后的业务 Port/DTO
backend/app/providers/sandbox_registry.py     # BackendRegistry + BackendManager
backend/app/providers/remote/sandboxd.py      # SandboxdClient + SandboxdBackend
backend/app/providers/remote/docker_sandbox.py# 可选：旧 Docker backend 过渡期位置/别名
```

建议职责：

- `SandboxBackendRegistry`
  - 注册 `docker`、`sandboxd` 等 provider；
  - `default(runtime_kind)` 仅供创建新 session；
  - `for_backend_id(backend_id, runtime_kind)` 仅供恢复/停止/删除已有 session；
  - 未知 backend id 直接报稳定错误；
  - registry 不读取 ORM，不做业务授权。

- `SandboxManager`
  - 组合 registry、settings 和协议能力；
  - 创建新 sandbox 时绑定 backend id；
  - 根据持久化 session 路由；
  - 统一错误映射、request id、metrics；
  - 不拼 Docker 参数。

- `SandboxdClient`
  - 使用共享同步 `httpx.Client` 与连接池；
  - 请求超时分 connect/read/write/pool；
  - 自动添加 service auth、request id、idempotency key，以及 canonical deployment/workspace/session scope；
  - mutating request 使用 `scope + operation + key` 作用域、canonical payload hash、in-progress 状态和明确 retention，支持断连/daemon crash 后重放；
  - 严格解析 error envelope；
  - 限制响应大小，流式文件传输；
  - close 由应用 lifespan 管理。

- `SandboxdBackend`
  - 完整实现 `SandboxBackendPort`；
  - 把业务 DTO 映射为协议 DTO；
  - 只发送 runtime kind、资源限制、logical workspace key 和 egress policy ref；
  - 不发送宿主路径、Docker network mode、capability 参数或 privileged flags。

### 7.2 sandboxd 侧

建议作为同仓库独立 Python package 和独立镜像：

```text
sandboxd/
├─ pyproject.toml
├─ Dockerfile
└─ sandboxd/
   ├─ main.py
   ├─ config.py
   ├─ api.py
   ├─ auth.py
   ├─ protocol.py
   ├─ controller.py
   ├─ store.py
   ├─ reconciliation.py
   ├─ quota.py
   ├─ paths.py
   ├─ audit.py
   ├─ bootstrap.py
   └─ runtime/
      ├─ port.py
      └─ docker.py
```

首版不依赖 LearnGraph 主数据库。sandboxd 使用自己的最小 SQLite state store + Docker labels：

- state store 保存 sandbox id、owner scope hash、volume ref、runtime ref、runtime kind、image digest、policy digest、state、TTL、request id；
- Docker labels 用于重启 reconciliation 和孤儿回收；
- 文件字节存于每个 sandbox 的 named volume；
- 主数据库仍保存业务 session、审计、命令结果和 opaque sandbox ref；
- sandboxd state volume 和 workspace volumes 必须纳入升级/备份策略。

### 7.3 Docker runtime adapter

从现有 `DockerSandboxBackend` 迁移/重构，保留：

- image inspect + ABI labels；
- immutable digest；
- hardened container create；
- exec stream 与超时/输出截断；
- archive 安全；
- quota；
- snapshot/restore；
- stop/delete/reconcile；
- egress network attach。

调整：

- 输入从业务 `SandboxCreateSpec` 改为 daemon 内部 `RuntimeCreateSpec`；
- workspace 使用 named volume，而非 app 传来的 host path；
- volume name 仅在 daemon 内部生成，需 deployment prefix + random id，不直接使用用户输入；
- 所有 Docker objects 带固定 label：deployment id、sandbox id、managed=true、runtime kind、policy revision、created at；
- 只允许预定义安全配置，API payload 不可覆盖 `privileged`、mount source、network mode、cap_add、device、pid/ipc namespace。

---

## 8. Port 与持久化语义演进

### 8.1 创建规格

过渡期建议把 `SandboxCreateSpec` 改为后端中立字段：

```python
SandboxCreateSpec(
    session_id: str,
    workspace_key: str,
    runtime_kind: str,
    memory_bytes: int,
    memory_swap_bytes: int,
    cpu_count: float,
    pids_max: int,
    disk_bytes: int,
    egress: SandboxEgressSpec | None,
    legacy_workspace_path: str | None = None,
    legacy_image_ref: str | None = None,
)
```

规则：

- sandboxd backend 拒绝/忽略 `legacy_*` 字段，绝不把它们发给 daemon；
- Docker backend 在 drain 期使用 `legacy_workspace_path` 和 `legacy_image_ref`；
- 新业务代码只构造 `workspace_key`；
- daemon 根据 `runtime_kind` 选择已 bootstrap 的 pinned image；
- drain 完成后删除 `legacy_*`。

`egress` 应改为冻结 dataclass，而非裸 dict，至少包含：

- `policy_digest`；
- `policy_revision`；
- sandboxd 可解析的 logical network policy id；

不要让 app 传任意 Docker network name/proxy environment。sandboxd 根据 deployment/sandbox opaque id 创建并回收每 sandbox internal egress network，代理地址来自 daemon 受信配置，并校验策略 digest。

### 8.2 Session 引用拆分

建议对 `SandboxSession` 增加：

- `backend_resource_ref: str | None`：稳定的 backend sandbox/workspace id；
- `backend_protocol_version: str | None`：创建时协商版本，用于诊断；
- 可选 `backend_generation: int`：reconcile/cold-start CAS。

保留：

- `backend_session_ref`：仅兼容旧 Docker container id，迁移期只读；
- `lifecycle_state`：作为 app 业务状态，不以 ref 是否为空推断全部状态。

语义：

- sandboxd `stop`：停止/删除 runtime container，但保留 sandbox object 和 volume；
- sandboxd `delete`：删除 runtime、volume 和 metadata，幂等；
- app 冷却时保留 `backend_resource_ref`；
- app workspace 过期时才清空 ref；
- Docker legacy session 继续使用 `backend_session_ref`，直到 drain。

`MCPRunnerSession` 增加 `backend_id`，否则 cleanup 无法路由。

### 8.3 数据库迁移

项目使用 `backend/app/core/migrations` 自定义迁移 ledger，而非 Alembic。需要：

1. 增加 additive migration；
2. SQLite 用 inspect 后 `ALTER TABLE ADD COLUMN`；
3. 非 SQLite 也要有等价路径，不能把 migration 仅写成 SQLite no-op；
4. 更新 `CURRENT_SCHEMA_REVISION` / description；
5. 测试旧库升级和 fresh `create_all` 两条路径；
6. 不改写旧 `backend_session_ref`；
7. 新 session 才写 `backend_resource_ref`。

---

## 9. Sandbox API v1 合同

### 9.1 传输与认证

首版：HTTP/1.1 JSON + `application/octet-stream` 文件流，部署在不可从宿主公网访问的内部控制网络。

必须：

- `/v1/...` 路径版本；
- `Authorization: Bearer <service-token>`，token 从文件型 secret 读取；
- `X-Request-Id`；
- mutating request 使用 `Idempotency-Key`；
- request body、header、文件和并发上限；
- 每个 sandbox 后续请求携带 canonical deployment/workspace/session scope，daemon 按 `(scope, sandbox_id)` 绑定校验；不能仅在 create 时校验 owner；
- 所有 mutating endpoint（create/stop/resume/delete/file write/delete/exec/cancel/bootstrap）定义幂等 key 作用域、canonical payload hash、in-progress/crash recovery 与 retention；
- access log 脱敏 Authorization；
- URL 只允许 `http://` 内网或配置 TLS 的 `https://`，生产远程模式强制 TLS；
- daemon 不设置 CORS，不接受浏览器直连。

后续远程 worker 可升级 mTLS，但不应阻塞单机首版。

### 9.2 健康与能力

```text
GET /v1/health/live
GET /v1/health/ready
GET /v1/capabilities
GET /v1/capacity
```

`ready` 至少检查：

- state store 可读写；
- Docker ping；
- managed volume helper 可运行；
- runtime digest/ABI 可用；
- 配置的 egress network 存在且为 internal（仅在 egress enabled 时）；
- reconciliation 已完成或处于明确 degraded 状态。

`capabilities` 返回：

- daemon version；
- protocol min/max；
- runner ABI min/max；
- runtime kinds；
- feature names（fixed runner、agent argv、file streaming、delete authorization、egress policy、resource usage 等）；
- 单次文件/输出/argv/路径限制。

### 9.3 生命周期

```text
POST   /v1/sandboxes
GET    /v1/sandboxes/{sandbox_id}
POST   /v1/sandboxes/{sandbox_id}/resume
POST   /v1/sandboxes/{sandbox_id}/stop
DELETE /v1/sandboxes/{sandbox_id}
```

创建请求包含：

- protocol version；
- logical `session_id` 和 `workspace_key`；
- owner scope（deployment/workspace/session 的不可歧义表示）；
- runtime kind；
- resource limits；
- egress policy reference；
- TTL；
- idempotency key。

响应只返回 opaque `sandbox_id`、state、effective limits、runtime digest/ABI、expires at；不返回 container id、volume name、宿主路径或 Docker inspect 内容。

### 9.4 文件 API

推荐：

```text
PUT    /v1/sandboxes/{id}/files?path=<percent-encoded-relative-path>
GET    /v1/sandboxes/{id}/files?path=<percent-encoded-relative-path>
DELETE /v1/sandboxes/{id}/files?path=<percent-encoded-relative-path>
GET    /v1/sandboxes/{id}/file-index?prefix=<relative-prefix>&limit=<n>&cursor=<opaque>
```

要求：

- 写/读 body 使用流，不使用 JSON base64；
- 必须有 Content-Length 或受控 chunked 上限；
- 服务端先规范化相对 POSIX 路径；
- 目标写入采用 temp + fsync/rename 或 daemon 等价原子策略；
- 文件 index 分页，禁止一次打包整个 workspace；
- read 支持最大字节上限，超限返回稳定 413；
- 对 cold sandbox，用短命 hardened helper container 挂 named volume；
- helper 镜像必须是固定 digest/ABI，使用 UID/GID 65532、read-only rootfs、network none、drop caps、NNP、seccomp和资源限制，只挂目标 volume；
- daemon 对每 sandbox 的 file/write/delete/exec mutation 串行化或使用 generation/CAS，避免并发写/执行/删除竞态；
- 路径操作不能只靠“先 resolve 再 open”：Linux 优先使用 `openat2`/dirfd + `O_NOFOLLOW` 等等价的抗 TOCTOU 策略，并在 helper 内完成；
- 不把 dynamic volume 路径暴露给 sandboxd 容器宿主文件系统。

### 9.5 执行 API

分开表达，避免把固定 runner 和 Agent argv 混成任意 command：

```text
POST /v1/sandboxes/{id}/executions/fixed
POST /v1/sandboxes/{id}/executions/agent
GET  /v1/executions/{execution_id}
POST /v1/executions/{execution_id}/cancel
```

固定任务请求最好使用 `task_type + input_path + output_path`，由 daemon 组装固定 runner argv；过渡期可接收 argv，但必须严格匹配 runner 前缀和 task allowlist。

Agent 请求包含 argv、cwd、timeout、output limit、允许删除的相对路径。daemon 必须再次执行：

- argv count/length；
- 可执行文件 allowlist；
- cwd/path containment；
- shell/meta 字符策略（按现有服务语义）；
- snapshot/quota；
- timeout/output kill；
- effective resource usage。

首版可保持阻塞 HTTP 响应以匹配现有同步 Port；同时生成 `execution_id`，为后续取消/轮询留接口。客户端断连不能自动等同于成功取消；必须有明确 cancel 或 daemon deadline。

### 9.6 Bootstrap API

```text
POST /v1/bootstrap/jobs
GET  /v1/bootstrap/jobs/{job_id}
GET  /v1/runtimes
```

sandboxd 负责：

- source/mode 校验；
- pull/build；
- RepoDigest；
- ABI labels；
- code/browser smoke；
- 原子持久化 runtime record；
- 单飞/幂等和进度；
- 日志脱敏。

LearnGraph 原有 HTTP API 可保持不变，由 app 代理到 sandboxd，减少前端改动。Bootstrap/build 是比普通 runtime 更高权限的管理面：使用独立 admin credential/scope，只接受受信 prebuilt source 或固定仓库内构建配方；禁止 API 上传任意 build context、Dockerfile、build args 或宿主路径。

### 9.7 错误合同

统一 envelope：

```json
{
  "error": {
    "code": "sandbox_not_found",
    "message": "safe operator-facing message",
    "retryable": false,
    "request_id": "...",
    "details": {}
  }
}
```

至少定义：

- `unauthorized` / `owner_mismatch`；
- `protocol_incompatible` / `capability_missing` / `runner_abi_mismatch`；
- `sandbox_not_found` / `sandbox_expired` / `invalid_state`；
- `invalid_path` / `file_too_large` / `workspace_quota_exceeded`；
- `command_rejected` / `destructive_authorization_required`；
- `execution_timeout` / `output_limit_exceeded` / `execution_failed`；
- `capacity_exceeded`；
- `runtime_unavailable` / `docker_unavailable`；
- `idempotency_conflict`。

`SandboxdBackend` 映射到现有稳定 Python exceptions；未知 5xx 一律映射为 backend unavailable/error，不泄露 daemon traceback。

### 9.8 幂等、并发和 crash 重放语义

不能只保证 create/delete 幂等。每个 mutating operation 都要冻结：

- key 作用域：`(deployment scope, sandbox_id或bootstrap scope, operation, idempotency_key)`；
- canonical payload hash：同 key 不同 payload 必须 conflict；
- `in_progress/succeeded/failed/retryable` 状态；
- client 断连、HTTP timeout、daemon crash、Docker API timeout 后的查询/重放行为；
- retention 和过期后的行为；
- exec 重放不得重复执行未知状态命令：必须返回同 execution id、查询状态或明确 `indeterminate`，不能悄悄再次运行 argv；
- cancel/stop/resume/file mutation/bootstrap 同样需要幂等测试。

### 9.9 文件操作并发与一致性合同

当前代码存在双写真源不一致：`write_file` 先写 runtime 再写 logical store，logical store 写失败会留下 runtime 新内容；`delete_file` 先删 logical store，再 best-effort 删除 runtime；read 又可能优先 logical store。迁移前必须冻结新合同：

- 业务 logical store 仍是持久真源，runtime volume 是带 generation 的执行副本；
- 每项 mutation 分配 operation id/generation，sandboxd 返回 effective content hash；
- app 通过 outbox/sync-state 记录 `pending/applied/repair_required`，不能把半成功静默当成功；
- exec 后对变更文件按 generation/hash 同步，旧 logical 内容不得遮蔽新 runtime 输出；
- delete tombstone 阻止旧 runtime 文件再次“复活”；
- repair/reconcile 可以从真源重放输入，并把明确产物发布回 object storage；
- file mutation 与 exec 使用 per-sandbox serialization或 generation CAS；
- 通过 fault injection 覆盖 app commit失败、daemon crash、断连和并发写/删/执行。

---

## 10. 文件与数据流

### 10.1 业务真源与执行副本

继续保留 `SessionWorkspaceService` + object storage/DB 作为用户业务文件真源；sandboxd volume 是短期执行副本。

输入：

```text
Object Storage / logical session file
  → app 流式读取（受 max_upload/max agent file 限制）
  → sandboxd File API
  → sandbox named volume
```

输出：

```text
sandbox named volume
  → sandboxd File API（受上限）
  → app publish_path / object storage
  → 用户可见 artifact/session file
```

### 10.2 必须删除的宿主旁路

`sandbox.py::_read_workspace_bytes_from_host()` 在 sandboxd 模式必须禁用并最终删除。大媒体文件改为：

- 优先使用业务对象存储原文件；
- 若文件只在 sandbox volume，使用 streaming GET；
- app 和 daemon 都执行 size limit；
- 不将整个文件读入 daemon 内存；
- 对 ASR provider 如仍要求 bytes，app 在自身已有上限内聚合，不能通过宿主路径绕过。

### 10.3 冷却与持久化

- runtime idle TTL 到期：停止/删除 container，保留 named volume；
- workspace TTL 到期：删除 container + volume + daemon metadata；
- app 的逻辑文件仍按业务 retention 管理；
- sandboxd 重启后从 state store + Docker labels reconcile；
- volume 删除失败进入 `cleanup_blocked` 并重试，不得先把主库标记 cleaned。

---

## 11. Bootstrap 与 Runner 供应链

### 11.1 Runtime record

sandboxd runtime store 建议至少保存：

- schema version；
- runtime kind；
- immutable image digest；
- runner ABI；
- architecture/os；
- policy revision；
- seccomp revision；
- environment manifest schema；
- bootstrap source；
- smoke timestamp/result；
- optional SBOM/signature verification result。

### 11.2 Runner 镜像 labels

给 `backend/sandbox/Dockerfile` 或后续独立 runner 构建增加 OCI/产品 labels：

- `org.opencontainers.image.revision`；
- `com.learngraph.runner-abi`；
- `com.learngraph.runtime-kind`；
- `com.learngraph.policy-revision`；
- `com.learngraph.seccomp-revision`；
- `com.learngraph.environment-manifest-schema`；
- 现有 legacy doc/browser capability labels继续保留。

拉取后必须校验 labels 和架构，再允许成为 active runtime。

### 11.3 依赖移动

`backend/pyproject.toml` 当前直接依赖 `docker>=7.1,<8`。完成切换后：

- app package 移除 docker 运行依赖；
- `sandboxd/pyproject.toml` 独占 docker-py；
- app 保留已有 `httpx`，用于 client；
- 过渡期若同一 app 包仍需 legacy Docker backend，可暂时保留 docker extra，直到 drain 支持结束。

---

## 12. 配置设计

### 12.1 LearnGraph app 新配置

建议：

```text
LEARNGRAPH_SANDBOX_BACKEND=sandboxd|docker
LEARNGRAPH_SANDBOXD_URL=http://sandboxd:8090
LEARNGRAPH_SANDBOXD_TOKEN_FILE=/run/secrets/sandboxd-token
LEARNGRAPH_SANDBOXD_CONNECT_TIMEOUT_SECONDS=2
LEARNGRAPH_SANDBOXD_REQUEST_TIMEOUT_SECONDS=190
LEARNGRAPH_SANDBOXD_FILE_TIMEOUT_SECONDS=300
LEARNGRAPH_SANDBOXD_PROTOCOL_MIN=1.0
LEARNGRAPH_SANDBOXD_PROTOCOL_MAX=1.x
LEARNGRAPH_SANDBOXD_TLS_CA_FILE=
```

规则：

- validator 接受 `docker|sandboxd`；
- `sandboxd` 缺 URL/token/protocol 时配置校验失败或 readiness fail-closed；
- URL 禁止用户态页面随意设置；
- secret 不出现在 settings API、日志或 audit details；
- 全局默认只决定**新 session**，旧 session 仍按持久化 `backend_id` 路由。

### 12.2 sandboxd 配置

建议：

```text
SANDBOXD_LISTEN_HOST=0.0.0.0
SANDBOXD_PORT=8090
SANDBOXD_TOKEN_FILE=/run/secrets/sandboxd-token
SANDBOXD_STATE_PATH=/var/lib/sandboxd/state.db
SANDBOXD_DEPLOYMENT_ID=<stable-id>
SANDBOXD_DOCKER_HOST=<optional>
SANDBOXD_EGRESS_NETWORK=<resolved-stack-network>
SANDBOXD_EGRESS_PROXY_URL=http://egress-proxy:8888
SANDBOXD_MAX_REQUEST_BYTES=...
SANDBOXD_MAX_FILE_BYTES=...
SANDBOXD_MAX_ACTIVE=...
SANDBOXD_RECONCILE_ON_START=true
```

禁止通过 API 覆盖 Docker host、host mount、privileged、network host 或任意 image。

---

## 13. Compose 与运行模式

### 13.1 Linux Compose 目标

- 新增 `sandboxd` service；
- 仅 `sandboxd` 挂 `/var/run/docker.sock`；
- `app` 删除 socket、`DOCKER_GID`、same-path bind；
- 新增 `sandbox-control` internal network；
- sandboxd 不发布宿主端口；
- app 通过 `http://sandboxd:8090` 调用；
- sandboxd state 使用专用 named volume；
- sandbox workspace 使用动态 named volumes；
- sandboxd 加 healthcheck；
- app depends_on sandboxd healthy（若 Compose 默认总启用该服务）；
- sandboxd 只接 `sandbox-control`，不加入 runner egress 数据面；
- egress-proxy 可接外网 default network，并由 daemon 为每个获批 sandbox 创建独立 internal egress network，只连接该 runner 与 proxy；禁止所有 runner 共用可互访 bridge；
- 若实现选择宿主防火墙/L3 ACL 代替独立 network，必须有等价隔离证明与 runner→sandboxd、runner→peer 负向测试。

sandboxd 容器本身：

- `read_only: true`；
- `cap_drop: [ALL]`；
- `security_opt: [no-new-privileges:true]`；
- `tmpfs`；
- 非 privileged；
- 仅因 Docker socket GID 获得最小必要补充组；
- 必须实测 entrypoint 降权后补充组没有被 `setpriv --init-groups` 丢弃。

注意：`:ro` 挂 Docker socket不能限制 Docker API 权限，不应把它写成安全控制。

### 13.2 源码开发模式

建议 `npm run dev` 同时管理：

1. sandboxd 本地进程；
2. FastAPI app；
3. preview；
4. 可选 egress proxy。

Windows/macOS/Linux 都由 sandboxd 自己使用 Docker context/Desktop。app 永远只连接本地 loopback sandboxd。开发 token 自动生成到本地数据目录，权限收紧且不提交仓库。

### 13.3 Windows 全 Compose

不得在未做真实 Docker Desktop 验收前宣布支持。候选连接方式由 sandboxd adapter/启动包装解决：

- Linux-container VM 内 socket bridge；
- Docker context/DOCKER_HOST；
- Windows named pipe（仅 sandboxd 原生进程）。

首个 release 的支持矩阵应明确：

- Linux source：支持；
- Linux Compose：支持；
- Windows/macOS source + Docker Desktop：支持目标，需实机 CI/人工验收；
- Windows/macOS full Compose：实验性或暂不承诺，直到通过 socket/path/permission 验收。

### 13.4 升级脚本

`scripts/docker-update.sh` 必须：

- app 与 sandboxd 版本作为配套单元升级；
- 备份主数据卷、sandboxd state 和需要保留的 workspace volumes；
- 通过 maintenance lock/停写窗口建立一致性点：先阻止新 exec/file mutation/bootstrap/cleanup，再按 deployment labels精确枚举 state与volumes，记录manifest和恢复顺序；不能分别在线打包后假设天然一致；
- 若决定不备份短期 workspace volume，必须先把业务真源/outbox同步完成并清理对应 ref，明确接受执行副本丢失；
- 正确处理 `${LEARNGRAPH_DATA_DIR}` bind 模式，不能硬编码 `/data/learngraph.db` 和 named volume；
- 健康检查同时覆盖 app 和 sandboxd；
- 协议/runner ABI 不兼容时停止升级并回滚；
- 回滚不能让旧 app 误解释新 backend refs。

---

## 14. 分阶段迁移

### Phase 0：冻结合同与修复现有断链

- 为现有 Port 写 contract matrix；
- 修 `document_learning.py` 的不存在类引用；
- 统一 egress/network_policy 真实语义；
- 决定测试文件跟踪策略；
- 不改部署默认值。

退出条件：现有 Docker 路径功能和安全基线可重复验证。

### Phase 1：Registry、依赖注入和 backend-id 路由

- 引入 registry/manager；
- service 支持传入 backend/manager；
- 所有恢复/删除按持久化 backend id；
- MCP session 增加 backend id；
- scheduler、renderer、MCP、fetch 消除直接 new Docker；
- 默认仍为 Docker。

退出条件：配置默认不变，但仓库除 registry provider 外无业务模块直接构造 Docker backend。

### Phase 2：sandboxd 最小控制面 + Docker runtime 搬迁

- 独立 package/process/image；
- auth/version/health/capacity；
- create/resume/stop/delete；
- Docker labels/state/reconcile；
- 保留 bind 或临时 volume只用于 daemon 内部开发，不切生产。

退出条件：本地真实 roundtrip 可通过，app 仍可保持 Docker 默认。

### Phase 3：Named volume + File API

- 每 sandbox 独立 volume；
- 流式 write/read/list/delete；
- cold helper；
- quota/path/archive/symlink 安全；
- app 移除 host read 和 runtime host rmtree；
- 业务 logical workspace 与 runtime副本明确分层。

退出条件：create → write → exec → read → stop/resume → delete 完整 roundtrip 不依赖共享路径。

### Phase 4：Bootstrap、egress 和旁路能力迁移

- Bootstrap job 进入 sandboxd；
- runtime digest/ABI 真源进入 daemon；
- egress policy ref 接线；
- renderer/MCP/fetch 全部走 sandboxd；
- cleanup/reconcile 双层职责稳定。

退出条件：FastAPI 运行路径不再调用 docker-py。

### Phase 5：混合后端灰度与 drain

- 配置接受 `sandboxd`，只影响新 session；
- 旧 Docker session 继续按 `backend_id=docker` 路由；
- 在仍有 app Docker 权限的维护窗口完成自然/强制 drain；
- 禁止把 raw container id 交给 sandboxd；
- 记录残留 session/容器/目录。

退出条件：主库所有 `backend_id=docker` 且未 cleaned 的 active/warm/cold/recovering/cleanup-blocked session 均为零，MCP legacy ref、旧宿主 workspace、旧网络与 orphan container/volume 均为零；不能只统计 active/warm。

### Phase 6：切断 app Docker 权限

- Compose 仅 sandboxd 挂 socket；
- app 移除 same-path data bind要求；
- app package 移除 docker 依赖与 legacy backend（可先保留代码但不打包）；
- 删除/归档 Linux-only override 的旧语义；
- 更新 README、env、升级脚本和运维手册。

退出条件：app 内 Docker ping 失败，所有沙箱能力仍通过 sandboxd 验收。

---

## 15. 混合后端、升级与回滚

### 15.1 绝对禁止的切换方式

禁止：

```text
直接修改 LEARNGRAPH_SANDBOX_BACKEND=sandboxd
→ 所有旧 session 通过当前 factory 恢复
→ sandboxd 收到旧 Docker container id
```

### 15.2 正确路由

- 新 session：registry 的 default backend；
- 已有 session：`session.backend_id`；
- legacy Docker：`backend_session_ref`；
- sandboxd：`backend_resource_ref`；
- unknown backend：fail closed + operator remediation；
- cleanup 失败：保留 ref 和 `cleanup_blocked`，不能伪报成功。

### 15.3 Drain 策略

首选安全策略是“并行注册、禁止新建、等待 TTL、强制清理”，而不是自动 adoption：

1. app 暂时保留 Docker socket；
2. 新 session 创建到 sandboxd；
3. 旧 Docker session 按原 backend 工作或到期；
4. 维护命令列出并删除剩余 legacy session；
5. 验证 Docker labels 下无 orphan；
6. 再移除 app socket。

若未来实现 adoption，必须是显式 admin 操作，并校验 `com.learngraph.sandbox=true`、session label、镜像、安全配置和 mount 根；不得根据用户提供的任意 container id adoption。

### 15.4 回滚

- 在 app socket 已移除后，不允许自动回滚为 Docker backend；
- 回滚旧 app 前必须确认旧 app 能识别数据库中的新字段且不会解释 sandboxd ref；
- sandboxd 与 app protocol 采用 min/max negotiation；
- rolling upgrade顺序建议：先部署向后兼容 sandboxd，再部署 app；回滚相反；
- schema 迁移为 additive，旧字段保留至少一个 release；
- runner ABI 不匹配时 fail closed。

---

## 16. 可观测性与运维

### 16.1 指标

app：

- client request latency/error/timeout；
- backend id、operation、stable error code；
- protocol/ABI mismatch；
- stream bytes；
- session create/resume/cleanup outcome。

sandboxd：

- active/starting/stopped sandboxes；
- allocated CPU/memory；
- volume bytes/files；
- exec duration/timeout/truncation/cancel；
- Docker API latency/error；
- reconcile adopted/orphan/deleted/blocked；
- file API bytes and rejected requests；
- bootstrap duration/result；
- egress policy reject counts。

不得把 workspace id、文件名、argv 或 token 作为高基数 metric label。

### 16.2 日志与审计

- app 和 daemon 都记录相同 request id；
- daemon 日志只记录 sandbox opaque id、operation、status/error code；
- 命令只存 digest/redacted projection；
- 文件路径可存 digest或经过长度限制的安全相对路径；
- auth token、proxy credential、registry credential 永不记录；
- destructive authorization、egress policy digest、cleanup blocked 必须可审计。

### 16.3 健康语义

- `/live` 只表示进程事件循环存活；
- `/ready` 表示可接 sandbox 请求；
- app 的主 `/livez` 不应因 sandboxd 短时异常被 watchdog 误杀；
- sandbox readiness 由现有 sandbox readiness/profile API单独暴露；
- Compose 可以让 app 等待 sandboxd healthy，但应用运行期仍需容忍 daemon重启并返回明确不可用。

---

## 17. 测试方案与测试文件指导

> 当前只给出文件职责和断言指导，不在本次文档任务中创建测试文件。遵守项目现有约束：不要 `git add -f`。先处理根/`backend/.gitignore` 冲突。

### 17.1 测试分层

1. **Unit**：fake transport/runtime，不需要 Docker；
2. **Contract/API**：固定协议 fixture + FastAPI TestClient，不需要真实 daemon；
3. **Integration（opt-in）**：真实 sandboxd + Docker；默认 skip；
4. **Compose smoke**：Linux runner 验证权限、网络、volume、socket边界；
5. **Security regression**：路径、ownership、network、resource、secret redaction；
6. **Upgrade/rollback rehearsal**：旧库、旧 session、daemon/app 版本矩阵。

### 17.2 建议文件（路径是指导，不代表自动入库）

| 建议路径 | 责任 | 核心断言 |
| --- | --- | --- |
| `backend/tests/unit/test_sandbox_backend_registry.py` | backend factory/registry | 默认只用于新建；旧 session 按 backend id；未知 id fail closed |
| `backend/tests/unit/test_sandboxd_client.py` | HTTP client | auth、request id、timeout、坏 JSON、error map、响应上限、脱敏 |
| `backend/tests/unit/test_sandboxd_backend.py` | Port adapter | 全部 Port 方法映射；幂等；ownership；禁止宿主 fallback |
| `backend/tests/unit/test_sandbox_session_routing.py` | ORM/service 路由 | Docker legacy 与 sandboxd 同库并存；cleanup 正确选择 provider |
| `backend/tests/unit/test_sandbox_bootstrap_sandboxd.py` | Bootstrap proxy | 单飞、进度、digest、ABI mismatch、日志脱敏 |
| `backend/tests/api/test_sandboxd_http.py` | LearnGraph 对外 API 回归 | authz、readiness、session/command/file status 与错误码 |
| `backend/tests/contract/test_sandboxd_protocol.py` | 协议 fixture | version negotiation、unknown fields、稳定 error envelope |
| `backend/tests/security/test_sandboxd_security.py` | daemon 输入安全 | path traversal、owner mismatch、argv、secret、请求大小 |
| `backend/tests/integration/test_sandboxd_roundtrip.py` | 真实 Docker | create/write/exec/read/list/stop/resume/delete |
| `backend/tests/integration/test_sandboxd_failure_modes.py` | 故障 | daemon restart、Docker restart、timeout、取消、重复 delete、孤儿 reconcile |
| `sandboxd/tests/unit/test_docker_runtime.py` | daemon Docker adapter | hardened create 参数、labels、volume、kill、cleanup |
| `sandboxd/tests/unit/test_workspace_files.py` | 文件层 | stream limit、atomic write、quota、symlink/hardlink/device 拒绝 |
| `sandboxd/tests/unit/test_reconciliation.py` | reconcile | state/label 差异、孤儿、cleanup_blocked、幂等 |
| `sandboxd/tests/unit/test_auth.py` | service auth | missing/wrong token、constant-time compare、日志不泄密 |

### 17.3 可复用现有模式

- `backend/tests/unit/test_sandbox_fetch_pool.py`：Fake backend + 并发/复用/驱逐；
- `backend/tests/unit/test_sandbox_agent_files.py`：内存 SQLite `StaticPool` + fake backend + `AppError.code`；
- `backend/tests/api/test_sandbox_bootstrap_modes.py`：fake Docker/monkeypatch/bootstrap job；
- `backend/tests/api/test_providers_http.py`：不可达 endpoint、错误不泄密；
- `backend/tests/security/test_agent_egress_policy.py`：临时 policy + fail closed；
- `backend/tests/unit/test_egress_proxy_main.py`：policy refresh/过期；
- `.github/workflows/docker.yml`：Compose config、live/health、egress network smoke。

### 17.4 pytest 规则

若项目允许新测试入库，应在 `backend/pyproject.toml` 注册：

```toml
[tool.pytest.ini_options]
markers = [
  "integration: requires an explicitly configured sandboxd/Docker runtime",
]
```

Integration 用明确环境变量，例如 `LEARNGRAPH_TEST_SANDBOXD_URL`，缺失时 skip。不能让默认 `pytest tests -q` 依赖 Docker。

常用命令：

```bash
cd backend
uv run --locked --extra test pytest tests/unit/test_sandboxd_client.py -q
uv run --locked --extra test pytest tests/unit/test_sandboxd_backend.py -q
uv run --locked --extra test pytest tests -q

# 仅显式集成环境
LEARNGRAPH_TEST_SANDBOXD_URL=http://127.0.0.1:18090 \
uv run --locked --extra test pytest tests/integration -q -m integration
```

sandboxd 独立包使用自己的 locked environment 和 pytest 命令，不能共享开发者主机的未锁定依赖。

### 17.5 测试资源清理

- 每个真实测试使用唯一 deployment/session id；
- 在 `finally` 删除 sandbox 和 volume；
- 测试结束再按 label 扫描，确认无容器/volume 泄漏；
- 禁止连接真实 `learngraph.db`；
- 禁止继承开发者 `.env` secret/runtime image；
- daemon state、policy、audit、workspace 都用临时目录/volume；
- failure test 即使强杀进程也必须由 teardown/reconcile 回收资源。

---

## 18. 关键验收场景

1. app container inspect 不含 Docker socket；app 内 Docker ping 失败；sandboxd Docker ping 成功。
2. app 只能通过内部网络调用 daemon，宿主无 sandboxd 公开端口。
3. create/write/exec/read/list/stop/resume/delete 使用 named volume 完成，不依赖 same-path bind。
4. runner inspect 满足 UID 65532、read-only、drop ALL、NNP、seccomp、memory/cpu/pids/tmpfs/shm、无 socket。
5. 无 policy 时网络完全失败；有 policy 时只通过内部 egress proxy，未批准域名和私网全部拒绝。
6. daemon/app 重启后 sandbox 可 reconcile；重复 create/delete 幂等；孤儿最终清理。
7. Docker legacy 和 sandboxd session 同库存在时，各自恢复/清理到正确 backend。
8. app 不可用时 daemon deadline 仍会终止超时 exec；daemon 不可用时 app 不回退宿主或 Docker。
9. Bootstrap 只激活 RepoDigest + 兼容 runner ABI；不兼容明确拒绝。
10. 路径 traversal、symlink escape、owner mismatch、超大文件、超配额和未授权删除全部稳定失败。
11. app/sandboxd 日志中搜索不到测试 token/credential。
12. 升级脚本能备份、升级、健康验证和回滚 app + sandboxd + state；旧 session 不被误解释。

---

## 19. 主要风险与缓解

| 风险 | 后果 | 缓解 |
| --- | --- | --- |
| 全局开关误路由旧 ref | 删除/访问错误资源 | registry 按持久化 backend id；mixed drain |
| stable sandbox ref 与 warm container ref 混用 | capacity/cleanup 错误 | 增加 `backend_resource_ref`，不复用旧字段语义 |
| File API 大文件内存放大 | OOM/DoS | octet stream、双端上限、chunk、无 base64 |
| daemon 持 socket 被利用 | 宿主接管 | 极小 API、私网认证、不可表达危险参数、审计 |
| UID/GID/volume 写权限 | 运行时不可写 | helper + runner 同 UID/受控 volume；Linux 实机测试 |
| `setpriv --init-groups` 丢 socket GID | sandboxd 无法访问 Docker | entrypoint 实机验收，必要时专用 entrypoint |
| Bootstrap 与 app 版本漂移 | runtime ABI mismatch | capabilities min/max + fail closed |
| runner 共用 egress bridge / 网络名冲突 | sandbox 间横向访问、跨栈串网 | per-sandbox internal network（或等价 L3 ACL），daemon独占命名与回收 |
| daemon state 与 Docker labels 漂移 | orphan/错误清理 | 启动 reconcile、ownership labels、cleanup blocked |
| 当前测试 ignore 冲突 | 新测试未入库/CI缺覆盖 | 先完成测试策略门禁，禁止 `git add -f` |
| 升级脚本遗漏 bind 数据/state | 数据损失 | 识别 compose 模式并演练恢复 |
| Windows full Compose 未验证 | 错误支持承诺 | 明确支持矩阵，先 source mode 实测 |

---

## 20. 完成定义（Definition of Done）

只有同时满足以下条件，才能宣布 sandboxd 迁移完成：

- 代码搜索除 sandboxd package/legacy migration tool 外没有 `docker.from_env()` 或 app 侧 `DockerSandboxBackend()`；
- app 的生产依赖和镜像不再需要 docker-py；
- app Compose 无 socket、无 Docker GID、无 same-path workspace bind；
- 所有新 session 写 `backend_id=sandboxd` 和 stable resource ref；
- legacy Docker session 已 drain 或有明确受支持的回退窗口；
- Bootstrap、renderer、MCP、fetch、cleanup 全部使用统一 manager/API；
- named-volume File API roundtrip、安全回归、故障恢复、egress、资源限制和升级回滚均通过；
- 文档明确支持矩阵、备份范围、故障排查和回滚；
- 没有降低当前安全不变量；
- 测试结果、Compose inspect 和真实 Linux验收证据可复核。

实施顺序和每项验收标准见配套 TODO 文档。

---

## 21. 附录：SandboxBackendPort 合同矩阵（TODO-001）

以 `backend/app/providers/ports/sandbox.py` 为准。行为语义以迁移前 `DockerSandboxBackend` 为基线，迁移后必须等价或更严格。

| 操作 | 现状语义（Docker backend） | 迁移后归属 | 安全不变量 |
| --- | --- | --- | --- |
| `probe()` | enabled + 镜像必须为 sha256 pin + 镜像 labels/能力 | sandboxd `/capabilities`（version/protocol/ABI/runtime kinds） | 未 pin / ABI 不兼容 = unavailable，fail closed |
| `host_capacity()` | Docker Engine NCPU/MemTotal | sandboxd `/capacity` | 不暴露容器内细节；app 只做展示 |
| `create(spec)` | 校验 spec → 创建 hardened container + bind mount + 可选 egress | sandboxd 创建 named volume + container + per-sandbox egress network | UID 65532、read-only、drop ALL、NNP、seccomp、limits；`spec.image_ref`/`workspace_path` 为 legacy，sandboxd 忽略 |
| `resume(session_id, ref)` | 按 container id 恢复 | 按 sandboxd sandbox id 恢复 | 必须按持久化 `backend_id` 路由；未知 ref fail closed |
| `write()` / `write_agent_file()` | tar 写文件（`write` 只读权限 0o444） | File API 流式 PUT | 路径 containment、配额、原子写、per-sandbox 串行化 |
| `delete_agent_file()` | tar 删除 | File API DELETE | 仅 work/ 树、幂等 |
| `exec_fixed()` | 固定 runner argv、timeout、output limit、杀进程树 | sandboxd fixed executions endpoint | 无任意命令；超限/超时终止 |
| `exec_agent()` | argv 校验 + cwd + snapshot + 未授权删除恢复 + quota | sandboxd agent executions endpoint | destructive 授权语义不丢失；daemon 二次校验 |
| `read()` | tar 读（限额） | File API 流式 GET | 双端 size limit；大文件不内存放大 |
| `list_files()` | tar 列举（限额） | File API 分页 index | 分页、opaque cursor |
| `stop()` | 停止容器（保留数据） | sandboxd stop（保留 volume） | 幂等；不删数据 |
| `delete()` | 删容器 + 清理 workspace | sandboxd delete（删 container+volume+state） | 幂等；只有 managed/ownership 证据才操作 |
