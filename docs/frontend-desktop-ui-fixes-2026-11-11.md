# LearnGraph 前端桌面交互修复记录

日期：2026-11-11。依据当前会话的 desktop-agent-ui 审查清单实施。保留现有 React、shadcn/Radix、Tailwind 和 Motion 体系，未重置仓库已有未提交改动，未修改本轮任务以外的后端功能。

## 修复清单

| 审查项 | 已实施方案 | 主要位置 |
| --- | --- | --- |
| 队列跨会话误投 | 每条消息绑定 workspaceId/sessionId，草稿与队列按会话/新会话所属项目隔离；发送前核验归属；异步失败回填仍回原会话；控制锁也按会话保存 | `frontend/src/features/chat/chat-pages.tsx`、`composer-session-state.ts` |
| 快速切换丢草稿 | 文本与引用元数据同步保存，取消原来的 800ms 丢尾字窗口；新会话也保存；临时草稿晋升后迁移，不因晋升重挂载 Composer | `composer-session-state.ts`、`chat-pages.tsx` |
| 附件切换与刷新丢失 | PromptInput 支持会话拥有的受控附件；File/Blob 通过 IndexedDB 保存，恢复时重建本地 URL；无法恢复或持久化失败时明确提示 | `components/ai-elements/prompt-input.tsx`、`composer-session-state.ts` |
| 编辑队列覆盖新草稿 | 改为队列消息原位编辑对话框，保留原条目的附件、表情、资料引用和参数；不覆盖主输入框；编辑过程中暂停自动派发 | `chat-pages.tsx` |
| 连续选择表情偶发遗漏 | 移除选择后会抢走新弹层焦点的延迟 rAF；由 Popover 关闭生命周期恢复输入焦点；添加与限量检查基于当前会话的原子状态；显式清除存储后拒绝旧异步 setter 污染新草稿 | `sticker-picker.tsx`、`chat-pages.tsx`、`composer-session-state.ts` |
| 假停止 | 明确请求停止中/停止未确认；取消 API 受理不等于终态，必须再读权威快照；失败时保留流与部分输出，并禁止依赖“已停止”的发送/队列动作；重试版本也使用真实终态 | `confirmed-cancellation.ts`、`chat-pages.tsx` |
| IME 抢键 | 将组合输入守卫放在外部菜单快捷键之前；覆盖原生 isComposing/keyCode 229；组合结束 500ms 内只消费一次伴生 Enter；保留 Shift+Enter 和后续普通发送 | `prompt-input.tsx` |
| 回看被正文拉走 | 正文首次出现不覆盖 MANUAL_READING；追加内容只提示新消息；主动回到最新后恢复持续跟随 | `use-conversation-scroll-controller.ts` |
| 右栏焦点及标签语义 | 使用现有 Radix Tabs 提供 roving focus、方向键、tab/tabpanel 关联；移除粗粒度阴影/边框覆盖对焦点的误伤，补充独立 focus-visible 轮廓 | `components/layout/workspace-shell.tsx`、`index.css` |
| 右栏入口平铺 | 当前学习为默认；侧边对话、会话文件保留直接入口；能力成长移至更多工作区菜单，选中时显示对应标签；语音调试未开启时不渲染其入口；新建动作放在 tablist 外 | `workspace-shell.tsx` |
| 窄窗导航模态焦点 | 保留常驻 DOM 与手势机制，增加 Tab/Shift+Tab 循环、背景 inert、关闭后焦点恢复；允许抽屉内触发的 Radix portal 正常交互 | `use-modal-drawer-focus.ts` |
| 分栏操作不完整 | 增加 Escape 恢复原宽、pointercancel/blur/卸载清理，键盘方向键/Home/End 调宽及 ARIA 数值，释放后边缘吸附；实际宽度与窗口空间约束一致 | `use-workspace-rail-resize.ts` |
| 微字号和层叠补丁 | 关键状态/上下文/右栏字号提升到至少 12px；新增复用既有语义色的工作区令牌；移除旧壳层白底/圆角/投影覆盖；附件卡片改平整表面与同心圆角 | `styles/workspace-tokens.css`、`index.css`、`shared/page-elements.tsx`、`shared/feature-model-select.tsx` |
| 阴影裁切 | 实际浏览器审计发现附件卡片装饰投影和暗色 Composer 外投影被裁切；移除这两处非浮层阴影，保留真实抽屉/弹层层级 | `index.css` |

## 重要行为约定

- 离开 A 时保留 A 的队列，不将其自动提交给正在查看的 B；回到 A 且权威时间线可用、没有在运行的回答后继续自动派发。
- 草稿包括文本、直接文件、资料库引用、表情、长粘贴、生成模式和图片比例；模型与思考偏好继续沿用现有会话偏好机制。
- 不支持 IndexedDB 或额度不足时不能承诺附件跨刷新恢复，因此显示明确提示；同一窗口内切换仍保留完整 File。没有使用 JSON 空对象冒充 File。
- 图谱节点预览仍不改变会话已保存的绑定节点；官方登录模型的官方搜索路由保持原逻辑。
- 修改表单字段的异步回调持有原会话 setter；会话切换后的工具栏回调重新绑定当前会话，避免新的闭包串扰。

