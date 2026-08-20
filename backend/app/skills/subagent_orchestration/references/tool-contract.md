# sandbox_subagent 系列工具契约 v2

## sandbox_subagent

启动一个持久化沙箱子代理任务（v2 走统一调度器），立即返回 `subagent_id`。

### 入参

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | string | ✅ | 自包含 Context Pack（子代理看不到主对话历史），≤16k |
| `title` | string | 否 | 可读任务名（UI 展示），默认"子代理任务" |
| `role_key` | string | 否 | `generic`（默认）/ scout / architect / implementer / reviewer / artifact |
| `tools` | array\<string\> | 否 | 工具名子集；缺省用离线默认集 |
| `skills` | array\<string\> | 否 | 预激活 Skill key 列表（≤8） |
| `max_rounds` | int 1-12 | 否 | 工具循环轮数上限（默认 6） |
| `max_seconds` | int 30-900 | 否 | 墙钟上限（默认配置 300） |
| `max_tool_calls` | int 1-200 | 否 | 工具调用总数上限 |
| `max_tokens` | int | 否 | token 预算（默认配置 60k） |
| `max_cost_usd` | float | 否 | 费用预算（默认配置 $0.15） |
| `write_set` | array\<string\> | 否 | 允许写路径前缀；缺省=任务车道 `work/subagents/<task_id>/` |
| `output_contract` | object | 否 | 交付契约自定义字段 |
| `sandbox_session_id` | string | 否 | 绑定沙箱会话（注入到每个工具调用） |

### 出参

```json
{
  "subagent_id": "sa_<uuid>",
  "task_id": "sa_<uuid>",
  "job_id": "<job id>",
  "status": "queued",
  "sandbox_session_id": "..."
}
```

## sandbox_subagent_status

### 入参

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `subagent_id` | string | ✅ | |
| `after_event_seq` | int | 否 | 只返回此 seq 之后的事件（增量） |

### 出参

```json
{
  "subagent_id": "sa_...",
  "task_id": "sa_...",
  "title": "视觉样式",
  "role_key": "implementer",
  "status": "queued|running|finalizing|succeeded|partial|failed|timed_out|cancelled|interrupted",
  "status_reason": null,
  "error_class": null,
  "error_message": null,
  "rounds": 3,
  "tool_calls": 12,
  "result": "最终答案文本（≤16k）",
  "deliverables": {"summary": "...", "artifacts": [...], "evidence": [...], "acceptance": [...]},
  "event_seq": 9,
  "events": [{"seq": 8, "event_type": "progress", "payload": {...}}],
  "latest_job_id": "..."
}
```

未知/过期 id：HTTP 404（`sandbox_subagent_not_found`）。

## sandbox_subagent_wait

等待一个或多个子代理任务达到终态或超时。

### 入参

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `subagent_ids` | array\<string\> | ✅ | 1..8 个任务 |
| `mode` | `any` \| `all` | 否 | `all`（默认）等全部；`any` 首个变化即返回 |
| `timeout_ms` | int 1000-60000 | 否 | 本次等待上限（默认 30000；服务端按片执行） |
| `after_event_seq` | int | 否 | 增量事件游标 |

### 出参

```json
{
  "tasks": [<status 快照>...],
  "retry_after_ms": 0,
  "timed_out": false
}
```

未达终态时 `timed_out: true` + `retry_after_ms`（建议模型按此间隔再次调用，不要连续轮询）。

## sandbox_subagent_cancel

请求取消（协作文：任务确认退出后才进入 `cancelled`；取消中可能仍显示 `running`）。

### 入参：`subagent_id`

### 出参：任务快照（当前状态）

## sandbox_subagent_retry

同一任务新建 attempt 重新排队（旧 attempt 保留在历史）。

### 入参

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `subagent_id` | string | ✅ | |
| `scope` | `same` \| `scoped` | 否 | `same` 沿用原 spec；`scoped` 允许缩小 prompt |
| `prompt_override` | string | 否 | `scoped` 时的新 prompt |
| `note` | string | 否 | 重试原因（进事件审计） |

### 出参：任务快照

## 状态机

```text
queued → running → finalizing → succeeded
                       ├→ partial   （预算/交付契约不满足，可能有产物）
                       ├→ failed    （异常/空结果）
                       ├→ timed_out （墙钟）
                       └→ cancelled （确认取消）
interrupted：进程/租约丢失后恢复判定（重启场景）
```

只有 `succeeded` 表示交付成功；`partial` 时必须检查 `deliverables` 与工作区文件。
