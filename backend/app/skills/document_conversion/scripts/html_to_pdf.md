# `html_to_pdf.py` — 本地 HTML 转 PDF

> 用镜像内 headless Chromium 把本地 HTML 渲染成 PDF。适合网页快照、表单打印、文档预览、前端构建产物打印。

## 用法

```bash
python scripts/html_to_pdf.py --input outputs/page.html --output outputs/page.pdf
```

## 输入 / 输出

- 输入：本地 `.html`（应自包含；外链资源在离线沙箱中被跳过）。
- 输出：`.pdf`。stdout 打印 bytes/sha256。

## 最佳组合

```text
docx_to_html ──> HTML ──html_to_pdf──> PDF
html_to_png   ──> PNG 用于快速预览；PDF 用于打印/交付
frontend-build-preview/build_frontend ──> dist/index.html ──html_to_pdf──> PDF 验收
```

## 限制与失败

- 不联网：`<img src="https://...">`、外部 CSS/字体/脚本不会加载。
- 页面很大可能超时（wall-time 180s）——先拆分或精简。
- 输出已存在需 `--overwrite`。
