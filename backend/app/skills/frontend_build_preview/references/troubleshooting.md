# 常见失败与处理（frontend-build-preview）

## 构建报 `vite` 找不到依赖

- 原因：依赖未预装（新包）。
- 处理：镜像已预装 vite/vue/react/typescript/tsx 等；若仍报缺，说明该项目需要未预装依赖，**不要 `npm install`（沙箱离线）**。改用受支持模板或内联实现。

## 外链资源被禁用

- `check_static_assets` 报 `external_refs`。
- 处理：把图片/CSS/字体内联（`vite-plugin-singlefile` 可用）或移入项目内；预览/发布必须自包含。

## 构建超时

- wall-time 180s。
- 处理：精简代码/依赖，或跳过 `render_preview` 只交付 `dist/`。

## `dist/index.html` 缺失

- 原因：项目不是 Vite 结构或构建失败。
- 处理：用 `scaffold_vite` 重建最小项目；如实说明构建结果，不伪造。

## 渲染空白页

- `render_preview` 出的 PNG 全空白。
- 处理：先 `check_static_assets` 看是否有外链/脚本错误；把页面改成纯本地渲染再试。

## 输出已存在

- 需 `--overwrite`。

## 错误退出码

- 非零退出 = 失败；stderr 有 `{status:"error", error}`。不把部分产物当成功。
