---
name: spreadsheet-analysis
description: CSV/XLS/XLSX/XLSB/ODS 表格的读取、探查、清洗、汇总与写出。
---

# 表格分析与处理

## When to use

- 用户上传或引用了 `.csv` / `.tsv` / `.xls` / `.xlsx` / `.xlsb` / `.ods`，需要**探查结构、清洗、汇总统计、写出新表格**。
- 需要对表格做 pandas 分析、生成报表或给下游（图谱、报告、前端图表）喂干净数据。

## 决策顺序

1. 先 `inspect_table.py` 看列名、类型、行数、前几行，确认编码与分隔符。
2. 清洗/转换 → `clean_table.py`（选列、去空、填值、重命名、过滤），产物 CSV/XLSX。
3. 统计 → `summarize_table.py`（describe、null 统计、唯一值）。
4. 写出 → `write_xlsx.py`（把结构化行写入带表头的 XLSX）。
5. 组合优先：`inspect → clean → summarize → write`；大文件按列选择/分块处理。

## 脚本索引

| 脚本 | 用途 |
|---|---|
| `inspect_table.py` | 列名/类型/行数/样例（结构探查） |
| `summarize_table.py` | describe + null + 唯一值统计 |
| `clean_table.py` | 选列/去空/填值/重命名/过滤 → 新表 |
| `write_xlsx.py` | 结构化行 → XLSX |

## 组合路线

```text
CSV ──inspect──> 结构
CSV ──clean──> 清洗.csv ──summarize──> 统计.json
清洗结果 ──write_xlsx──> 报表.xlsx
```

## 安全与限制

- 离线运行，不联网、不执行包安装、不读取 secret。
- 只读工作区内文件；路径限制同其他脚本。
- 读取 `xls`/`xlsb`/`ods` 依赖 xlrd/pyxlsb/odfpy——较旧格式可能失败，如实说明。
- 公式：openpyxl 默认读缓存值；不重新计算公式。

## 详细说明

组合配方见 `references/best-combinations.md`，输入/输出契约见 `references/input-output-contract.md`，常见失败见 `references/troubleshooting.md`。每个脚本的完整用法见 `scripts/*.md`。
