# 常见失败与处理（pptx-generation）

## 无法直接渲染 PPTX 为 PDF/PNG

- 现象：用户要“把 PPT 转成 PDF/图片”。
- 原因：镜像离线且不含 LibreOffice；`python-pptx` 只读写不渲染。
- 处理：用 `deck_to_html.py` 生成 HTML 预览，再 `document-conversion/html_to_pdf.py` 出 PDF。如实说明这是“文本级预览”，不伪装成原稿像素级渲染。

## 大纲 JSON 结构错误

- 现象：`build_deck.py` 报 schema 错误。
- 处理：按 `references/input-output-contract.md` 校验 `slides[].title/points/notes`；补齐缺失字段后重试。

## 中文字体问题

- python-pptx 用主题字体名，不内嵌字体；打开机器的 Office 若无中文字体可能回退。
- 处理：提示用户在本地打开时选择带 CJK 的字体；`deck_to_html.py` 的 HTML 用 Noto CJK，预览不受影响。

## 页数超限/超时

- 大纲太大导致生成超时或文件超大。
- 处理：拆成多份 PPT 或精简 `points`；先 `build_deck` 小样验收版式。

## 输出已存在

- 需 `--overwrite`。

## 错误退出码

- 非零退出 = 失败；stderr 有 `{status:"error", error}`。不把部分产物当成功。
