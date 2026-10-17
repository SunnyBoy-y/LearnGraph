---
name: sandbox-files
description: 沙箱工作区文件的定位、检索、分页读取、精确编辑与安全删除（sandbox_* 文件工具）。
---

# 沙箱文件处理

## When to use

- 智能体需要在沙箱工作区（`inputs/` 附件、`work/` 草稿、`outputs/` 产物）里**定位、检索、读取、修改、删除**文件。
- 用户上传了文档/代码/数据文件，需要先看内容再决定怎么处理。

## 决策顺序（核心：小操作走工具，重活走 exec）

```text
sandbox_list_files 定位目录结构
  └─ pattern 过滤（work/**/*.py）
sandbox_grep 内容检索（正则 + 上下文）
  └─ 找到位置后再读，不要全量读大文件
sandbox_read_file 分页读取（start_line/end_line/max_chars）
  └─ 返回 total_lines/total_bytes，按页推进
sandbox_edit_file 精确修改（唯一串替换，先 read；运行时自动校验已读版本）
  └─ replace_all=true 仅用于批量同改（上限 100 次）
sandbox_exec 验证/批量/多文件处理（learngraph_tasks、fs 库）
```

**判据**：
- 单文件小改动 → `sandbox_write_file / sandbox_edit_file`
- 找位置 → `sandbox_grep`（与 exec/read 使用一致的活跃工作区视图）
- 读大文件 → `sandbox_read_file` 带行区间，不要一次全量读
- 多文件/批量/需要 Chromium/ffmpeg/工具链 → `sandbox_exec` + 预备脚本库
- 一次性计算、管道、临时批处理 → 直接 `sandbox_exec(command=...)`；不必先写脚本。
- 前台 `sandbox_exec` 由服务端等待容量并返回最终结果，不要另发状态查询或重复执行。只有需要后台执行时才传 `yield_time_ms`；之后 `sandbox_get_job` 不传 `wait_seconds` 即等待完成，`0` 仅用于刻意读取即时快照。用户取消会中止排队。
- 代码需要复用、编辑或交付时才写文件；会话 ID 与 runtime 由宿主自动绑定。
- 专项工具未出现在本轮列表时，用 `lg_capability_search` 搜索并激活；旧调用保持兼容。

## 工具索引

| 工具 | 何时用 | 何时不用 |
|---|---|---|
| `sandbox_list_files` | 先看工作区有什么；用 `pattern` 过滤、`max_results` 限量 | 文件很多时不要无过滤全列 |
| `sandbox_grep` | 找符号/报错串/模式，包含 exec 生成文件；`context_lines` 返回上下文 | 二进制文件需专用处理 |
| `sandbox_read_file` | 读内容；大文件用 `start_line/end_line/max_chars` 分页 | 二进制/非 UTF-8（会报 `sandbox_file_not_text`） |
| `sandbox_write_file` | 一次调用新建/覆盖；运行时增量接收参数，校验完成后提交 | 不要自行分块多次 append；小改动用 edit |
| `sandbox_edit_file` | 唯一串替换；先 read，运行时自动填入已读版本；也可显式传 `expected_sha256` | 多处同改时 `replace_all`（≤100）；超过走 exec |
| `sandbox_delete_file` | 删除 work/ 下单个文件（沙箱隔离，默认免审批） | inputs/outputs/宿主文件不可删 |
| `sandbox_exec` | `command` 执行内联代码、shell、管道、批处理；`argv` 运行已有脚本，两者择一 | 输出用预算限制，必要时按 result_ref 回读 |
| `sandbox_get_job` | `wait_seconds` 等待任务；`output_cursor` 避免重复接收日志 | 不要高频空轮询 |
| `sandbox_read_result` | 按 result_ref 分页回读；可选 stdout/stderr 字段或文字搜索 | 不要为了读日志重新运行原命令 |
| `sandbox_bash` | 旧调用兼容；新调用用 sandbox_exec(command=...) | 不必为同一任务切换两种执行入口 |
| `sandbox_todo` | 多步任务的会话级清单 add/done/list | 单步任务不需要 |
| `sandbox_apply_patch` | 多 hunk/多文件统一 diff（git 格式）一次应用 | 单点小改动用 edit_file 更直接 |
| `sandbox_git` | 工作区内本地 git（status/log/commit/init），离线 | clone/fetch/pull 必须走 `sandbox_git_clone` 审批通道 |
| `sandbox_git_clone` | 克隆公开 GitHub 仓库（宿主侧走审批，快照 + 容器侧 git init） | 有 egress 审批但无真实 git 历史，需要完整历史时另行处理 |
| `sandbox_search_web` / `sandbox_fetch` | 宿主侧联网检索/抓取（走授权域），容器保持离线 | 需要未经授权域时不要用（会弹授权卡片） |
| `sandbox_subagent` + `sandbox_subagent_status` | 并行委派独立子任务（受限工具集） | 简单任务直接自己做；子代理不能发起授权卡片 |
| `sandbox_skill_list` / `sandbox_skill_read` | 查看/读取官方技能指令 | 运行中技能已注入 prompt 时不必重复读 |
| `sandbox_notebook` | 需要跨步骤保持 Python 解释器状态（open/execute/close） | 一次性计算用 sandbox_exec 更省资源 |

## 组合路线

```text
定位: list(pattern) → grep(pattern) → read(start_line..end_line)
修改: read → edit(唯一串，自动版本校验) → 验证
临时批量: exec(command=内联脚本) → 查看输出
复用代码: write 脚本 → exec(argv=[python, work/x.py]) → 验证
长任务: exec(yield_time_ms=1000) → get_job(wait_seconds=10)
大结果: result_ref → read_result(field=stdout/stderr, cursor=next_cursor)
多文件: apply_patch(统一 diff) → read 验证
删除: delete_file(work/…，免审批) 或 exec 内删除（限 work/ 树）
版本: git init → add → commit（容器内）→ log/status
联网: git_clone(审批) / search_web / fetch(授权域) → 宿主侧获取，容器离线
并行: subagent(prompt) → subagent_status 轮询结果
长会话: todo 清单跟踪多步计划
交互状态: notebook open → execute(保持状态) → close
```

## 安全与限制

- 路径必须相对且不能越出工作区；`inputs/`/`outputs/` 只读，删除限 `work/` 树。
- 文件受 `sandbox_agent_file_bytes` 上限；`sandbox_grep` 单文件 4 MiB、总扫描 64 MiB 上限。
- `sandbox_read_file` 严格 UTF-8；中文 GBK 老文档先用 `learngraph_tasks.fs.file_stats` 探测、`to_utf8` 转换。
- work/ 树内删除默认免审批（Docker 沙箱隔离，不影响宿主机文件）；路径越出 `work/` 树或含绝对路径/`..`/盘符时，策略直接硬拦（`sandbox_path_blocked`）。
- `work/` 读取、搜索和发布以活跃执行视图为准；运行时不可用时不会静默返回旧副本。
- 输出在资源上限内完整保留；模型接收有界预览，完整内容用 `sandbox_read_result` 读取。
- `learngraph_tasks.fs` 库随镜像分发：先 import 探测再使用，`ModuleNotFoundError` 时降级到宿主工具（宿主工具永远可用）。

## 详细说明

- 工具输入输出契约见 `references/tool-contract.md`
- 常见失败与对策见 `references/troubleshooting.md`
- 预备脚本库 `learngraph_tasks.fs` 用法见 `references/fs-library.md`
