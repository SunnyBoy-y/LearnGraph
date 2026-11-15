# 图谱侧栏交付验证

## 已获得的证据

- 隔离浏览器使用真实 WorkspaceShell、ChatCanvasPage、KnowledgeGraph；API 仅作用于内存测试夹具，无真实模型调用或用户数据写入。
- `docs/graph-sidebar-browser-tests.log` 中 `NAVIGATION_QA.pass = true`。八个断言场景覆盖书架导航、不创建空会话、重复点击、同名图谱、草稿恢复、节点学习隔离、局部图搜索及主按钮/更多层级。
- 该轮浏览器数据：3 个图谱文件夹，2 个会话，其中第 2 个会话仅由显式“学习此节点”创建；来源会话完整对象不变，没有 PATCH 请求。
- 当前节点完整落在侧栏画布可视范围内，卡片宽 236px；父节点在其上方 200px，不再把整张图缩成缩略图。
- 默认、真实悬停、菜单展开三种状态的 clip-audit 均为 `{ok:true, cut:0, hidden:0}`。
- 截图：`graph-sidebar-desktop-qa.png`、`graph-sidebar-menu-qa.png`。
- 初轮服务端 6 项测试通过，覆盖同名、改名、旧文件夹收编、无关文件夹不重绑、归档恢复、权限作用域及首次并发打开。此后补充了失效映射并发修复和节点分页游标测试。

## 未通过的全量门禁

- 全量 TypeScript 检查报告：`frontend/src/features/resources/resource-pages.tsx:693` 两处 `Cannot find name 'workspaceId'`。本轮没有修改该文件。
- 最新前端批跑中，绑定菜单 8 项、画布回归 16 项通过；其他 worker 出现 `Timeout waiting for worker to respond` / `Worker exited unexpectedly`，该轮不能计作完整通过。
- 扩展的聊天模式和草稿晋升回归出现超时，未作为通过项。
- 服务端后续完整重跑未取得完成回执，不能把新增测试当作已经执行通过。
- 浏览器关键断言已全部完成并写入日志，但浏览器退出阶段遇到进程超时，因此不把该 shell 命令记为正常退出成功。

## 运行与部署边界

本轮为源码修改与本地隔离验收，没有部署到线上。新增了服务端学习文件夹解析接口，需要运行更新后的后端；前端也应使用本轮代码。无需数据库结构迁移。

复现命令和完整行为说明见 `graph-sidebar-navigation.md`。不要把关键路径已通过等同于全量 CI 或所有平台验收已通过。
