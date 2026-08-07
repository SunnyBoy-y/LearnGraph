# `summarize_table.py` — 表格统计

> 用 pandas 对表格做探查统计：describe、null 计数、唯一值、数值摘要、可选按列分组计数。输出 JSON。

## 用法

```bash
python scripts/summarize_table.py --input inputs/成绩.xlsx
python scripts/summarize_table.py --input 成绩.xlsx --groupby 班级
python scripts/summarize_table.py --input data.csv --encoding gbk --max-cols 20
```

## 输入 / 输出

- 输入：CSV/TSV/Excel。
- 输出：stdout JSON（`dtypes/nulls/unique/numeric/group_counts`）。

## 参数

| 参数 | 说明 |
|---|---|
| `--groupby` | 按某列统计分组计数（前 20 组） |
| `--max-cols` | 限制统计列数（默认 40），防止超大表超时 |

## 最佳组合

```text
inspect_table ──> 确认列 → summarize_table 统计
summarize_table（groupby）──> 分组均值/计数 ──> write_xlsx 出报表
```

## 限制与失败

- 数值列才有 `numeric` 摘要；object 列只有 null/unique。
- 超大列数被 `--max-cols` 裁剪并标注 `columns_summarized`。
