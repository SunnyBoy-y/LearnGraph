# 子代理运行器实现说明

## 位置与结构

- 运行器：`backend/app/services/sandbox_subagent.py`
  - `SubagentSpec`：不可变任务描述（id / workspace / actor / chat / prompt / tools / max_rounds / max_seconds / sandbox_session_id）
  - `SubagentJob`：任务状态（status / rounds / error_class / error_message / result / 时间戳）
  - `SubagentRegistry`：进程内注册表（RLock 保护，上限 64 个 job，满员时淘汰最旧的已完成 job）；`start()` 起守护线程，`get()` / `cancel()` 查询与取消
  - `_run_subagent()`：嵌套循环主体（每轮 provider 调用 + 工具执行 + 结果回填）
- 工具实现：`backend/app/services/sandbox_toolkit.py` → `toolkit_subagent()` / `toolkit_subagent_status()`
- 接线：`backend/app/services/sandbox.py` `execute_agent_tool` 分发 + `agent_tool_definitions()` 注册（每类 agent 会话都会暴露这两个工具）

## 嵌套循环语义

```
messages = [system(人设+可用工具), user(prompt)]
for round in 1..max_rounds:
    校验取消 / 墙钟（> max_seconds 抛 TimeoutError）
    events = provider.stream_chat(messages, tools=definitions)
    攒 text_delta → text；攒 tool_calls
    无 tool_call → final = text，结束
    有 tool_call：
      append assistant(带 tool_calls)
      逐个执行 execute_agent_tool(name, args, agent_authorized=True)（仅限 allowed 集）
      异常 → {"error": class, "message"} JSON 回填
      结果 → JSON（≤8k 截断）作为 role=tool 消息
job.result = final（≤16k）；status = completed
```

## 关键约束（实现强制）

1. **允许集过滤**：`allowed` = 调用方 `tools` 子集或 `DEFAULT_SUBAGENT_TOOLS`（离线默认集）。定义层只注入 allowed 内的工具 schema；执行层再次校验，不在 allowed 的工具直接回 `{"error": "tool not allowed in sub-agent"}` 且**不执行**。
2. **无授权拦截**：默认工具集排除 `sandbox_fetch` / `sandbox_search_web` / `sandbox_git_clone` / `sandbox_subagent`，子代理永远不会触发宿主侧授权卡片。
3. **取消**：`cancel()` 置 `cancelled`；循环每轮起点检查该标记直接返回，成功路径也不会覆盖为 `completed`。
4. **资源上限**：`max_rounds`（1-12，默认 6）、墙钟（默认 300s）、工具结果 8k、最终结果 16k、注册表 64 job。
5. **隔离**：每个子代理独立 `SessionLocal` + 独立 `model_provider_for_workspace`（生产路径）；单元测试可注入 fake provider / fake sandbox。

## 生产依赖

- 配置：`sandbox_subagent_enabled`、`sandbox_subagent_max_seconds`、`sandbox_subagent_max_rounds`（`app/core/config.py`）
- provider：`model_provider_for_workspace(db, workspace_id, settings)` 返回工作区绑定的模型提供者（LLM 调用走工作区供应商配置）
- 数据库：运行器正常路径需要可用的 `SessionLocal`；子代理工具执行复用 `SandboxAgentWorkspaceService`（独立会话）

## 已知限制

- 注册表**进程内**：应用重启后 job 全部丢失，`sandbox_subagent_status` 对旧 id 返回 not_found。
- 后台线程"并行"是进程内线程级并行，非跨进程调度；多子代理共享同一 model provider 限流。
- 最终结果仅纯文本；需要结构产物时由子代理写入工作区，主代理再读取。
- Linux 容器内 cell 超时用 `signal.alarm`；无 SIGALRM 平台（Windows）上 kernel cell 超时退化为 daemon 墙钟。