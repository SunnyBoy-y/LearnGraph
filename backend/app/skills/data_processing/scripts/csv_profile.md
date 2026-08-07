# `csv_profile.py` — CSV 概览

> 用 pandas 对 CSV 做概览：列、类型、行数、null 统计、样例行。适合任何 CSV 分析前的第一步。

## 用法

```bash
python scripts/csv_profile.py --input inputs/data.csv
python scripts/csv_profile.py --input data.csv --max-rows 10000 --sample 3
python scripts/csv_profile.py --input data.csv --encoding gbk
```

## 参数

| 参数 | 说明 |
|---|---|
| `--max-rows` | 限定统计行数（0=全部；大文件建议设上限防超时） |
| `--sample` | 样例行数（默认 5） |
| `--encoding` | 编码（默认自动 utf-8→gbk） |
| `--sep` | 分隔符（默认自动） |

## 输入 / 输出

- 输入：`.csv`/`.tsv`。
- 输出：stdout JSON（`columns/dtypes/nulls/sample`）。

## 最佳组合

```text
csv_profile ──> 决定 json_transform/clean_table 处理
csv_profile ──> 统计 → make_report 出报告
```

## 限制与失败

- 编码错误用 `--encoding gbk` 重试。
- 超大 CSV 用 `--max-rows` 限行，避免 wall-time 180s 超时。
