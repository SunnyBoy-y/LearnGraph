---
name: sandbox-files
description: 沙箱工作区文件的定位、检索、分页读取、精确编辑与授权删除（sandbox_* 文件工具）。
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
sandbox_edit_file 精确修改（唯一串替换，先 read 拿 sha256）
  └─ replace_all=true 仅用于批量同改（上限 100 次）
sandbox_exec 验证/批量/多文件处理（learngraph_tasks、fs 库）
```

**判据**：
- 单文件小改动 → `sandbox_write_file / sandbox_edit_file / sandbox_append_file`
- 找位置 → `sandbox_grep`（宿主侧，不启动容器）
- 读大文件 → `sandbox_read_file` 带行区间，不要一次全量读
- 多文件/批量/需要 Chromium/ffmpeg/工具链 → `sandbox_exec` + 预备脚本库

## 工具索引

| 工具 | 何时用 | 何时不用 |
|---|---|---|
| `sandbox_list_files` | 先看工作区有什么；用 `pattern` 过滤、`max_results` 限量 | 文件很多时不要无过滤全列 |
| `sandbox_grep` | 找符号/报错串/模式，正则 + `context_lines` 上下文 | 文件是 exec 产物（容器内）时搜不到，先 read 或进脚本搜 |
| `sandbox_read_file` | 读内容；大文件用 `start_line/end_line/max_chars` 分页 | 二进制/非 UTF-8（会报 `sandbox_file_not_text`） |
| `sandbox_write_file` | 全量新建/覆盖 | 已有文件的小改动用 edit |
| `sandbox_edit_file` | 唯一串替换；先 read 拿 `expected_sha256` | 多处同改时 `replace_all`（≤100）；超过走 exec |
| `sandbox_delete_file` | 删除 work/ 下单个文件（会弹用户授权） | inputs/outputs/宿主文件不可删 |
| `sandbox_exec` | 跑 workspace 内 .py/.js 脚本 | 单文件小操作有专用工具时不要用（每次有快照开销） |

## 组合路线

```text
定位: list(pattern) → grep(pattern) → read(start_line..end_line)
修改: read(拿 sha256) → edit(唯一串) → read 验证
批量: write 脚本 → exec(python work/x.py) → grep 验证
删除: delete_file(work/…，用户授权) 或 exec 内授权删除
```

## 安全与限制

- 路径必须相对且不能越出工作区；`inputs/`/`outputs/` 只读，删除限 `work/` 树。
- 文件受 `sandbox_agent_file_bytes` 上限；`sandbox_grep` 单文件 4 MiB、总扫描 64 MiB 上限。
- `sandbox_read_file` 严格 UTF-8；中文 GBK 老文档先用 `learngraph_tasks.fs.file_stats` 探测、`to_utf8` 转换。
- 删除必须先过单次用户授权（`sandbox_auth_required` → 聊天内授权对话框），授权即用即失效。
- exec 生成但未写回宿主存储的文件，`sandbox_grep` 索引不到——先在脚本内处理或 read_file 拉回。
- 大输出受 `sandbox_output_bytes` 截断；脚本 stdout 用结构化 JSON + 摘要。
- `learngraph_tasks.fs` 库随镜像分发：先 import 探测再使用，`ModuleNotFoundError` 时降级到宿主工具（宿主工具永远可用）。

## 详细说明

- 工具输入输出契约见 `references/tool-contract.md`
- 常见失败与对策见 `references/troubleshooting.md`
- 预备脚本库 `learngraph_tasks.fs` 用法见 `references/fs-library.md`
