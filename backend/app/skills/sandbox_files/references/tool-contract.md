# 工具输入输出契约（sandbox_* 文件工具）

所有工具都接受可选的 `sandbox_session_id`；省略时自动复用/创建本聊天的会话，回传上一次结果里的 `sandbox_session_id` 可保持会话。

## sandbox_list_files

- 入参：`pattern`（可选 glob，如 `work/**/*.py`）、`max_results`（默认 200，上限 1000）
- 出参：`files: [{path, size_bytes, role, source, mtime, file_id}]`
- `role`：`input`（上传附件）/ `work`（草稿）/ `output`（已发布产物）
- `source`：`chat_attachment` / `agent_write` / `agent_publish` / `container`（仅容器内存在）

## sandbox_grep

- 入参：`pattern`（正则，必填）、`path`（可选 glob）、`case_sensitive`、`context_lines`(0-5)、`max_matches`(默认 50，上限 500)
- 出参：`matches: [{path, line_number, text, context:[{line_number, text}]}]`、`file_counts`、`searched_files`、`skipped_binary/skipped_large/skipped_container_only`、`truncated`
- 语义：按行匹配；`context_lines` 重叠窗口自动合并；匹配行数达到上限即停并置 `truncated=true`
- 约束：单文件 > 4 MiB 跳过（`skipped_large`）；非 UTF-8 跳过（`skipped_binary`）；仅容器内文件跳过（`skipped_container_only`）

## sandbox_read_file

- 入参：`path`、`start_line`(≥1)、`end_line`(≥start_line，超出自动钳制)、`max_chars`
- 出参：`content`、`total_lines`、`total_bytes`、`start_line`、`end_line`、`truncated`
- 语义：1-based 闭区间；不传则读全文件；`start_line` 超过文件行数报 `sandbox_file_range_out_of_bounds`
- 错误：非 UTF-8 → `sandbox_file_not_text`；超过字节上限 → `sandbox_file_too_large`

## sandbox_write_file / sandbox_append_file

- 入参：`path`、`content`（append 可选 `expected_sha256` 并发校验）
- 出参：`sha256`、`size_bytes`、`file_id`（仅 outputs 下生成可下载产物）
- 写 `outputs/` 路径会登记为可下载 artifact

## sandbox_edit_file

- 入参：`path`、`old_string`、`new_string`、`expected_sha256`（必填，来自 read 的 sha256）、`replace_all`(默认 false)
- 语义：先校验 sha256（变了报 409 `sandbox_file_changed`）；唯一匹配否则 422 `sandbox_edit_match_invalid`；`replace_all` 允许全部替换但上限 100 次（超出报 `sandbox_edit_too_many_matches`）
- 出参：额外含 `replaced_count`

## sandbox_delete_file

- 入参：`path`（必须位于 `work/` 树下）
- 流程：首次调用无授权时报 403 `sandbox_auth_required`（details 含 `command_intent_digest`、`message_zh`），聊天 UI 弹授权；用户授权后重试即消费授权并完成删除
- 出参：`{deleted: true, path}`
- 错误：非 work/ 路径 → 422 `sandbox_path_blocked`

## sandbox_exec

- 入参：`argv`（如 `["python", "main.py"]`）、`cwd`（仅 "."）、`runtime`（已废弃，统一镜像）
- 出参：`exit_code`、`stdout`（截断展示）、`stderr`、`timed_out`、`truncated`、`latency_ms`
- 约束：只执行工作区内 .py/.js；argv 不进 shell；输出受 `sandbox_output_bytes` 截断；壁钟时间受 `sandbox_wall_time_seconds`
