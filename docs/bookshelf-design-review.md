# 右侧图谱书架设计优化

调研日期：2026-11-14。范围：对话右侧的标签导航、书架搜索、图谱列表与主题可读性。

## 一手资料与适用范围

- OpenAI 官方《UI guidelines》：https://developers.openai.com/plugins/concepts/ui-guidelines 。参考其中 Visual design guidelines 的配色、字号、间距、图标与可访问性要求。这是 ChatGPT 内嵌应用指南；本项目迁移其视觉一致性原则，不照搬嵌入卡片的导航限制。
- Anthropic 官方 `frontend-design`：https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md 。参考其围绕真实任务建立层级、装饰须表达信息、谨慎管理 CSS 优先级、可见键盘焦点、克制非用户触发动效的原则。这是前端设计指导，不代表 Claude 产品的完整内部设计系统。
- X 官方《Accessibility features of X》：https://help.x.com/en/using-x/accessibility-features 。参考高对比度按钮、左对齐、键盘操作、减少动态效果及深色显示支持。这是公开可访问性说明，不把它表述为未公开的内部组件规范。

## 设计选择

| 依据 | 书架中的落地 |
| --- | --- |
| 系统颜色与单色线框图标 | 复用 foreground / muted / border 等现有 token；图标使用 18px Lucide 线框，34px 中性容器 |
| 信息层级与可扫读性 | 使用轻量列表，移除逐行重边框和悬浮位移；标题 13px，状态 12px，长标题最多两行 |
| 明确且一致的交互 | 保留整行打开，hover 与键盘焦点反馈；搜索加入清除按钮；展开按钮使用真实 aria-expanded |
| 低视觉噪声 | 标签仅保留一个选中指示；工具固定在右侧，只有标签区可横向滚动 |
| 真实状态与可恢复失败 | 加载时显示状态，失败时显示原因与重试，搜索无结果使用独立状态文案 |

## 图标根因

原 `project-bookshelf__spine` 同时属于导航图标和全局品牌色选择器。局部 `.context-rail .project-bookshelf__spine` 设置灰色笔画，优先级更高的 `html[data-color-mode='mono'] .project-bookshelf__spine` 却将底色设置为黑色，形成黑底灰线。

导航图标现在使用独立的 `project-bookshelf__icon`。背景与前景由同一处样式定义，不再加入品牌标记的主题覆盖规则，避免局部换色后被全局规则覆盖一半。

## 文件与验收

- 组件：`frontend/src/components/layout/workspace-shell.tsx` 中的 `ProjectBookshelf`。
- 样式：`frontend/src/components/layout/graph-rail.css`。
- 浏览器验证：`scripts/qa-bookshelf.cjs`，复用现有隔离 QA 页面，在模块加载前扩充内存数据，不访问真实模型、不写真实会话。

验证命令：

```sh
cd frontend
npm run typecheck
npm run test -- src/features/chat/rail-node-binding.test.tsx src/features/chat/side-conversation-rail.test.tsx src/lib/graph-rail-model.test.ts
npm run build
cd ..
npx --yes --package=playwright -c "node scripts/qa-bookshelf.cjs"
```

浏览器验证覆盖：6 种配色的浅色/深色模式、280/320/360/480px 侧栏、图标与状态文字对比度、搜索与清除、无结果、展开/收起、键盘焦点与裁切审计。验收结果见下方最终记录。
