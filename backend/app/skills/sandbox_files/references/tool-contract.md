# 工具输入输出契约（sandbox_* 文件工具）

会话与 runtime 由宿主绑定；普通调用不需要传 ID。旧调用中的 `sandbox_session_id` 仍接受并校验。子代理会话由编排器绑定。

## sandbox_list_files

前台 `sandbox_exec` 遇到容量满额时由服务端持有等待，容量释放后继续执行，同一次调用返回最终结果。排队有服务端截止时间且支持用户取消，不需要模型轮询。显式 `yield_time_ms` 保留后台语义；后台 job 的 `sandbox_get_job` 默认等待完成，不传 `wait_seconds` 即可。`wait_seconds=0` 只用于主动查询即时状态；`output_cursor` 仍可增量回读。

- 入参：`pattern`（可选 glob，如 `work/**/*.py`）、`max_results`（默认 200，上限 1000）
- 出参：`files: [{path, size_bytes, role, source, mtime, file_id}]`
- `role`：`input`（上传附件）/ `work`（草稿）/ `output`（已发布产物）
- `source`：`chat_attachment` / `agent_write` / `agent_publish` / `container`（仅容器内存在）

## sandbox_grep

- 入参：`pattern`（正则，必填）、`path`（可选 glob）、`case_sensitive`、`context_lines`(0-5)、`max_matches`(默认 50，上限 500)
- 出参：`matches: [{path, line_number, text, context:[{line_number, text}]}]`、`file_counts`、`searched_files`、`skipped_binary/skipped_large/skipped_container_only`、`truncated`
- 语义：按行匹配；`context_lines` 重叠窗口自动合并；匹配行数达到上限即停并置 `truncated=true`
- 约束：单文件 > 4 MiB 跳过（`skipped_large`）；非 UTF-8 跳过（`skipped_binary`）；包含 exec 生成的活跃文件。

## sandbox_read_file

- 入参：单文件 `path` 加行范围/游标；批量用 `paths`，或 `items:[{path,start_line,end_line,cursor,max_chars}]` 为每项指定不同范围；三者择一。
- 批量总文本预算用 `max_total_chars`（默认 16000），每项保留独立 `next_cursor`，不需要多轮调用来读取不同文件的不同段落。
- 出参：`content`、`total_lines`、`total_bytes`、`start_line`、`end_line`、`truncated`
- 语义：1-based 闭区间；不传则读全文件；`start_line` 超过文件行数报 `sandbox_file_range_out_of_bounds`
- 错误：非 UTF-8 → `sandbox_file_not_text`；超过字节上限 → `sandbox_file_too_large`

## sandbox_write_file / sandbox_append_file

- 入参：`path`、`content`（append 可选 `expected_sha256` 并发校验）
- 出参：`sha256`、`size_bytes`、`file_id`（仅 outputs 下生成可下载产物）
- 写 `outputs/` 路径会登记为可下载 artifact
- 新写入只需调用一次 write_file；支持参数流的 Provider 会提前将片段送入沙箱暂存，完整参数校验后原子提交。中断不覆盖旧文件。append 保留兼容，不用它模拟 Token 流。

## sandbox_edit_file

- 入参：`path`、`old_string`、`new_string`、`expected_sha256`（模型工具可省略，由当前运行已读取的版本填入；直接 HTTP 接口仍必填）、`replace_all`(默认 false)
- 语义：先校验 sha256（变了报 409 `sandbox_file_changed`）；唯一匹配否则 422 `sandbox_edit_match_invalid`；`replace_all` 允许全部替换但上限 100 次（超出报 `sandbox_edit_too_many_matches`）
- 出参：额外含 `replaced_count`

## sandbox_delete_file

- 入参：`path`（必须位于 `work/` 树下）
- 流程：默认免审批——work/ 树内删除直接执行（Docker 沙箱隔离，不影响宿主机文件）；部署若设置 `LEARNGRAPH_SANDBOX_DELETE_APPROVAL_MODE=on` 才恢复单次授权流程（403 `sandbox_auth_required` → 聊天 UI 授权 → 重试消费）
- 出参：`{deleted: true, path}`
- 错误：非 work/ 路径 → 422 `sandbox_path_blocked`

## sandbox_exec

- 入参：`command`（shell 字符串，支持内联脚本）或 `argv`（已有脚本），两者择一；可选 `timeout_seconds`、`yield_time_ms`、`max_output_tokens`；cwd 固定为工作区根。
- 出参：`exit_code`、`stdout`、`stderr`、`timed_out`、`truncated`。超过模型预算时仍保留控制字段与 stderr，并返回 `result_ref`。
- `yield_time_ms` 启用持久后台任务；短等待内完成则直接返回结果，否则返回 `job_id`。
- 容器内执行；输出和执行时间受资源上限约束。模型 Token 预算不影响资源上限内完整结果留存。

## sandbox_get_job / sandbox_read_result

- get_job：`job_id`、`wait_seconds`（0..10）、`output_cursor`（上次 next_output_cursor），返回状态与新增输出。不同聊天的任务 ID 不可混用。
- read_result：`result_ref`、可选 `field`（stdout/stderr 等）、`cursor`、`max_chars`（1..16000）、`pattern`（字面文本搜索）。返回 content、has_more、next_cursor、版本与总长度。
- 结果按工作区、用户与聊天隔离；已截断结果无需重新执行命令即可恢复。

## sandbox_bash

- 入参：`command`（bash -lc 的脚本串，可含换行）、`timeout_seconds`（可选，上限=沙箱墙钟）、`sandbox_session_id`
- 出参：同 `sandbox_exec`（`exit_code`/`stdout`/`stderr`/`timed_out`/`truncated`）
- 约束：argv 形态固定为 `["bash","-lc",command]`，宿主永不 eval 该串；命令含 `rm/mv/shred/dd/...` 且目标在 `work/` 下时默认免审批直接执行（沙箱隔离；部署设 `LEARNGRAPH_SANDBOX_DELETE_APPROVAL_MODE=on` 时恢复单次授权）；目标为绝对/`..`/非 work/ 路径直接硬拦；容器默认离线，联网命令失败

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
