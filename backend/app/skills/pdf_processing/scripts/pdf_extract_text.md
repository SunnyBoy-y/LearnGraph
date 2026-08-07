# `pdf_extract_text.py` — PDF 正文抽取

> 抽取 PDF 全文或指定页的文本（PyMuPDF）。大 PDF 用 `--pages` 分段，避免超时。产物写文件，stdout 只放摘要。

## 用法

```bash
python scripts/pdf_extract_text.py --input inputs/教材.pdf --output outputs/教材.txt
python scripts/pdf_extract_text.py --input 教材.pdf --output 前5页.txt --pages 1-5
python scripts/pdf_extract_text.py --input 教材.pdf --output 指定页.txt --pages 1,3-4
```

## 输入 / 输出

- 输入：`.pdf`（非加密）。
- 输出：UTF-8 `.txt`。stdout 打印页数、抽取页数、chars、sha256。

## 参数

| 参数 | 说明 |
|---|---|
| `--pages` | 1 起的范围/逗号列表，如 `1-5`、`1,3-4`；空 = 全部 |

## 最佳组合

```text
pdf_extract_text ──> 正文.txt ──> graph-generation / 记忆 / 摘要
pdf_extract_text（每章）──> 多个 txt ──> data-processing/make_report 汇总
pdf_extract_text ──> document-conversion/extract_text 对 HTML 讲义同样处理
```

## 限制与失败

- 加密 PDF → 报错，无法离线解密。
- 扫描版无文字层 → 空文本；改 `pdf_to_png` 渲染走视觉。
- 页码越界自动裁剪（`warnings` 体现）。
