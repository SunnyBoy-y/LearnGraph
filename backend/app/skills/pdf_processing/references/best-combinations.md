# 最佳组合（pdf-processing）

> 组合原则：脚本按文件路径接力。`pdf_extract_text` 产出 `.txt`，`pdf_merge`/`pdf_split` 产出 `.pdf`，`pdf_to_png` 产出 `.png`。

## 常见任务 → 脚本链

| 任务 | 脚本链 | 说明 |
|---|---|---|
| 读 PDF 正文做学习/图谱 | `pdf_extract_text.py` → 下游 | 先落盘文本再分析 |
| 合并多份讲义 | `pdf_merge.py` | 顺序合并 |
| 拆分大 PDF 为章节 | `pdf_split.py --start N --end M` | 按页范围 |
| 核对合并/拆分结果 | `pdf_merge.py` → `pdf_to_png.py` | 渲染首页核对 |
| PDF 首页缩略图 | `pdf_to_png.py --page 1` | 视觉验收 |
| 多份 PDF 抽正文汇总 | `pdf_extract_text.py`（逐个）→ `data-processing/make_report.py` | 每份生成 txt 再汇总报告 |

## 跨 Skill 组合示例

```text
用户给一份教材 PDF 和一页手写 docx
  1) pdf-processing/pdf_extract_text.py    → 教材正文 .txt
  2) document-conversion/docx_to_pdf.py    → 手写 .pdf
  3) pdf-processing/pdf_merge.py           → 合并.pdf（教材+附录）
  4) 正文 → graph-generation               → 结构入库
```

```text
把课程 PDF 转成便于手机阅读的 PNG 切片
  1) pdf-processing/pdf_split.py --start 1 --end 5   → 前5页.pdf
  2) pdf-processing/pdf_to_png.py                     → 每页 PNG（fitz 渲染）
```

## 选择依据

- 只要**文字** → `pdf_extract_text.py`（pypdf 无重渲染，最快）。
- 要**视觉**（扫描页/图表）→ `pdf_to_png.py`（fitz 渲染）。
- 要**结构操作**（合并/拆分）→ `pdf_merge.py`/`pdf_split.py`。
- 扫描版 PDF 无文字层 → 抽文本得空，改用渲染 PNG 走视觉（或提示 OCR 能力不可用）。
