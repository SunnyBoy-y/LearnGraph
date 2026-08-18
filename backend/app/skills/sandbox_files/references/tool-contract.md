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

## sandbox_bash

- 入参：`command`（bash -lc 的脚本串，可含换行）、`timeout_seconds`（可选，上限=沙箱墙钟）、`sandbox_session_id`
- 出参：同 `sandbox_exec`（`exit_code`/`stdout`/`stderr`/`timed_out`/`truncated`）
- 约束：argv 形态固定为 `["bash","-lc",command]`，宿主永不 eval 该串；命令含 `rm/mv/shred/dd/...` 且目标在 `work/` 下时走单次授权（同 `sandbox_delete_file`）；目标为绝对/`..`/非 work/ 路径直接硬拦；容器默认离线，联网命令失败

## sandbox_todo

- 入参：`action`（list/add/done/remove/clear）、`text`（add 必填）、`item_id`（done/remove 必填）
- 出参：`items`（[{id,text,status,created_at,updated_at}]）、`revision`、`open_count`
- 语义：会话级宿主侧清单，不占容器；跨容器重启存活

## sandbox_apply_patch

- 入参：`patch`（git 统一 diff 文本）、`fuzz`（0-10，默认 3）、`sandbox_session_id`
- 出参：`files_changed`（[{path, action: create|modify|delete}]）
- 语义：宿主侧解析并应用到 durable 工作区（容器无需启动）；文件删除复用单次授权闸门；CRLF 归一为 LF
- 错误：解析失败 `sandbox_patch_parse_error`；hunk 无法匹配 `sandbox_patch_apply_error`；目标缺失 `sandbox_patch_missing_file`

## sandbox_git / sandbox_git_clone

- `sandbox_git`：入参 `args`（git 子命令数组，如 `["-C","work/repo","log","--oneline"]`）；容器内离线执行；`git rm/checkout --/restore/mv` 命中 work/ 路径时走单次授权；`git clean/reset` 无明确路径直接硬拦；`clone` 子命令被拒，必须走 `sandbox_git_clone`
- `sandbox_git_clone`：入参 `owner/repo/ref/path/destination_root`；宿主侧经 EgressApprovalService 审批（首调弹 `egress_authorization_required` 卡片）→ `ExternalAcquisitionService.download_github_source` 快照物化 → 容器侧 `git init + add + commit`（无真实 git 历史）

## sandbox_search_web / sandbox_fetch

- 两者均为宿主侧实现：容器保持 `network_mode=none`，请求走已授权域
- `sandbox_search_web`：`query`（1-500 字）、`max_results`（1-12）；经 SearchProvider，结果限授权域
- `sandbox_fetch`：`url`；host 不在 `web_fetch.policy`/用户策略/统一 allowlist 时先弹 `fetch_domain_authorization_required` 授权卡片（FetchAuthorizationRequest），批准后经 SandboxFetchProvider 抓取并二次校验 final_url（SSRF 硬闸）

## sandbox_subagent / sandbox_subagent_status

- `sandbox_subagent`：`prompt`（自包含任务）、`tools`（可选子集）、`max_rounds`；后台线程独立 Session + 独立 model provider 跑受限工具集循环，立即返回 `subagent_id`
- `sandbox_subagent_status`：`subagent_id`；轮询 `status`（queued/running/completed/failed/cancelled）与 `result`（最终纯文本，≤16k）
- 约束：子代理工具集默认不含 search/fetch/git_clone/subagent（不能弹授权卡片）；进程内注册表，重启即失

## sandbox_skill_list / sandbox_skill_read

- `sandbox_skill_list`：返回官方技能目录（key/category/description）
- `sandbox_skill_read`：`skill_key`；返回对应 SKILL.md 全文（≤20k 字符）

## sandbox_notebook

- 入参：`action`（open/execute/close/status）、`kernel_id`、`code`、`interpreter`（v1 仅 python）
- 语义：sandboxd 新增 kernel 端点；容器内经 `docker exec -d` 启动纯 stdlib socket 服务器，`execute` 经一次性 client exec 送 cell 并回读 JSON（stdout/stderr/result_repr/timed_out）；状态跨 cell 保持
- 约束：cell 超时受沙箱墙钟；输出经 daemon 256KiB 传输上限；kernel 随沙箱停止自动清理（watchdog + 控制器钩子）
