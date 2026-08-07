# `build_frontend.py` — 离线构建前端

> 用镜像预装依赖执行 `npm run build`（Vite）。**从不执行 `npm install`**（沙箱离线）。纯 HTML 项目直接复制到 `dist/`。

## 用法

```bash
python scripts/build_frontend.py --dir my-app
```

## 输入 / 输出

- 输入：含 `package.json`（Vite 项目）或单 `index.html`（plain-html）的工作区项目目录。
- 输出：`<dir>/dist/`。stdout 打印文件清单与每个文件 sha256。

## 前置

- Vite 项目需 `node_modules` 存在（镜像 `/node_modules` 提供 `vite`；项目 `package.json` 的依赖需在预装集中）。
- plain-html 无需构建。

## 最佳组合

```text
scaffold_vite ──> build_frontend ──> dist/ ──render_preview──> PNG/PDF
build_frontend ──> dist/ ──check_static_assets──> 自包含校验
build_frontend ──> dist/ ──(宿主 sandbox_publish_web_app)──> 分享
```

## 限制与失败

- 无 `node_modules` → 报错并提示不能离线安装。
- 构建未产出 `dist/index.html` → 报错。
- 构建可能因缺未预装依赖失败——如实说明，不伪造。
