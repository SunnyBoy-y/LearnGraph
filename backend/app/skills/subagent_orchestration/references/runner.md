# 子代理运行器实现说明（v2）

## 位置与结构

- **执行器**：`backend/app/services/sandbox_agent_executor.py`
  - `execute_subagent_job(settings, job, task, *, emit_event, provider, sandbox_service)`：一次执行的完整循环，返回 `SubagentRunOutcome`（status / event_type / summary / deliverables / attempt_record / error）。
  - `finalize_deliverables(result_text, *, default_output_root, file_exists)`：FINALIZING 机器校验交付契约。
- **调度**：`backend/app/services/sandbox_scheduler.py`
  - `SandboxSchedulerService._execute_job` 新增 `kind=="subagent"` 分支 → `_execute_subagent`；
  - `_execute_subagent`：四层配额（chat/user/workspace）→ 置 RUNNING → 调执行器 → 更新 task（status/deliverables/attempts）→ 写终态事件 → `_finish(job, ...)`；
  - `append_agent_event(db, task, event_type, payload)`：写 `sandbox_agent_events`（(task_id, seq) 幂等）并推进 `task.event_seq`。
- **持久化**：`backend/app/domain/models.py`
  - `SandboxAgentTask`：任务身份 + 冻结 spec_json + 最新状态 + deliverables_json + attempts_json 历史；
  - `SandboxAgentEvent`：生命周期事件流（UI/SSE/审计）。
- **提交**：`backend/app/services/sandbox_toolkit.py`
  - `toolkit_subagent`：建 task → `SandboxSchedulerService.submit_job(kind="subagent")` → 事件 created/queued；
  - `toolkit_subagent_status/_wait/_cancel/_retry`：查 task + job，返回快照/事件。
- **兼容**：`backend/app/services/sandbox_subagent.py` 保留 v1 进程内 Registry 仅供旧任务查询兜底（不再新增 v1 任务）。

## 执行语义

```
任务契约（job.payload_json）:
  {task_id, task_title, role_key, prompt, tools, write_set, budget, sandbox_session_id}

for round in 1..max_rounds:
    检查 cancelled → CANCELLED；检查 deadline → TIMED_OUT
    events = provider.stream_chat(messages, tools=definitions)
    累计 last_usage token；按 pricing_catalog 折算成本
    emit progress / tool_call 事件
    无 tool_call：
      空文本 → FAILED(EmptyResult)
      非空 → 正常结束
    有 tool_call：
      max_tool_calls 超限 → PARTIAL(MaxToolCallsExhausted)
      逐个执行（allowed 集过滤 → session 注入 → 写集校验 → execute_agent_tool）
      token/cost 超限 → PARTIAL(BudgetExhausted)
轮数耗尽 → PARTIAL(MaxRoundsExhausted)
FINALIZING:
  finalize_deliverables(result_text, ...) → 契约完整且产物存在 → SUCCEEDED
  否则 → PARTIAL(HandoffIncomplete)
```

## 关键约束

1. **写集**：文件类工具（write/append/edit/delete）在 `execute_agent_tool` 前做前缀校验，越界返回 `write_not_allowed` 且不执行；未声明 write_set 时默认只允许 `work/subagents/<task_id>/` 车道。
2. **取消**：`SandboxSchedulerService.cancel_job` 置 `cancel_requested`；执行器每轮起点检查 → CANCELLED。阻塞中的 provider 流不可硬中断（协作式取消，adapter 能力受限时等待流返回）。
3. **预算**：rounds / wall clock / tool calls / tokens / USD cost 五维；token 优先取 `provider.last_usage`，缺失时按字符估算；成本按 `pricing_catalog.PRICING_CATALOG` 匹配（无价格条目时仅按 token 上限）。
4. **配额**：`_subagent_quota_exceeded` 检查 chat/user/workspace 三级活动数（QUEUED+STARTING+RUNNING），超限 requeue（5s 后重试），不失败调用方。
5. **重试**：`toolkit_subagent_retry` 用新 idempotency key 提交新 job，attempts_json 追加历史。
6. **交付**：模型输出末尾 JSON 块 → 机器解析 + 结构校验 + 产物存在性尽力校验（sandbox 不可用时跳过并标记），`handoff_parse=false` 时交付不完整 → PARTIAL。

## 事件类型

`created` `queued` `started` `progress` `tool_call` `finalizing` `succeeded` `partial` `failed` `timed_out` `cancelled` `retry_scheduled`（预留 `checkpoint` `takeover_required` `claimed_by_parent` `delivered` 供 P3 使用）。

## 生产依赖与限制

- 单进程部署（uvicorn workers=1）：事件 seq 由 `task.event_seq + 1` 分配，进程内安全；多 worker 需改 DB 序列。
- wait 工具在 chat 主循环内联分片执行（≤5s/片），不占单 worker Agent 工具执行器；模型按 `retry_after_ms` 再次调用。
- 进程崩溃后：QUEUED 任务由调度器 tick 继续；RUNNING 任务心跳超时后需恢复逻辑标记 `interrupted`（P3）。
