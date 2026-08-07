# `pdf_to_png.py` — PDF 页面渲染为 PNG

> 用 PyMuPDF 把 PDF 指定页渲染成 PNG，用于视觉验收、缩略图、扫描版阅读。产物可直接在对话展示。

## 用法

```bash
python scripts/pdf_to_png.py --input inputs/教材.pdf --output outputs/首页.png --page 1
python scripts/pdf_to_png.py --input 教材.pdf --output outputs/第3页.png --page 3 --dpi 200
```

## 输入 / 输出

- 输入：`.pdf`。
- 输出：`.png`。stdout 打印宽高/DPI/sha256。

## 参数

| 参数 | 说明 |
|---|---|
| `--page` | 1 起的页码（默认 1） |
| `--dpi` | 渲染 DPI（默认 150；打印用途可 200–300） |

## 最佳组合

```text
pdf_merge ──pdf_to_png──> 核对合并结果
pdf_split ──pdf_to_png──> 章节封面
扫描 PDF ──pdf_to_png──> 视觉阅读（无文字层时唯一路径）
```

## 限制与失败

- 只渲染单页；多页请循环调用（每次一个 `--output`）。
- 超大 DPI 会使 PNG 很大——注意输出 ≤256MB。
- 加密 PDF 无法处理。
