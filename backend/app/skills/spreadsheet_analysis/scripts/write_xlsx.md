# `write_xlsx.py` — 结构化行写入 XLSX

> 把一份 JSON 行数据写成带样式的 `.xlsx` 报表。输入可为 `[{"col": val}, ...]` 或 `{"columns": [...], "rows": [...]}`。

## 用法

```bash
python scripts/write_xlsx.py --input outputs/统计.json --output outputs/报表.xlsx
```

## 输入 JSON

```json
{ "columns": ["姓名", "语文", "数学"],
  "rows": [["张三", 90, 88], ["李四", 85, 92]] }
```

或

```json
[ { "姓名": "张三", "语文": 90, "数学": 88 } ]
```

## 输入 / 输出

- 输入：rows JSON。
- 输出：`.xlsx`（表头加粗+底色，自动列宽）。stdout 打印行列/sha256。

## 最佳组合

```text
summarize_table / clean_table ──> 数据 ──write_xlsx──> 报表.xlsx
data-processing/json_transform ──> 转换结果 ──write_xlsx──> 交付表格
```

## 限制与失败

- 行长度与列数不匹配 → 报错。
- 无法推断列名（空表）→ 报错；请显式给 `columns`。
