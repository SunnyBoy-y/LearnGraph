# 任务 P2-B｜隔离 MCP stdio runner 与 OAuth 生命周期（不可并行）

## 元信息块

```text
并行性   : 不可并行（对 backend/app/providers/local/mcp.py 与安全边界的集中改动；实现依赖 P1-B）
状态     : 已完成 —— 2026-08-03（runner 合约 + Docker stdio runner 容器 + 启动规范注册/审批 + OAuth 授权码/加密持久化/refresh 单飞/撤销 + runner 结构化审计 + 孤儿容器清理 scheduler + OAuth→runner IPC 注入；默认仍 UnavailableStdio，真实 Docker 端到端由镜像重建脚本验证）
主要文件 : backend/app/providers/local/mcp.py、隔离 runner 合约（待建）、OAuth 生命周期（待建）
依赖     : P1-B（实现阶段）—— 设计阶段可先行
口音标注 : 无
```

> 放 02_不可并行，因为集中在 `mcp.py`（与 P2-A 同在安全边界，不与 P2-A 抢文件但都属后端大改队列）。
> 设计阶段可并行推 RFC，实现在 P1-B 后。

## 背景

TODO —— 复制 ROADMAP P2-B 目标：在「禁止 FastAPI 主进程启动任意命令」前提下，让经审核 MCP stdio Server
在独立受限环境运行，并支持 OAuth 凭据生命周期，同时保持默认拒绝语义。

## 实施范围

- [x] 保持 `UnavailableStdioMCPAdapter` 默认拒绝；新增隔离 runner 合约，**禁止 Web/API 进程 `subprocess`**（`app/providers/ports/mcp_runner.py`）。
- [x] runner：最小镜像/命令白名单、non-root、资源配额、只读根 fs、workspace 临时目录、默认禁网、审计（`app/providers/remote/mcp_stdio.py`：`egress=None` 强制 `network_mode="none"`、non-root、cap_drop ALL、参数/结果上限；`mcp_stdio_runner_enabled` 默认关闭）。
- [x] 注册与运行分离；注册记录审核后的启动规范、版本/哈希、权限 envelope（`register_stdio_launch_spec` / `approve_stdio_launch_spec`，未审批恒不可执行）。
- [x] OAuth 授权码流程、动态客户端注册、加密保存、作用域、刷新、撤销、失效回收；token 仅注入隔离 runner（`app/services/mcp_oauth.py`：PKCE S256 + state 常量时间比对、exchange/refresh/revoke、`register_oauth_client`、加密持久化、`runner_only_token` 单飞注入）。
- [x] 受控 IPC 接入现有 MCP port，超时/大小限制/配额/脱敏/健康检查（`StdioIsolatedMCPAdapter.invoke` one-shot JSON-RPC；`MCPRunnerTimeout`/`MCPRunnerResourceExceeded` → `ExtensionInvocation` 结构化审计；`MCPRunnerSession` + `mcp_runner_cleanup_scheduler` 回收孤儿容器）。

## 边界（防冲突）

- 不碰 `components.py`（P2-A）、不碰前端。与 P1-B 的实现阶段后置。

## 验收条件

- [x] 主 API 进程不启动第三方 MCP 命令（`local/mcp.py` 保持 `UnavailableStdioMCPAdapter`；runner 只在隔离容器内执行）。
- [x] 未审核/哈希不匹配/无授权/已撤销的 MCP Server 无法执行（launch-spec 审批流 + `_stdio_launch_spec` 门控）。
- [x] OAuth token 不出现于 API 响应、Agent 工具输入、前端状态、普通审计正文（`runner_only_token` 仅注入容器、revoke/过期 fail-closed）。
- [x] runner 崩溃/超时/资源超限/token 失效返回结构化、可审计、不泄密的失败。
- [x] 不同 workspace 的 MCP 凭据/能力快照/执行记录严格隔离（workspace 级 `MCPRunnerSession`/credential 记录 + 测试 `test_mcp_oauth_flow`/`test_mcp_runner_*` 通过）。