# `_common.py` — 内部共享辅助（非独立脚本）

> 供本 Skill 其它脚本 `import _common` 复用：安全路径校验、格式判断、表格读取、sheet 列表。不作为独立命令运行。

## 用法

```python
from _common import safe_path, read_table, sheet_names, suffix
```

## 函数

| 函数 | 说明 |
|---|---|
| `safe_path(value)` | 拒绝绝对路径/`..`/`.`，返回 `Path` |
| `suffix(path)` | 小写扩展名（无点） |
| `read_table(path, sheet, encoding, sep)` | 读取 CSV/TSV/Excel 为 DataFrame |
| `sheet_names(path)` | Excel 的 sheet 名列表（非 Excel 返回空） |

## 限制

- 只在沙箱内被同目录脚本导入；不能从宿主进程导入。
- 修改此文件会影响同 Skill 所有脚本——保持向后兼容。
