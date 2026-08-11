---
name: document-conversion
description: DOC/DOCX/RTF/HTML（Word/Office）文档转 HTML、纯文本、PDF、PNG 预览，并抽取正文供下游分析；PDF/表格/JSON 不属本 Skill（→ pdf-processing / spreadsheet-analysis / data-processing）。
---

# 文档转换与文本抽取

## When to use

- 用户上传或引用了 `.doc` / `.docx` / `.rtf` / `.html` 文件，需要**读取正文、转 PDF、转图片、转可打印页面**。
- 需要把某份 Word/网页内容喂给下游 Skill（PDF 合并、表格分析、前端预览、知识图谱）之前先抽取文本。
- 需要把一段本地 HTML 渲染成 PDF 或 PNG 截图做验收。

> **不是本 Skill 的职责**：`.pdf` 的解析/合并/拆分/渲染（→ `pdf-processing`）；表格 `.csv/.xls/.xlsx/.ods` 的探查与清洗（→ `spreadsheet-analysis`）；JSON/文本批量流水线与报告（→ `data-processing`）。本 Skill 只管 DOC/DOCX/RTF/HTML 类文档的转换与正文抽取。输入是 `.pdf` 时不要在这里用 HTML 链路硬转，交给 pdf-processing。

## 决策顺序

1. 先确认输入格式和期望输出：纯文本 → `extract_text.py`；HTML 渲染 → `html_to_pdf.py` / `html_to_png.py`；DOCX→PDF → `docx_to_pdf.py`；DOCX→HTML → `docx_to_html.py`。
2. 输入必须是工作区内相对路径（`inputs/...` 或 `...`），禁止绝对路径与 `..`。输出到 `outputs/...`。
3. 先 `extract_text.py` 拿到正文，再做下游分析；只有需要视觉/打印版本时才转 PDF/PNG。
4. 组合优先，不重复实现：`docx_to_html → html_to_pdf → html_to_png` 构成一条完整链路。

## 脚本索引

| 脚本 | 用途 |
|---|---|
| `extract_text.py` | DOC/DOCX/RTF/HTML → UTF-8 纯文本 |
| `docx_to_html.py` | DOCX → 独立 HTML（含中文字体样式） |
| `docx_to_pdf.py` | DOCX → PDF（mammoth → Chromium 打印） |
| `html_to_pdf.py` | 本地 HTML → PDF |
| `html_to_png.py` | 本地 HTML → PNG 截图 |

## 组合路线

```text
DOCX ──extract_text──> 正文 ──> 下游分析/图谱
DOCX ──docx_to_html──> HTML ──html_to_pdf──> PDF
HTML ──html_to_pdf──> PDF  ──(pdf-processing)──> 合并/拆分
HTML ──html_to_png──> PNG  ──> 视觉验收
```

## 安全与限制

- 所有脚本离线运行；不联网、不执行包安装、不读取 secret。
- 输入输出都限制在沙箱工作区内；覆盖输出需显式 `--overwrite`。
- 单文件输出遵守镜像限额（单文件 ≤64MB、输出 ≤256MB、wall-time ≤180s）。
- 只支持镜像内已有的解析器：antiword(.doc)、mammoth(.docx)、striprtf(.rtf)、BeautifulSoup/lxml(.html)。
- 若需要把正文继续处理，先保存文本文件，再交给其他 Skill 的脚本；不要用 stdout 传大文本。

## 详细说明

组合配方见 `references/best-combinations.md`，输入/输出契约见 `references/input-output-contract.md`，常见失败见 `references/troubleshooting.md`。每个脚本的完整用法与参数见对应 `scripts/*.md`。
