# 02 - 前端性能审计（Frontend Performance）

> 方法：代码审计（frontend-code 子智能体）+ 运行时实测（Playwright，dev 5174；生产构建因 P0-1 崩溃，**所有生产构建性能结论以 P0-1 阻断为前提**）。

## 1. 页面加载实测（dev 模式，3 次取中位）

| 页面 | 中位加载 | JS 文件数 | JS 传输 | TTFB | DCL |
|---|---|---|---|---|---|
| 登录页 | 1.19-1.28s | 190 | 41.9MB | ~3-5ms | ~0.7s |
| 首页（登录后） | 1.3s | — | — | — | — |
| 图谱页（300 节点真实图谱） | **1.5s**（至节点全渲染） | — | — | — | — |
| 图谱书架 / 资料 / 设置 / 记忆 | 1.17-1.35s | — | — | — | — |

- dev 模式每模块独立请求（190 个文件），冷启动受 vite 编译影响；**生产构建修复后必须复测**（当前无法对比）。
- 生产 dist：**32MB / 854 个 JS 文件**，多 chunk >800kB（vendor~index 931kB gzip 257kB、emacs-lisp 780kB×2、wasm 622kB×2 等）→ 见 F1-1/F1-2 与构建日志证据。

## 2. 代码级发现

### 包体与分包
- **F1.1 [P2/确认]** 图谱库 @xyflow/react 经 WorkspaceShell 静态 import 进入**首屏入口 chunk**（App.tsx:7 → workspace-shell.tsx:101-105），路由懒加载被架空。建议 workspace-shell 内三处 KnowledgeGraph 抽为 React.lazy。
- **F1.2 [P2/确认]** pdfjs-dist / exceljs / docx-preview 静态打入 chat 相关 chunk（document-previewers.tsx:10-19，经 file-preview → document-chat-panel/sandbox-file-artifact 引入）；recharts 全套静态进 chat（message-part-renderer.tsx:3-19，仅 ChartPart 使用）。无文档/图表也下载 0.5-1MB+。建议动态 import。
- F2.3 [P4] shiki 核心在 chat chunk（code-block.tsx:29-30），语言按需加载，可接受。
- F2.5 [P4] motion/pptx-preview 按需正常 ✅。

### 轮询与重复请求（运行时实测）
- **U1-2 [P3]** 登录后 30s 采样：`auth/me` 26次/分、`workspaces` 24次/分、`sessions` 14次/分、`settings` 14次/分、`projects` 12次/分、`graphs/goals/dashboard` 10次/分。来源：页面切换全量刷新 + AuthProvider 30s 心跳未随 hidden 暂停（auth-context.tsx:90-92）+ staleTime 偏小。建议心跳随 document.hidden 暂停、会话缓存、请求去重。
- F3.1 [P4] 聊天页沙箱就绪 5s 轮询（仅异常态，可接受）；F3.2 [P3] 流式重放轮询离开页面后继续（unmount 不 abort，设计性）；F3.3 [P3] 文档学习页 job+jobEvents 双 800ms 轮询可合并；F3.5 [P4] AuthProvider 30s 心跳未随隐藏暂停。
- **F3.7/F3.8 [✅]** SSE 去重+幂等+乐观回滚完备（api/sse.ts 按 event_id 去重、重连 Last-Event-ID、reconcilePersisted 重试对账、cancelled 标志）——这是本仓库最高质量实践之一。

### 渲染性能
- **F4.1 [P2/确认]** 消息列表无虚拟化，长会话 DOM 全量挂载。
- **F4.2 [P3/确认]** 图谱 onMove 每帧 setZoom 触发整组件重渲染（knowledge-graph.tsx:1005）；建议按 zoom 档位跨越时 setState。
- **F4.3 [✅]** 流式渲染 rAF 批处理 + 隐藏降级 + 后台 animate:false（chat-pages.tsx:1114-1160）——最佳实践。
- F4.4 [P3] markdown 流式每 delta 全量重解析（streamdown 全量解析），超长文本建议分片；F4.7 [P4] stream-stats 逐字符正则。
- F4.5/F4.6 [✅] Context 范围可控、消息级 memo 到位。

### 图谱专项
- **F5.1 [P3/确认]** 树布局每次变化双倍构建（knowledge-graph.tsx:559-572：skeleton + 完整布局两次 O(n)）。
- **F5.2 [P3/确认]** 折叠/展开全节点重建且节点组件未 memo（laidOutNodes data 重建 :672-692；nodeTypes 未 memo :383）→ 300 节点图谱折叠卡顿风险，与 U1-1 实测（节点选择 1.7s）相互印证。
- **F5.3 [P4]** 无 onlyRenderVisibleElements/MiniMap 全量渲染（1000+ 节点未压测，标记未测）。
- **F5.4 [✅]** 布局计算不进渲染循环（纯函数 + useMemo + positionOverrides 独立 + 120ms 防抖）。

### 内存与资源（运行时 + 代码）
- **内存循环实测 [✅]**：10 轮 图谱↔首页 切换，JSHeap 115→96→97→97→100→97→97→99→97→97 MB，**无单向增长**。
- F6.1-F6.4 [✅] createObjectURL 成对 revoke、监听器/定时器/WS 清理齐全、全局注册表有界（session-streams Map、stream-stats 200 上限）。

## 3. 结论
前端工程实践整体质量高（懒加载方向、流式 rAF 批处理、SSE 幂等、资源清理均为最佳实践），**无 P0/P1 级前端缺陷**；主要问题集中在包体（P2：F1.1/F1.2）与图谱/长会话交互（P2-P3：U1-1/F4.1/F5.2）以及轮询流量（P3：U1-2）。**生产构建崩溃（P0-1）是前端当前最大问题，但根因在构建链路而非页面代码**。
