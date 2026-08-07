# `make_report.py` — Markdown/HTML 报告生成

> 从结构化内容 JSON 生成 Markdown 或自包含 HTML 报告。适合把分析结果/学习内容整理成可读报告，HTML 版可直接交给 `document-conversion/html_to_pdf` 打印。

## 用法

```bash
python scripts/make_report.py --input outputs/content.json --output outputs/报告.md
python scripts/make_report.py --input content.json --output 报告.html --format html
```

## 内容 JSON

```json
{
  "title": "学习报告",
  "sections": [
    { "heading": "掌握情况",
      "paragraphs": ["本周完成 5 个知识点。"],
      "bullets": ["数学", "语文"],
      "table": { "columns": ["知识点", "掌握度"], "rows": [["导数", "80%"]] } }
  ]
}
```

## 参数

| 参数 | 说明 |
|---|---|
| `--format` | `md\|html`（默认按输出扩展名推断） |
| `--title` | 覆盖报告标题 |

## 输入 / 输出

- 输入：内容 JSON。
- 输出：`.md` 或自包含 `.html`。stdout 打印格式/chars/sha256。

## 最佳组合

```text
json_transform / csv_profile / summarize_table ──> 数据 ──make_report──> 报告.md
make_report(html) ──> document-conversion/html_to_pdf ──> 报告.pdf
```

## 限制与失败

- `sections` 为空或内容为空 → 报错（不生成空报告）。
- HTML 版自包含（CJK 样式、无外链），可安全预览/打印。
