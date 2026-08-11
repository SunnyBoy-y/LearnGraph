---
name: data-processing
description: JSON/CSV/文本批处理、转换、统计与 Markdown 报告生成；CSV 作为批处理管道一环/产出报告或 JSON → 本 Skill；围绕「表」的探查清洗写出 → spreadsheet-analysis。
---

# 数据批处理与转换

## When to use

- 需要对 JSON/CSV/纯文本做**批量转换、清洗、统计、抽样**。
- 需要把分析结果/学习内容整理成 **Markdown 报告**。
- 需要在工作区内批量重命名/整理文件。

> **不是本 Skill 的职责**：结构化表格 `.xls/.xlsx/.xlsb/.ods` 的探查/清洗/汇总/写出（→ `spreadsheet-analysis`）；文档类正文抽取与转换（`.doc/.docx/.rtf/.html` → `document-conversion`；`.pdf` → `pdf-processing`）。**CSV 归属裁决**：围绕"表"的读/洗/汇总/写出（inspect/clean/summarize/write）→ `spreadsheet-analysis`；作为批处理管道一环（`json_transform → csv_profile → make_report` 流水线）或最终产物是报告/JSON/重命名 → 本 Skill。

## 决策顺序

1. 先看输入形态：JSON 数组/对象 → `json_transform.py`；CSV → `csv_profile.py`；多文件重命名 → `batch_rename.py`；要报告 → `make_report.py`。
2. 大输入先抽样（`--limit`/`--max-rows`），避免输出过大。
3. 组合优先：`json_transform → csv_profile → make_report` 构成数据流水线。
4. 所有脚本只读写工作区相对路径；不扫描宿主路径。

## 脚本索引

| 脚本 | 用途 |
|---|---|
| `json_transform.py` | JSON 变换/过滤/选择字段（jq 式 CLI） |
| `csv_profile.py` | CSV 列统计与抽样 |
| `make_report.py` | Markdown 报告生成（把结构化内容写成 .md） |
| `batch_rename.py` | 工作区内批量重命名文件（安全） |

## 组合路线

```text
json ──json_transform──> 结果.json ──make_report──> 报告.md
csv ──csv_profile──> 统计.json ──make_report──> 报告.md
多文件 ──batch_rename──> 重命名后
```

## 安全与限制

- 离线运行，不联网、不执行包安装。
- `batch_rename` 只在工作区内重命名，禁止绝对路径/`..`。
- 输出遵守 64MB/256MB/180s 限额；大输入用 `--limit` 抽样。

## 详细说明

组合配方见 `references/best-combinations.md`，输入/输出契约见 `references/input-output-contract.md`，常见失败见 `references/troubleshooting.md`。每个脚本的完整用法见 `scripts/*.md`。
