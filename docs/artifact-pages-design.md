# LearnGraph 产物页面：运行时、布局与版本分享方案

## 结论

产物卡片必须被当作受控的子应用集合，而不是普通 DOM。宿主负责调度、暂停、回收和权限边界；卡片只通过受限 `postMessage` 协议声明自己已暂停。当前实现把卡片预览限制在视窗附近，并为每个 iframe 提供媒体、动画、定时器、`requestAnimationFrame` 和 `AudioContext` 的暂停通道；不响应协议的远程页面在 350ms 后被导航到空文档，恢复时重新加载，从而终止脚本、Worker、音频和网络活动。

## 运行时调度

状态按“全局意图 + 单卡片状态”合并：`运行`、`局部暂停`、`全局暂停`、`不可见回收`。顶部栏的暂停按钮调用 `pauseAllPreviews()`，页面切到后台由 Page Visibility API 自动暂停。只有由全局操作暂停的卡片会在“恢复全部”时恢复，用户单独暂停的卡片不会被误启动。

卡片距视窗 480px 以内才拉取预览数据并挂载 iframe；离开范围即卸载 iframe，React Query 只保留快照缓存。卡片和文件项使用 `content-visibility:auto` 与 intrinsic size，避免长列表的离屏布局和绘制。iframe 使用 `loading="lazy"` 作为浏览器级额外提示。页面不可见时不能只依赖 CSS `display:none`：该做法不会改变 iframe 内 document 的可见状态，因此由宿主显式发消息并在无响应时清空 browsing context。

子页面协议固定为 `{lg:1, kind:"preview.control", action:"pause"|"resume", requestId}`，子页回传 `preview.control.ack`。协议只影响自己的媒体和动画，不把宿主 DOM 暴露给不可信页面；iframe 仍使用现有 opaque-origin sandbox、CSP 和 capability bridge。

## 卡片与文件布局

卡片列表继续采用两列流式布局，卡片内部高度由内容决定。资料库网格改为 CSS columns 瀑布流：断点为 1/2/3/4 列，卡片 `break-inside:avoid`，图片使用自然高度 `object-contain`，文档/音视频使用稳定的 10rem 占位，避免图片被裁切或出现大块留白。CSS Grid Level 3 的 masonry 仍是工作草案，因此当前使用 columns 作为兼容实现；未来可在支持度足够时切换到原生 masonry，并保留 DOM 顺序和键盘顺序。

## 版本、发布与分享

版本号和发布状态是两个维度：

1. AI 首次生成或对卡片产生实质内容变更时，服务端立即保留一个 `draft` 修订并分配下一个整数版本号；只改变会话/实例等传输元数据不会产生新修订。
2. “发布”只把最新 draft 修订的状态改为 `active`，不会再生成一个新版本号。旧的 active 快照永远不改写。
3. 版本列表和分享令牌只针对 active 快照；默认预览仍显示最新草稿，版本与分享弹窗默认选最新已发布版本。
4. 分享令牌绑定 `card_version_id`，只能读取该冻结快照；撤销、过期和次数上限在服务端原子校验。

这与 Semantic Versioning 的不可变原则一致：已发布版本内容不能被修改，修改必须产生新版本（当前卡片界面继续显示 `v1`、`v2` 等简单序号；若未来对外暴露 API，再映射到 `MAJOR.MINOR.PATCH`）。工作区新增“已分享页面”页签，列出所有卡片分享、版本、查看次数和状态，并支持撤销；出于安全原因只显示令牌前缀。

## 会话粒度跳转

卡片保存 `chat_session_id` 与 `message_id`。点击“会话”携带 `targetMessageId` 路由状态；聊天页在首屏历史完成后定位 `conversation-jump-{messageId}`，平滑滚动并短暂高亮。若消息因分页尚未进入 DOM，最多重试约 1.4 秒后清理路由状态，避免重复跳转。

## 已落地文件

- `frontend/src/lib/preview-pause-controller.ts`：全局暂停注册表、页面可见性、iframe ACK/清空兜底。
- `frontend/src/lib/sandboxed-html-preview.ts`：沙箱内媒体、动画、定时器、rAF、AudioContext 控制 shim。
- `frontend/src/components/layout/workspace-shell.tsx`：全局暂停/恢复按钮。
- `frontend/src/components/chat/magic-card-host.tsx`、`sandbox-artifact.tsx`、`document-previewers.tsx`：iframe lazy loading 与暂停状态心跳处理。
- `frontend/src/features/artifacts/card-artifacts-panel.tsx`：视窗附近加载、最新已发布版本默认值、精确会话跳转。
- `frontend/src/features/artifacts/artifacts-page.tsx`：已分享页面管理页签。
- `frontend/src/features/resources/resource-pages.tsx`：默认瀑布网格、自然图片比例、离屏渲染优化。
- `frontend/src/features/chat/chat-pages.tsx`：按消息 ID 滚动定位。
- `backend/app/services/artifact_cards.py`：AI 实质修改自动创建 draft 修订，发布提升 draft 状态。
- `backend/app/api/routers/artifact_gateway.py`、`backend/app/domain/schemas/artifacts.py`、`frontend/src/api/artifacts.ts`、`frontend/src/types/artifacts.ts`：工作区分享管理 API 与类型。

## 验证与边界

`npm run typecheck`、`npm run build`、目标文件 `oxlint` 均通过；后端相关模块通过 `python -m py_compile`。当前环境未安装 pytest，无法运行后端测试套件。浏览器原生 masonry 尚未作为唯一实现，因为 W3C 规范仍处于草案阶段。对完全不响应 `postMessage` 的远程页面，恢复会重新加载 iframe，这是终止未知脚本所必需的可预测降级行为。

## 参考资料

- [MDN Page Visibility API](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API)
- [MDN Lazy loading](https://developer.mozilla.org/en-US/docs/Web/Performance/Guides/Lazy_loading)
- [MDN Intersection Observer API](https://developer.mozilla.org/en-US/docs/Web/API/Intersection_Observer_API)
- [web.dev content-visibility](https://web.dev/articles/content-visibility)
- [W3C CSS Grid Layout Module Level 3（masonry 草案）](https://www.w3.org/TR/2024/WD-css-grid-3-20240919/)
- [Semantic Versioning 2.0.0](https://semver.org/)
- [GitHub Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
