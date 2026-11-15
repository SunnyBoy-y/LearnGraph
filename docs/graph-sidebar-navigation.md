# 图谱文件夹导航与侧栏交互

## 行为契约

- 点击图谱只打开其学习文件夹，不修改来源会话的 graph_id、current_node_id、project_id、草稿或附件。
- 有可访问的历史学习会话时恢复它；没有历史时进入 `chat/new?project=...`，浏览本身不创建空会话。
- 图谱名称只用于展示。服务端使用工作区内的图谱 ID → 项目 ID 持久映射，改名或重名不改变归属。
- 学习不同节点先解析目标图谱文件夹，再恢复该节点的学习会话或创建目标会话，不要求解绑旧会话。
- “继续学习”可以复用该节点已有会话；显式问答、拆分等任务创建独立目标会话，保留来源上下文。
- 连续重复点击合并同一在途请求；切换图谱或手动导航后，旧请求不得抢回页面。

## 服务端

`POST /graphs/{graph_id}/learning-folder` 返回 `ProjectView`，不创建或更新会话。

映射保存在已有 `WorkspaceSetting`，键为 `learning.graph-folder.{graph_id}`，无需数据库结构迁移。旧文件夹只按 `primary_graph_id` 收编；多个候选按创建时间和 ID 确定默认项，随后固定具体项目 ID。不会根据同名标题重绑无关项目。

首次并发打开利用工作区设置键的唯一约束仲裁；失效映射修复采用 updated_at 比较交换，竞争失败回滚新文件夹后重试。已有映射指向归档文件夹时，在权限允许的前提下恢复原文件夹。

会话恢复使用现有分页接口，限定 project_id、graph_id、session_kind，节点学习额外限定 node_id。只读取一个有界页面及必要的详情，不扫描全量会话。node_id 纳入游标作用域校验。

## 侧栏层级

- 固定区：图谱名、文件夹、切换图谱、节点搜索、回到当前节点、完整图谱。
- 图谱区：默认当前节点及一阶关系，常规邻居最多 12 个，额外节点按需展开；保持可读缩放，使用纵向局部布局。
- 详情区：节点名、预览/当前学习标记、简短定义，一个学习主按钮和一个“更多”。
- 低频动作统一放“更多”：下一节点、讲解、对比、自测、误区、历史、掌握、重点、拆分、编辑。
- 书架默认显示最近使用的 6 个图谱；搜索或“查看全部”后显示其余项。
- 输入区标签始终反映持久会话绑定，不能被侧栏预览覆盖；标签内保留明确的绑定管理入口。
- 路由 learningNode 仅在与目标会话持久绑定一致时消费一次，移除该状态字段时保留其他待处理意图。

## 验证入口

- 前端：`npm --prefix frontend test -- --maxWorkers=1 src/features/chat/rail-node-binding.test.tsx src/lib/graph-folder-navigation.test.ts src/lib/graph-rail-model.test.ts src/lib/session-graph-binding.test.tsx src/components/graph/graph-regressions.test.tsx src/features/chat/composer-session-state.test.tsx src/api/session-browser.test.ts`
- 后端：在 backend 下执行 `uv run pytest tests/unit/test_graph_learning_folder.py tests/unit/test_session_browser.py -q`。
- 隔离浏览器页面：`frontend/graph-sidebar-qa.html`，使用真实外壳、对话与图谱组件，所有 API 写入只作用于页面内存夹具，不调用真实模型或修改用户数据。
- 浏览器验收：先在 frontend 启动 Vite 监听 127.0.0.1:5201，再执行 `npx --yes --package=playwright -c "node scripts/qa-graph-sidebar.cjs"`；使用已安装的 Microsoft Edge 无头模式。
- 日志：`graph-sidebar-frontend-tests.log`、`graph-sidebar-backend-tests.log`、`graph-sidebar-browser-tests.log`。

全量类型检查另受当前工作区 `frontend/src/features/resources/resource-pages.tsx:693` 的未定义 workspaceId 错误阻塞；该文件不属于本轮图谱改动范围。扩展聊天模式回归还出现超时，不能将其计作通过。
