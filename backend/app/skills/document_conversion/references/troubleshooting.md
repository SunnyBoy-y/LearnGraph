# 常见失败与处理（document-conversion）

## antiword 失败（.doc）

- 现象：`antiword failed (exit N)`。
- 原因：文件其实不是 `.doc`（可能是 OOXML/HTML 伪装的），或包含 antiword 不支持的压缩。
- 处理：用 `file <input>` 确认真实类型；若是 OOXML 则按 `.docx` 走 mammoth；告知用户老格式可能丢失复杂版式。

## DOCX 图片/样式丢失

- mammoth 只提取文本+内联图片到 HTML；艺术字、文本框、SmartArt、复杂表格可能缺失。
- 处理：向用户说明“这是文本级转换”，若必须保版式则无法离线完成（镜像不含 LibreOffice），建议由 Agent 重建或仅交付文本版。

## HTML 里脚本/外链

- `html_to_pdf`/`html_to_png` 用本地 Chromium 渲染，但**不联网**；外链图片/字体/脚本会被跳过，页面可能不完整。
- 处理：提示用户内联资源或接受离线渲染结果；不得尝试放行公网。

## 中文乱码

- 镜像带 Noto CJK 字体；mammoth 输出的 HTML 已含 CJK 样式。
- 若 PDF 中仍乱码：确认输入 HTML 的 `<meta charset>` 正确；否则先用 `extract_text.py` 拿到正确文本再重建文档。

## 输出已存在

- 现象：`output already exists; pass --overwrite to replace`。
- 处理：确认目标确实要覆盖后加 `--overwrite`；不要用随机文件名规避。

## 超大文档超时

- wall-time 180s。PDF 渲染/大 HTML 可能超时。
- 处理：先 `extract_text.py` 分页/分块处理；需要转 PDF 时提示用户文档过大，建议拆分或只渲染部分页。

## 错误退出码

- 所有脚本非零退出 = 失败；stderr 有 JSON `{error}`。不要把非零退出当“部分成功”，如实报告。
