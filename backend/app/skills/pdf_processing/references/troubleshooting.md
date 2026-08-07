# 常见失败与处理（pdf-processing）

## 加密 PDF

- 现象：`pdf_info` 返回 `encrypted: true` 或抽文本为空。
- 处理：如实告知“该 PDF 需要密码，无法离线解密”；不猜测密码、不伪造内容。可渲染封面 PNG 让用户确认是哪份。

## 扫描版 / 无文字层

- `pdf_extract_text` 得到空文本。
- 处理：用 `pdf_to_png.py` 渲染页面为 PNG 走视觉；若需求 OCR，说明镜像无 OCR 能力，不编造抽取结果。

## 页码越界

- `--pages 20-30` 但 PDF 只有 15 页。
- 处理：脚本会裁剪到实际范围并返回 `warnings`；Agent 应据 `pdf_info` 的页数先行校验。

## 大文件超时

- wall-time 180s。抽全文超时。
- 处理：先 `pdf_info`，用 `--pages` 分段抽取；合并大文件时拆分批次。

## 合并顺序错误

- `pdf_merge.py` 按 `--inputs` 顺序合并。传错顺序会得到颠倒的文档。
- 处理：先列出文件清单核对顺序，再合并；合并后用 `pdf_to_png` 渲染首页核对。

## 输出已存在

- 需 `--overwrite`；不要用随机文件名规避。

## 错误退出码

- 非零退出 = 失败；stderr 有 `{status:"error", error}`。如实报告，不把部分产物当成功。
