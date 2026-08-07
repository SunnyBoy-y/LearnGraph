# `clean_table.py` — 表格清洗/转换

> 对表格做选列、去空、填值、重命名、过滤，输出 CSV/TSV/XLSX。适合把脏数据整理成下游可用表格。

## 用法

```bash
python scripts/clean_table.py --input inputs/成绩.xlsx --output outputs/干净.csv \
  --columns 姓名 语文 数学 --dropna
python scripts/clean_table.py --input 成绩.xlsx --output 及格.xlsx \
  --filter-col 总分 --filter-mode notnull --fill 备注=无
python scripts/clean_table.py --input data.csv --output out.tsv --rename 姓名=name --sep ,
```

## 参数

| 参数 | 说明 |
|---|---|
| `--columns` | 保留的列（按顺序） |
| `--dropna` | 丢弃含空值的行 |
| `--fill COL=VAL` | 按列填充空值（可多个） |
| `--rename OLD=NEW` | 重命名列（可多个） |
| `--filter-col` + `--filter-mode eq\|ne\|notnull` + `--filter-value` | 行过滤 |

## 输入 / 输出

- 输入：CSV/TSV/Excel。
- 输出：`.csv`/`.tsv`/`.xlsx`。stdout 打印行数/列/sha256。

## 最佳组合

```text
inspect_table ──> 确认脏点 → clean_table 清洗
clean_table ──> 干净.csv ──summarize_table──> 统计
clean_table ──> 干净.xlsx ──write_xlsx 不再需要（产物已是表格）
```

## 限制与失败

- 不支持的输出格式报错（只支持 csv/tsv/xlsx）。
- 过滤模式只支持 `eq/ne/notnull`；复杂条件请在 Agent 层先说明再拆步。
