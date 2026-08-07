# 最佳组合（spreadsheet-analysis）

> 组合原则：探查先行，清洗后统计，写出收尾。所有脚本读工作区相对路径。

## 常见任务 → 脚本链

| 任务 | 脚本链 | 说明 |
|---|---|---|
| 摸清表格结构 | `inspect_table.py` | 列/类型/行数/样例 |
| 清洗后做统计 | `inspect → clean → summarize` | 先探查再决定清洗 |
| 产出干净 CSV 给下游 | `clean_table.py --output clean.csv` | 选列/去空/填值 |
| 把 JSON 行写成 Excel 报表 | `write_xlsx.py` | 表头+数据 |
| 多份 CSV 汇总 | `inspect`（各）→ `data-processing/json_transform` 合并 | 或 pandas 拼接 |

## 跨 Skill 组合示例

```text
用户给一份成绩单 xlsx，想要“每科平均分 + 按班级分组”
  1) spreadsheet-analysis/inspect_table.py    → 看列
  2) spreadsheet-analysis/summarize_table.py  → 数值 describe + 分组（按班级 groupby 输出）
  3) spreadsheet-analysis/write_xlsx.py       → 统计结果.xlsx
```

```text
课程数据表 → 前端图表
  1) spreadsheet-analysis/clean_table.py      → 干净 .csv
  2) data-processing/json_transform.py        → 图表用的 .json
  3) frontend-build-preview/build_frontend    → 渲染图表页
```

## 选择依据

- 只要看结构 → `inspect_table`。
- 要数值统计 → `summarize_table`。
- 要转换数据 → `clean_table`。
- 要生成 Excel 产物 → `write_xlsx`。
