# `scaffold_vite.py` — 生成最小离线前端项目

> 在工作区生成一个离线可构建的最小 Vite 项目（react/vue/plain-html/vite-ts）。镜像已预装 vite/vue/react/typescript，**不联网、不 `npm install`**。

## 用法

```bash
python scripts/scaffold_vite.py --dir my-app --framework react --title "打卡页"
python scripts/scaffold_vite.py --dir plain --framework plain-html
```

## 参数

| 参数 | 说明 |
|---|---|
| `--dir` | 工作区内相对项目目录（必填） |
| `--framework` | `react\|vue\|plain-html\|vite-ts`（默认 plain-html） |
| `--title` | 页面标题（默认「离线页面」） |

## 生成内容

- `plain-html`：单个自包含 `index.html`（无构建步骤）。
- `react`/`vite-ts`：`package.json` + `src/main.tsx` + `vite.config.ts`。
- `vue`：`package.json` + `src/main.ts` + `vite.config.ts`。
- 均写入 `scaffold.lg` 标记，便于后续构建识别。

## 最佳组合

```text
scaffold_vite ──> 项目/ ──（用沙箱文件工具写页面）──> build_frontend ──> dist/
scaffold_vite(plain-html) ──> index.html ──> build_frontend（直接复制到 dist）
```

## 限制与失败

- 目标目录非空且无 `scaffold.lg` → 拒绝（需 `--overwrite` 或换路径）。
- 项目需要未预装依赖时无法离线构建——改用受支持模板。
