# `render_preview.py` — 渲染构建产物预览

> 把 `dist/index.html` 用 headless Chromium 渲染为 PNG 或 PDF，用于视觉验收。产物可显示给用户或入文档。

## 用法

```bash
python scripts/render_preview.py --dir my-app --format png
python scripts/render_preview.py --dir my-app --output preview.pdf --format pdf
python scripts/render_preview.py --dir my-app --full-page --width 900
```

## 参数

| 参数 | 说明 |
|---|---|
| `--format` | `png\|pdf`（默认 png） |
| `--output` | 输出路径（默认 `<dir>/preview.png`） |
| `--width/--height` | PNG 视口（默认 1280×720） |
| `--full-page` | PNG 截整页 |

## 输入 / 输出

- 输入：`<dir>/dist/index.html`（先 `build_frontend`）。
- 输出：PNG/PDF。stdout 打印 bytes/sha256。

## 最佳组合

```text
build_frontend ──render_preview──> preview.png（给用户看）
render_preview(pdf) ──> 打印/分享版本
```

## 限制与失败

- `dist/index.html` 缺失 → 先构建。
- 外链资源离线不加载（见 `check_static_assets`）。
- 超大页面渲染可能超时——可只截首屏。
