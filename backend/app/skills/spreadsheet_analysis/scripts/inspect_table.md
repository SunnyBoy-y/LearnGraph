# `inspect_table.py` — 表格结构探查

> 读取 CSV/TSV/Excel（xlsx/xls/xlsb/ods），输出列名、类型、行数、sheet 列表和前几行样例。任何表格分析前先跑它。

## 用法

```bash
python scripts/inspect_table.py --input inputs/成绩.xlsx
python scripts/inspect_table.py --input inputs/成绩.xlsx --sheet "Sheet2"
python scripts/inspect_table.py --input inputs/data.csv --encoding gbk --sep ,
```

## 输入 / 输出

- 输入：`.csv/.tsv/.xlsx/.xls/.xlsb/.ods`。
- 输出：stdout JSON（`format/rows/columns/dtypes/sheets/sample`）。

## 参数

| 参数 | 说明 |
|---|---|
| `--sheet` | Excel sheet 名或索引（默认第一个） |
| `--encoding` | CSV 编码（默认自动 utf-8→gbk） |
| `--sep` | CSV 分隔符（默认自动） |
| `--rows` | 样例行数（默认 5） |

## 最佳组合

```text
inspect_table ──> 决定 clean 的列选择/类型
inspect_table ──> 决定 summarize 的分组字段
```

## 限制与失败

- 老格式加密/损坏会失败——如实说明。
- 超大表 `--rows 0` 可省样例输出。
