---
name: frontend-build-preview
description: 离线创建 Vite/React/Vue 项目、构建静态产物并渲染 PNG/PDF 预览。
---

# 前端构建与预览

## When to use

- 用户要一个静态页面 / React / Vue 应用，并希望**离线生成可预览的构建产物**。
- 需要在沙箱内 `npm run build` 出 `dist/`，再渲染 PNG/PDF 做视觉验收，或准备发布。
- 需要把页面产物交给 `sandbox_publish_web_app` / `sandbox_publish_file` 分享。

## 决策顺序

1. 有源码：`build_frontend.py` 在项目目录执行构建（Vite/React/Vue/HTML 均可），产物在 `dist/`。
2. 无源码但只有想法：`scaffold_vite.py` 生成最小可构建项目（react/vue/html 模板），再构建。
3. 构建后：`render_preview.py` 把 `dist/index.html` 渲染为 PNG/PDF 视觉验收。
4. 发布：把 `dist/` 打包或用 `sandbox_publish_web_app` 分享（本 Skill 只产出，发布由宿主工具完成）。

## 发布为双向交互子应用

当产物是**多文件交互应用**（表单、练习、行程/学习规划器、问卷、自测题）且需要"用户操作 → Agent 回写状态"的循环时，用 `sandbox_publish_web_app` 并携带 `interaction_contract`，让它成为**双向交互子应用**（不是静态预览）：

1. 先 `sandbox_validate_web_app(output_root, entry_path)` 校验产物，拿到 `validation_id`。
2. 再 `sandbox_publish_web_app(validation_id, title, interaction_contract={...})`：
   - `event_schema`：描述用户能在应用里触发的动作。必须**顶层 object 且 `additionalProperties:false`**，例如 `{"type":"object","additionalProperties":false,"required":["question_id","selected"],"properties":{"question_id":{"type":"string"},"selected":{"type":"string"}}}`。
   - `state_schema`：描述 Agent 会通过 `subapp_patch_state` 写入的**完整状态**。同样必须是闭包 object，例如 `{"type":"object","additionalProperties":false,"required":["view","answers"],"properties":{"view":{"type":"string"},"answers":{"type":"object"}}}`。
   - 字段名要与子应用内的 `component.event` 载荷、`renderer.state` 快照保持一致。
3. 成功后返回 `subapp_mode:true` 与 `artifact_version_id`，聊天卡片会实例化为隔离 iframe 的双向子应用。
4. 运行中：用户操作会经 `subapp_observe` 被观察到；用 `subapp_patch_state(session_id, state, expected_version)` 推送新状态（先读当前版本，用乐观锁）。

**何时不用契约**：静态展示页面、单文件网页/图片、下载型产物一律不带 `interaction_contract`——它们走静态预览或 `sandbox_publish_file`。

## 脚本索引

| 脚本 | 用途 |
|---|---|
| `scaffold_vite.py` | 生成最小 Vite 项目（react/vue/plain-html） |
| `build_frontend.py` | 在项目目录执行构建（自动选命令） |
| `render_preview.py` | 把 `dist/index.html` 渲染为 PNG/PDF |
| `check_static_assets.py` | 校验 `dist/` 清单与无外链资源 |

## 组合路线

```text
scaffold_vite ──> 项目/ ──build_frontend──> dist/ ──render_preview──> preview.png
已有源码 ──build_frontend──> dist/ ──check_static_assets──> 清单.json
dist/ ──(宿主 sandbox_publish_web_app)──> 分享
```

## 安全与限制

- 离线构建：不联网、不执行 `npm install`（依赖已预装进镜像的 `/node_modules`）、不下载字体/CDN。
- 只允许构建工作区内相对路径的项目。
- 页面不得依赖外部资源（CSP/无外链约束）；`check_static_assets` 会校验。
- 构建产物遵守 64MB/256MB/180s 限额；超大 bundle 先精简再构建。

## 详细说明

组合配方见 `references/best-combinations.md`，输入/输出契约见 `references/input-output-contract.md`，常见失败见 `references/troubleshooting.md`。每个脚本的完整用法见 `scripts/*.md`。
