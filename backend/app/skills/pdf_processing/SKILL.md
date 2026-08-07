---
name: pdf-processing
description: PDF 元信息、正文/页抽取、合并拆分、页面渲染为 PNG。
---

# PDF 解析与处理

## When to use

- 用户上传或引用了 `.pdf`，需要**读元信息、抽正文、按页提取、合并多份、拆分、渲染页面为图片**。
- 需要把 PDF 内容交给下游（图谱、记忆、报告、表格）之前先抽取文本。
- 需要视觉核对 PDF 页面（缩略图/截图）。

## 决策顺序

1. 先 `pdf_info.py` 拿到页数/大小/加密状态，决定能否处理（加密 PDF 需先说明无法离线解密）。
2. 只要文字 → `pdf_extract_text.py`（可 `--pages` 限定范围，避免大文件超时）。
3. 合并/拆分 → `pdf_merge.py` / `pdf_split.py`（操作整份 PDF，不解密内部文本）。
4. 视觉预览 → `pdf_to_png.py`（按页渲染 PNG）。
5. 组合优先：`pdf_extract_text → 下游`；`pdf_merge → pdf_to_png` 做合并后核对。

## 脚本索引

| 脚本 | 用途 |
|---|---|
| `pdf_info.py` | 页数、尺寸、加密、标题等元信息 |
| `pdf_extract_text.py` | 全文/指定页文本抽取（JSON + 文本文件） |
| `pdf_merge.py` | 按顺序合并多份 PDF |
| `pdf_split.py` | 按页范围拆分出一份新 PDF |
| `pdf_to_png.py` | 把一页渲染为 PNG（fitz） |

## 组合路线

```text
PDF ──pdf_info──> 元信息
PDF ──pdf_extract_text──> 正文.txt ──> 图谱/记忆/报告
PDF A + B ──pdf_merge──> 合并.pdf ──pdf_to_png──> 缩略图
PDF ──pdf_split──> 子集.pdf ──(document-conversion/html_to_pdf?)──> 重建/交付
```

## 安全与限制

- 所有脚本离线运行，不联网、不执行包安装。
- 输入输出限制在沙箱工作区内；覆盖输出需 `--overwrite`。
- 加密（需密码）的 PDF 无法离线处理——如实说明，不猜测密码。
- 大文件：先 `pdf_info` 看页数，抽文本用 `--pages` 分段，避免 wall-time 180s 超时。

## 详细说明

组合配方见 `references/best-combinations.md`，输入/输出契约见 `references/input-output-contract.md`，常见失败见 `references/troubleshooting.md`。每个脚本的完整用法见 `scripts/*.md`。
