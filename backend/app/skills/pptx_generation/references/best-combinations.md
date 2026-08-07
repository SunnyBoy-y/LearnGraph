# 最佳组合（pptx-generation）

> 组合原则：大纲 JSON 是唯一数据面；PPTX 是产物；HTML 是唯一可打印预览路径。

## 常见任务 → 脚本链

| 任务 | 脚本链 | 说明 |
|---|---|---|
| 从要点生成 PPT | 整理大纲 JSON → `build_deck.py` | 一次生成整份 |
| 读既有 PPT 内容 | `inspect_deck.py` | 每页文本 + 统计 |
| PPT 可打印版本 | `deck_to_html.py` → `document-conversion/html_to_pdf.py` | 无 LibreOffice 的唯一视觉路径 |
| PPT 内容转纯文本入库 | `inspect_deck.py` → `document-conversion/extract_text.py` 不需要 | 直接用 inspect 输出 |

## 跨 Skill 组合示例

```text
用户：把这次复习要点做成 10 页 PPT
  1) 根据会话内容整理 outline.json（title/points/notes）
  2) pptx-generation/build_deck.py  → 复习要点.pptx
  3) pptx-generation/deck_to_html.py → 预览.html
  4) document-conversion/html_to_pdf.py → 预览.pdf（给用户快速预览）
```

```text
用户给了一个旧 .pptx 想知道内容
  1) pptx-generation/inspect_deck.py → JSON 摘要
  2) 摘要文本 → 记忆/图谱
```

## 选择依据

- 生成用 `build_deck.py`；读取用 `inspect_deck.py`；可打印预览用 `deck_to_html.py`。
- 不要尝试直接“把 PPTX 转成 PDF”——镜像离线无 LibreOffice，`python-pptx` 不渲染。要走 `deck_to_html → html_to_pdf`。