## 自动化与构建

已执行前端全量 Vitest，以及针对最后改动的聊天、输入和工作区测试；最终数量与类型检查/构建结果见本文末的最终验收回执。

新增/扩展测试：

- `composer-safety.test.tsx`：A/B 队列隔离、立即切换文本/直接 File、新会话草稿、原位队列编辑、取消失败门禁、切换后的工具栏回调。
- `composer-session-state.test.tsx`：同步保存、迟到 setter 的归属、File 字节保持、项目草稿隔离及晋升迁移。
- `confirmed-cancellation.test.ts`：只受理未停止、取消失败、权威取消以及停止时已完成的竞争情形。
- `prompt-input-ime.test.tsx`：组合输入期间菜单键、伴生 Enter、Shift+Enter、原生组合状态及后续正常发送。
- `use-conversation-scroll-controller.test.tsx`：手动阅读优先、回到底部后的连续跟随、追加内容不移动读者。
- `workspace-interactions.test.tsx`：分栏键盘/取消/卸载/吸附、导航焦点循环及 inert 恢复。
- 保留并扩展 `rail-node-binding.test.tsx`，验证实际右栏 Tabs 与绑定隔离；更新旧测试夹具以覆盖当前条件时间线接口和新的原位编辑语义。

## 浏览器验收

使用本地 `desktop-ui-safety-preview.html`，加载真实 WorkspaceShell、ChatCanvasPage、PromptInput 与样式；数据和流均由隔离的开发夹具提供，不执行真实后端写操作。

1. 在 Chromium 中将草稿文本和真实 File 写入浏览器存储；重新加载页面后，文本、附件卡片均恢复，无“附件未能恢复”提示。IndexedDB 数据库为 `learngraph-composer`。
2. 确认右栏三个常驻标签实际计算字号均为 12px，更多工作区、新建侧边对话位于 tablist 外。
3. 使用 Skill 的 clip-audit 算法，检查整个 `.workspace-shell` 父范围（而非只检查卡片自身），并包含 viewport 边界与 hover 规则。
4. 明色/暗色、右栏关闭/展开四种最终状态均返回 `ok: true, cut: 0`。展开时的 `hidden: 3` 对应贴合窗口顶部、底部、右侧的抽屉阴影，属于预期物理边缘，不是半截投影。
5. 自动化浏览器标签当时为后台状态，CSS 时间线暂停；审计通过原生 Animation.finish() 明确落到最终状态。没有把这描述为真实连续动效已验收。

开发验收入口和审计算法位于 `frontend/src/dev/desktop-ui-safety-preview.tsx`、`desktop-clip-audit.js`，未加入生产路由。

## 未覆盖的人工边界

- 真实微软拼音/其他 CJK 输入法候选窗、触控板/触屏及不同 DPI 的手感仍需人工复核；合成事件测试不能替代系统输入法。
- 本轮未使用真实登录后端或真实模型执行停止故障注入；停止逻辑已通过可控失败测试，但没有宣称真实远端服务均已验收。
- 未在 macOS、透明原生桌面窗口进行验收。
- 仓库级 Lint 仍有 warning，大型第三方包仍有构建 chunk warning；本轮未通过关闭规则掩盖这些提醒，也未声称完成全仓技术债清理。

## 最终验收回执

- 最终前端全量测试：199 个测试文件、1,339 项测试全部通过，0 失败、0 跳过。执行命令：`npm run test -- --maxWorkers=2 --reporter=json --outputFile=node_modules/.cache/desktop-ui-test-results.json`。
- 连续选择表情的 9 项测试独立重复 5 轮，45 次断言用例均通过；新增“旧动画帧不得关闭刚重开的选择面板”和“旧草稿 setter 不得污染显式清除后重建的草稿”回归。
- `npm run typecheck` 通过。
- `npm run build` 通过，最终构建耗时约 5.33 秒；第三方大依赖保留原有 chunk 体积提醒。
- `npm run lint` 执行成功：0 errors、56 warnings；未禁用规则以隐藏历史或非阻断提醒。
- 本轮修改文件的 `git diff --check` 通过，仅存在 Git 的 CRLF/LF 提醒。
- 浏览器草稿/真实本地附件跨刷新恢复和明暗主题、右栏开关四个最终状态的 `cut: 0` 已验收；人工环境边界见上一节。
- 本轮临时补丁脚本已删除，验收预览服务未继续运行。所有修复保留在工作区，未擅自创建提交或发布。
