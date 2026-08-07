# `pdf_info.py` — PDF 元信息

> 读取页数、页面尺寸、加密状态与元数据标题，不抽取正文。任何 PDF 处理前先跑它决定策略。

## 用法

```bash
python scripts/pdf_info.py --input inputs/教材.pdf
```

## 输入 / 输出

- 输入：`.pdf`。
- 输出：stdout JSON——`page_count`、`encrypted`、`needs_password`、每页 `width/height`、`metadata`。

## 最佳组合

```text
pdf_info ──> 决定后续：抽文本 / 渲染 / 合并
pdf_info ──> 校验 pdf_split 的页码范围
pdf_info ──> 告知用户加密或扫描版限制
```

## 限制与失败

- 加密 PDF：`encrypted: true`，无法离线处理。
- 扫描 PDF：这里无正文可判断，需 `pdf_extract_text` 验证。
