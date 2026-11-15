# 通知与就地反馈实现

## 范围

继续复用项目现有 Sonner，不新增运行时组件库。Web 应用和 native-renderer 均挂载 `AppToaster`。生产通知调用统一从 `@/lib/notifications` 导入。开发预览仍可直接使用 Sonner，但不代表生产生命周期。

## 已落实

- 浮层使用项目 `--popover`、`--popover-foreground`、`--border` 和字体令牌，应用 `html.dark` 切换时即时同步。
- 桌面宽度 380px、12px 圆角、0.5px 边框、克制阴影。关闭按钮移到内部右上角，32px 热区，中文无障碍名称。
- 桌面默认位于顶部 72px、右侧 24px；可用 `--notification-top` 配合窗口外壳。窄屏 16px 留白和较小投影，避免阴影在屏幕边缘裁切。
- 进入 200ms、8px 位移；退出 140ms、6px 位移；无回弹。支持系统 `prefers-reduced-motion` 和 `[data-reduce-motion=true]`。
- 同屏最多两条；额外通知 FIFO 排队，直到实际显示才开始寿命计时。显式 id 可更新排队或正在显示的通知。
- 相同 scope、等级、标题和说明合并，避免重复堆叠；不同会话或对象可指定不同 scope。
- 成功 2800ms、普通信息 4000ms、警告 5500ms、错误 6000ms。带动作的通知默认常驻；显式 duration 可覆盖。
- 悬停沿用 Sonner 暂停；键盘焦点进入时额外锁定寿命，离开后提供完整阅读时间。Alt+T 进入通知；在通知内部 Escape 关闭并恢复原焦点，不影响下层对话框。
- 网络离线使用常驻状态提示，浏览器报告恢复后消失。不伪造恢复、不自动重放请求。
- 聊天模型、附件、绘图模式校验转到输入区附近；说明与输入控件通过 `aria-describedby` 关联。切换会话不接受旧回调写入新会话提示。
- 聊天主消息、回答、划词和侧栏回答的复制成功改为本地对勾，2500ms 后复位；剪贴板失败仍反馈，不伪报成功。
- 结束会话使用确认按钮处理中、就地状态和已结束说明，不再把等待当成错误。结束成功通知带会话标题。
- 单模型参数校验在原对话框显示；保存失败保留配置和原因，按钮改为“重试保存”，不自动重放。
- 封面降级改为“已使用默认封面”，说明短小并带图谱名称。定时任务的请求受理和停止请求采用信息级别，通知携带任务名；失败提供“查看任务”，不自动重试收费或有副作用的操作。

## 使用方式

```tsx
import { toast } from '@/lib/notifications'

toast.success('配置已保存', { scope: `${workspaceId}:${modelId}`, context: modelId })

// 在异步开始前捕获对象身份，不要从完成时的当前路由猜测归属。
const scope = `${workspaceId}:${task.id}`
const context = task.name
const notice = toast.loading('正在处理图谱变更', { scope, context })
try {
  await performChange()
} finally {
  toast.dismiss(notice)
}
```

操作型错误只添加真实存在且安全的动作。对“可能已提交但响应丢失”的写请求，优先让用户查看服务端状态，不新增盲目自动重试。表单已有稳定的错误位时，保留就地原因和原按钮重试，不再重复发全局错误。

## 验收证据

`frontend/notification-preview.html` 使用真实生产组件，不提交业务 API 请求。运行开发服务器后访问 `/notification-preview.html`。

`frontend/scripts/check-notifications.mjs` 使用隔离的 Windows Edge headless 实例，不操作用户已登录的浏览器。Playwright 可使用临时 npm package 或 `PLAYWRIGHT_MODULE` 指向已安装模块，无需加入运行时依赖。

已完成一次通过的浏览器验收，原始报告见 `docs/notification-qa.json`：

- 浅色/深色主题、长中文、380px 桌面和390px窄屏。
- 200% 缩放等效 CSS 视口（720×500），不是操作真实浏览器缩放菜单。
- 重复五次仍一条；连续五条同屏两条，手动关闭后下一条显示。
- 操作按钮生效、释放队列；Alt+T 和 Escape 恢复输入焦点。
- 实际悬停超过默认寿命仍保留；鼠标移开后键盘焦点继续暂停。
- 离线持续显示、联网清除；复制仅本地对勾、无全局成功弹窗。
- 系统减弱动效与显式减弱动效切换。
- 使用 desktop-agent-ui clip-audit 算法检查默认、实际 hover、两条展开状态和最终落点：所有记录 `cut: 0`、`hidden: 0`，无页面脚本错误。

实际截图：`notification-light.png`、`notification-dark.png`、`notification-narrow.png`。

## 验收边界

- 浏览器专项验收覆盖共享真实组件；不等于所有登录后业务与后端联调已完成。
- 尚未在 macOS、真实独立桌面壳及读屏软件中手动验收。
- 首批17项新增单测通过，相关业务首次分组回归62项通过、1项动作事件兼容失败；该事件问题已修复，native suggested prompts 后续8项通过。
- 最终轻量 Node 状态回归 `node scripts/check-notification-state.mjs` 通过：队列限制、FIFO 释放、相同对象去重、焦点暂停、停留时长、所属对象说明和常驻操作。
- 新组件定向 lint 为0错误；存在3项 Fast Refresh 导出结构警告。
- 实现中期 `tsc -b --pretty false` 曾通过；最终类型复验及构建串行命令在当前环境超时，没有拿到最终构建通过证据。
- 全量测试运行超时并存在多项失败/环境警告，未获得完整通过报告，不标记全量通过。后续分组也受到超时影响，需要在稳定环境重新运行。
