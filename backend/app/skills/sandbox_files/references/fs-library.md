# 预备脚本库：`learngraph_tasks.fs`

随沙箱镜像预装（纯标准库 + charset-normalizer），在 `sandbox_exec` 的脚本里 import 使用：

```python
import json
from learngraph_tasks.fs import file_stats, grep_lines

print(json.dumps(file_stats("inputs/notes.txt"), ensure_ascii=False))
print(json.dumps(grep_lines("TODO", glob="work/**/*.py", context=1), ensure_ascii=False))
```

## 与宿主工具的分工

| 场景 | 优先选择 |
|---|---|
| 单文件、毫秒级定位/修改 | 宿主工具（`sandbox_grep` / `sandbox_read_file` / `sandbox_edit_file`） |
| 容器内独有的文件（exec 产物） | `fs.grep_lines` / `fs.read_lines`（在脚本里直接搜） |
| 批量/多文件/转换 | `fs` 函数一次 exec 完成 |
| 大文件（>4 MiB） | `fs.grep_lines` / `fs.split_lines` / `fs.head_lines` |

## 函数速查

| 函数 | 作用 |
|---|---|
| `file_stats(path)` | 大小、行数、**编码探测**、sha256、mime |
| `find_files(name_glob, dirs, min_bytes, max_bytes, sort)` | 结构化找文件 |
| `grep_lines(pattern, glob, context, max_matches, case_sensitive)` | 内容检索（可搜容器独有文件） |
| `head_lines(path, n)` / `tail_lines(path, n)` | 抽头尾 |
| `read_lines(path, start, end)` | 行区间读取 |
| `replace_all(path, old, new, max_replacements=100)` | 批量替换并返回计数 |
| `insert_lines(path, at_line, lines)` | 按行号插入 |
| `delete_lines(path, start, end)` | 按行号区间删除 |
| `to_utf8(path, target=None)` | GBK/其他编码 → UTF-8 |
| `split_lines(path, chunk_lines, out_dir)` | 大文件切块（配合分页读） |
| `tree(max_depth=3)` | 目录树 |

## 约定

- 路径都是工作区相对路径（exec 的 cwd 是 `/workspace`）；绝对路径或 `..` 会被拒绝。
- 返回值是普通 Python 结构；脚本打印时用 `json.dumps(..., ensure_ascii=False)` 结构化输出。
- 大结果必须限量（`max_matches`、`n`、`max_depth`），宿主对 stdout 有字节截断（`sandbox_output_bytes`）。
- 文件修改默认写成 UTF-8；非 UTF-8 源文件先 `file_stats` 探测、`to_utf8` 转换。
