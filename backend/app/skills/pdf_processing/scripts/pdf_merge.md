# `pdf_merge.py` — 合并 PDF

> 按 `--inputs` 给出的顺序把多份 PDF 合并成一份。内部用 pypdf `PdfWriter.append`。

## 用法

```bash
python scripts/pdf_merge.py --inputs a.pdf b.pdf c.pdf --output outputs/合并.pdf
python scripts/pdf_merge.py --inputs outputs/*.pdf --output outputs/all.pdf
```

> 注意：`--inputs` 顺序即合并顺序；用 shell 通配符时请先确认排序符合预期。

## 输入 / 输出

- 输入：一个或多个 `.pdf`（相对路径，空格分隔）。
- 输出：合并 `.pdf`。stdout 打印 bytes/sha256。

## 最佳组合

```text
docx_to_pdf / html_to_pdf ──> 多个 PDF ──pdf_merge──> 合订本
pdf_merge ──pdf_to_png──> 渲染首页核对合并结果
```

## 限制与失败

- 顺序错会导致文档颠倒——合并前核对清单。
- 输出已存在需 `--overwrite`。
