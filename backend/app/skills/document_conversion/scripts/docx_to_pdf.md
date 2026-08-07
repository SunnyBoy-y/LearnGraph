# `docx_to_pdf.py` — DOCX 转 PDF

> 把 `.docx` 转成 PDF（mammoth → HTML → 本地 Chromium 打印），CJK 字体齐全。适合把讲义/报告交付为可打印文档。

## 用法

```bash
python scripts/docx_to_pdf.py --input inputs/讲义.docx --output outputs/讲义.pdf
```

## 输入 / 输出

- 输入：`.docx`。
- 输出：`.pdf`。stdout 打印 bytes/sha256。

## 最佳组合

```text
DOCX ──docx_to_pdf──> PDF ──pdf-processing/pdf_merge──> 合并讲义
DOCX ──docx_to_pdf──> PDF ──pdf-processing/pdf_to_png──> 首页缩略图
DOCX ──docx_to_pdf──> PDF ──archive-workspace/archive_create──> 打包交付
```

## 限制与失败

- 文本级转换：文本框/公式/复杂表格会简化。
- 需要保版式且离线不可达 → 如实说明（镜像不含 LibreOffice）。
- 输出已存在需 `--overwrite`。
