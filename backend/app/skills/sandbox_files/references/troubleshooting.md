# 常见失败与对策（沙箱文件处理）

| 现象 | 原因 | 对策 |
|---|---|---|
| `sandbox_file_not_text` (422) | 文件不是严格 UTF-8（常见：中文 GBK/GB18030 老文档） | 先用 `sandbox_exec` + `learngraph_tasks.fs.file_stats` 探测编码，`to_utf8` 转成 UTF-8 再读 |
| `sandbox_file_changed` (409) | 编辑/追加时文件已被并发修改，sha256 不匹配 | 重新 `sandbox_read_file` 拿最新 sha256 再操作 |
| `sandbox_edit_match_invalid` (422) | old_string 出现 0 次或多次 | 用 `sandbox_grep` 确认唯一性；多处同改时 `replace_all=true` |
| `sandbox_edit_too_many_matches` (422) | replace_all 命中 > 100 处 | 这是安全上限：改走 `sandbox_exec` 脚本做批量重写 |
| `sandbox_file_too_large` (422) | 文件超过 `sandbox_agent_file_bytes` | 大文件用 `sandbox_exec` 内处理，或用 `fs.split_lines` 切块后分页读 |
| `sandbox_file_range_out_of_bounds` (422) | start_line 超出文件行数 | 看返回的 `total_lines`，用 `end_line` 钳制（超出自动收尾） |
| grep 搜不到某文件 | 该文件是 `sandbox_exec` 在容器内生成的，宿主存储没有 | 先 `sandbox_read_file`（有容器兜底）拉回宿主，或在脚本里直接搜 |
| grep 返回 `skipped_large` | 文件 > 4 MiB | 用 `sandbox_exec` + `fs.grep_lines`（可设更大范围）或先 split 再搜 |
| 删除报 `sandbox_auth_required` (403) | 部署开启了审批模式（`LEARNGRAPH_SANDBOX_DELETE_APPROVAL_MODE=on`） | 预期流程：聊天 UI 弹授权框，用户允许后重试一次即可；默认部署（off）下 work/ 树内删除免审批不会触发 |
| 删除报 `sandbox_path_blocked` (422) | 目标不在 `work/` 树 | 只能删工作区草稿；inputs/outputs 与宿主文件不可删 |
| `sandbox_grep_invalid_pattern` (422) | 正则语法错误 | 修正正则；需要纯文本匹配时先 `re.escape` |
| exec 输出被截断（`truncated=true`） | 超过 `sandbox_output_bytes` | 脚本 stdout 用结构化 JSON + 摘要/计数，不要打印大块原文 |
| exec 超时（`timed_out=true`） | 超过 `sandbox_wall_time_seconds` | 缩小任务范围；批量用增量/分批脚本 |
