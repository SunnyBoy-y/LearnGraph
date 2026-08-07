# 最佳组合（data-processing）

> 组合原则：数据脚本产出 JSON/CSV 文件，`make_report` 把结构化结果写成 Markdown 报告，供阅读/入库。

## 常见任务 → 脚本链

| 任务 | 脚本链 | 说明 |
|---|---|---|
| JSON 抽取/过滤 | `json_transform.py --select --filter` | jq 式 |
| CSV 概览 | `csv_profile.py` | 列统计+抽样 |
| 生成 Markdown 报告 | `make_report.py` | 标题/段落/表格 |
| 批量重命名素材 | `batch_rename.py` | 前缀/替换/序号 |
| 数据流水线 | `json_transform → csv_profile → make_report` | 端到端 |

## 跨 Skill 组合示例

```text
课程数据 → 分析报告
  1) spreadsheet-analysis/clean_table.py  → 干净.csv
  2) data-processing/csv_profile.py       → 统计.json
  3) data-processing/make_report.py       → 报告.md
  4) document-conversion/html_to_pdf      → 报告.pdf（如需打印）
```

```text
一堆截图文件整理
  1) data-processing/batch_rename.py --prefix lecture_ --pad 3
```

## 选择依据

- JSON 变换 → `json_transform`；CSV 概览 → `csv_profile`；报告 → `make_report`；文件重命名 → `batch_rename`。
- 需要打印版报告 → `make_report` 产物交给 `document-conversion/html_to_pdf`（先转 HTML 或直接 pdf 渲染 MD 需先转 HTML，推荐 `make_report --format html`）。
