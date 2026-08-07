# `docx_to_html.py` — DOCX 转独立 HTML

> 把 `.docx` 转为带 CJK 字体样式的独立 HTML（mammoth）。产物可打印、可进 `html_to_pdf`/`html_to_png`。

## 用法

```bash
python scripts/docx_to_html.py --input inputs/讲义.docx --output outputs/讲义.html
python scripts/docx_to_html.py --input a.docx --output a.html --overwrite
```

## 输入 / 输出

- 输入：`.docx`。
- 输出：独立 HTML（`<meta charset>` + Noto CJK 样式 + 内联图片）。stdout 打印 chars/sha256。

## 最佳组合

```text
DOCX ──docx_to_html──> HTML ──html_to_pdf──> PDF（可打印/分享）
DOCX ──docx_to_html──> HTML ──html_to_png──> PNG（视觉验收）
DOCX ──docx_to_html──> HTML ──extract_text(--format html)──> 纯文本
```

## 限制与失败

- 文本框/艺术字/复杂表格会缺失（文本级转换）。
- 输出已存在需 `--overwrite`。
