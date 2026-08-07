# `build_deck.py` — 从大纲 JSON 生成 PPTX

> 用 python-pptx 从结构化大纲生成简洁 16:9 幻灯片（标题+要点+备注）。大纲 JSON 是唯一数据面，不得凭空编造内容。

## 用法

```bash
python scripts/build_deck.py --input inputs/outline.json --output outputs/复习要点.pptx
```

## 大纲 JSON

```json
{
  "title": "复习要点",
  "slides": [
    { "title": "第一章 结论", "points": ["要点一", "要点二"], "notes": "备注" }
  ],
  "theme": { "accent": "C4472E", "title_size": 32, "point_size": 18 }
}
```

## 输入 / 输出

- 输入：`outline.json`。
- 输出：`.pptx`（≤100 页）。stdout 打印页数/bytes/sha256。

## 最佳组合

```text
整理要点 → outline.json → build_deck → 报告.pptx
build_deck ──deck_to_html──> 预览.html ──html_to_pdf──> 预览.pdf
```

## 限制与失败

- 不联网、不下载模板字体；版式为内置简洁模板。
- 中文字体不内嵌；本地打开时系统需有 CJK 字体。
- 大纲缺 `slides` 或页面无 `title` → 报错。
