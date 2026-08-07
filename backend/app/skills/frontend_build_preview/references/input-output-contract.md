# 输入/输出契约（frontend-build-preview）

## 支持的框架

| 模板 | 说明 |
|---|---|
| `plain-html` | 单个自包含 `index.html`（无构建步骤） |
| `react` | Vite + React 19（`@vitejs/plugin-react`） |
| `vue` | Vite + Vue 3（`@vitejs/plugin-vue`） |
| `vite-ts` | Vite + TypeScript（`tsx`/`typescript` 已预装） |

## 路径规则

- 项目路径为工作区内相对路径；拒绝绝对路径、`..`。
- 构建输出固定为 `<project>/dist/`（Vite 默认）。
- 覆盖已存在文件需 `--overwrite`。

## 通用 CLI

```text
scaffold_vite.py:     --dir <project-rel> --framework react|vue|plain-html|vite-ts [--title T] [--overwrite]
build_frontend.py:    --dir <project-rel> [--overwrite]
render_preview.py:    --dir <project-rel> [--output <png|pdf>] [--width --height --full-page] [--overwrite]
check_static_assets.py:--dir <project-rel> [--max-external N]
```

## stdout 约定

成功单行 JSON（`status:"ok"`、`output`、`files`/`bytes`/`sha256`）。
失败 stderr JSON + 非零退出码。

## 资源预算

- 产物 ≤ 64MB；单次任务输出 ≤ 256MB；wall-time ≤ 180s。
- 超大 bundle 会拖慢构建/渲染——精简后再构建。

## 成功判据

- `scaffold_vite`：目录含 `package.json` 与 `src/`（或 `index.html`）。
- `build_frontend`：`dist/index.html` 存在。
- `render_preview`：输出 PNG/PDF 可被打开。
- `check_static_assets`：`external_refs` 数量 ≤ 阈值（默认 0）。
