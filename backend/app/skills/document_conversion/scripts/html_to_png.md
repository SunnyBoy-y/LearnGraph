# `html_to_png.py` — 本地 HTML 转 PNG 截图

> 用 headless Chromium 把本地 HTML 截成 PNG，用于快速视觉验收、缩略图、UI 走查。产物可直接在对话里作为图片展示。

## 用法

```bash
python scripts/html_to_png.py --input outputs/page.html --output outputs/page.png
python scripts/html_to_png.py --input page.html --output full.png --full-page --width 800 --height 600
```

## 输入 / 输出

- 输入：本地 `.html`。
- 输出：`.png`。stdout 打印 bytes/尺寸/sha256。

## 参数

| 参数 | 说明 |
|---|---|
| `--width` / `--height` | 视口尺寸（默认 1280×720） |
| `--full-page` | 截整页（默认只截视口） |

## 最佳组合

```text
前端构建产物 ──html_to_png──> 视觉验收 PNG
DOCX ──docx_to_html──> HTML ──html_to_png──> 缩略图
```

## 限制与失败

- 外链资源离线不加载。
- 页面超长且 `--full-page` 可能超时/超输出上限，可只截首屏。
