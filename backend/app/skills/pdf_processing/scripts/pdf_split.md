# `pdf_split.py` — 按页范围拆分 PDF

> 把一份 PDF 的某一段页面（1 起、含端点）导出为新 PDF。可用于按章节拆分大文件。

## 用法

```bash
python scripts/pdf_split.py --input 教材.pdf --output 前5章.pdf --start 1 --end 40
python scripts/pdf_split.py --input 讲义.pdf --output 附录.pdf --start 45 --end 60
```

## 输入 / 输出

- 输入：`.pdf`。
- 输出：子集 `.pdf`。stdout 打印 `kept_pages`（如 `[1, 40]`）。

## 最佳组合

```text
pdf_split ──> 子集.pdf ──pdf_merge──> 重排/组合讲义
pdf_split ──> 子集.pdf ──pdf_to_png──> 章节封面
```

## 限制与失败

- 范围越界自动裁剪到实际页数；空范围报错。
- 加密 PDF 无法处理。
- 先用 `pdf_info` 确认页数再定范围。
