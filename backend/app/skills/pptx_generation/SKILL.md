---
name: pptx-generation
description: 从结构化大纲 JSON 生成 PPTX、抽取幻灯片文本、转换为可打印 HTML 预览；处理演示文稿/幻灯片/slides/deck 相关需求。
---

# PPT 生成与检查

## When to use

- 用户要生成一份演示文稿（PPTX），已有大纲/要点/结论。
- 需要读取既有 `.pptx` 的幻灯片文本、统计页数、检查结构。
- 需要把 PPTX 内容变成可打印/可预览的 HTML（离线无 LibreOffice，不能直接渲染 PPTX 为 PDF/PNG）。

## 决策顺序

1. 生成：先整理一份**结构化大纲 JSON**（标题、要点、备注、可选分区），再 `build_deck.py` 生成 `.pptx`。
2. 读取：`inspect_deck.py` 抽取每页文本与形状统计。
3. 预览：`deck_to_html.py` 把幻灯片文本转成可打印 HTML，再交给 `document-conversion/html_to_pdf`（不能直接渲染 PPTX）。
4. 不联网、不下载模板字体；版式由脚本内置的简洁模板决定。

## 脚本索引

| 脚本 | 用途 |
|---|---|
| `build_deck.py` | 从大纲 JSON 生成 PPTX（python-pptx） |
| `inspect_deck.py` | 抽取 PPTX 幻灯片文本/形状统计 |
| `deck_to_html.py` | PPTX 文本 → 可打印 HTML 预览 |

## 组合路线

```text
大纲.json ──build_deck──> 报告.pptx
报告.pptx ──inspect_deck──> 文本统计
报告.pptx ──deck_to_html──> 预览.html ──(document-conversion html_to_pdf)──> 预览.pdf
```

## 安全与限制

- 离线运行，不联网、不执行包安装、不下载模板。
- 大纲 JSON 必须是受控结构（见 `references/input-output-contract.md`）；所有标题/要点来自用户提供或模型整理的内容，**不得编造幻灯片内容**。
- PPTX 无法离线直接渲染为 PDF/PNG（镜像不含 LibreOffice）——要交付视觉版请走 `deck_to_html → html_to_pdf`。

## 详细说明

组合配方见 `references/best-combinations.md`，输入/输出契约见 `references/input-output-contract.md`，常见失败见 `references/troubleshooting.md`。每个脚本的完整用法见 `scripts/*.md`。
