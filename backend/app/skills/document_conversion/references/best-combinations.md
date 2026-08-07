# 最佳组合（document-conversion）

> 组合原则：每个脚本只做一件事，通过**文件路径**接力，不互相隐式调用。上游把产物写到 `outputs/`，下游把该路径作为 `--input`。

## 常见任务 → 脚本链

| 任务 | 脚本链 | 说明 |
|---|---|---|
| 读取 DOCX 正文做分析 | `extract_text.py` → 后续 | 先落盘 UTF-8 文本，再交给分析/图谱 |
| DOCX 转 PDF | `docx_to_pdf.py` | 内部已做 mammoth→HTML→Chromium 打印 |
| DOCX 转可打印 HTML | `docx_to_html.py` | 生成带 CJK 字体样式的独立 HTML |
| DOCX → PDF → 合并多份 | `docx_to_pdf.py` → `pdf-processing/pdf_merge.py` | 先各自转 PDF，再合并 |
| HTML 转 PDF/PNG 验收 | `html_to_pdf.py` / `html_to_png.py` | 适合网页快照、表单打印、UI 验收 |
| RTF/DOC 转文本 | `extract_text.py --format rtf/doc` | 无 Office 时用 antiword/striprtf |
| HTML 抽取正文 | `extract_text.py --format html` | bs4 去除 script/style 后取文本 |

## 跨 Skill 组合示例

```text
用户上传一份 docx 课程讲义
  1) document-conversion/extract_text.py      → 讲义正文 .txt
  2) document-conversion/docx_to_pdf.py       → 讲义 .pdf
  3) pdf-processing/pdf_to_png.py             → 首页缩略图 .png（视觉核对）
  4) 正文文本 → graph-generation / 记忆        → 结构入库
```

```text
把一份本地 HTML 变成可分享 PDF
  1) document-conversion/html_to_pdf.py       → report.pdf
  2) archive-workspace/archive_create.py      → 把 pdf + 源 html 一起打包
```

## 选择依据

- 只要**文字** → `extract_text.py`（最快、最省资源）。
- 要**版式/打印** → `docx_to_pdf.py` / `html_to_pdf.py`（走 Chromium，CJK 字体齐全）。
- 要**视觉缩略图/验收截图** → `html_to_png.py`（1280×720 默认，可 `--width/--height`）。
- 输入为 `.doc`/`.rtf`（老格式）→ `extract_text.py --format doc|rtf`，不试图转 HTML（无 Office 转换器）。
