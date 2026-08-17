# sandbox_subagent / sandbox_subagent_status 工具契约

## sandbox_subagent

启动一个后台沙箱子代理（嵌套 agent 循环），立即返回 `subagent_id`。

### 入参

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | string | ✅ | 自包含任务描述（子代理看不到主对话历史） |
| `tools` | array\<string\> | 否 | 工具名子集；缺省用离线默认集 |
| `max_rounds` | int 1-12 | 否 | 工具循环轮数上限（默认 6） |
| `sandbox_session_id` | string | 否 | 沙箱会话标识（透传） |

### 默认工具集（离线）

`sandbox_env_info`、`sandbox_list_files`、`sandbox_grep`、`sandbox_read_file`、`sandbox_write_file`、`sandbox_append_file`、`sandbox_edit_file`、`sandbox_delete_file`、`sandbox_exec`、`sandbox_bash`、`sandbox_todo`、`sandbox_apply_patch`、`sandbox_git`、`sandbox_skill_list`、`sandbox_skill_read`

### 出参

```json
{
  "subagent_id": "sa_<uuid>",
  "status": "queued"
}
```

## sandbox_subagent_status

### 入参

| 参数 | 类型 | 必填 |
|---|---|---|
| `subagent_id` | string | ✅ |

### 出参

```json
{
  "subagent_id": "sa_<uuid>",
  "status": "queued | running | completed | failed | cancelled",
  "rounds": 3,
  "error_class": null,
  "error_message": null,
  "result": "最终答案纯文本（≤16k；仅 completed 时有值）",
  "started_at": 1720000000.0,
  "finished_at": 1720000100.0
}
```

### 状态机

- `queued` → `running` → `completed` / `failed` / `cancelled`
- 未知 id（注册表进程内，重启即失）：返回 `{"status": "not_found"}`
- `failed` 常见 `error_class`：`TimeoutError`（墙钟超限）、`RuntimeError`（provider/工具异常）

### 执行语义

- 每轮：provider `stream_chat` 收文本与 `tool_calls` → 有 tool_call 则执行并回填 tool 结果，继续下一轮；无 tool_call 则取 final text 结束。
- 工具执行结果 JSON 截断至 8k 回填；最终结果截断至 16k。
- 墙钟上限 = 配置 `sandbox_subagent_max_seconds`（默认 300s）；`max_rounds` 用尽时返回提示文本而非失败。
- 子代理工具调用带 `agent_authorized=True`，但默认工具集不含 fetch/search/git_clone/subagent，因此**不会触发授权卡片**。