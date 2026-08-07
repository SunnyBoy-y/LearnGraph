# `deck_to_html.py` — PPTX 转可打印 HTML 预览

> 把 PPTX 的文本内容（标题/要点/备注）转成自包含、CJK 友好的 HTML。离线无 LibreOffice，因此这是“PPT → 可打印/可预览”的唯一路径，产物再交给 `html_to_pdf`/`html_to_png`。

## 用法

```bash
python scripts/deck_to_html.py --input inputs/报告.pptx --output outputs/预览.html
python scripts/deck_to_html.py --input 报告.pptx --output 预览.html --title "周会报告"
```

## 输入 / 输出

- 输入：`.pptx`。
- 输出：自包含 `.html`。stdout 打印 chars/sha256。

## 最佳组合

```text
build_deck ──deck_to_html──> 预览.html ──document-conversion/html_to_pdf──> 预览.pdf
deck_to_html ──> 预览.html ──document-conversion/html_to_png──> 缩略图
```

## 限制与失败

- 只含文本，不含图片/图表/复杂版式；这是“内容预览”，不是像素级渲染。
- 外链资源不加载（自包含 HTML）。
