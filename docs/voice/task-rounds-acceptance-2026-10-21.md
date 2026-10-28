# 产物归属对话轮次验收

2026-10-21，部署至当前 18000 服务，镜像 `learngraph:task-rounds-20261021`。

任务按发起时的消息或语音轮次定位，排队、完成、打断和播报更新均保留原位置。实时快照与持久任务按任务 ID 合并。被合并的语音用户消息保留原轮次别名。旧语音任务缺少主聊天消息时，从现有语音日志恢复问题和时间，作为历史轮次插入，不挂在最新问题下。

验证：

- 轮次分配、语音合并和持久产物展开测试：21 项通过；真实聊天组件的轮次顺序回归：1 项通过。
- 发布源码类型检查、生产构建通过。构建保留依赖分块体积提示。
- 18000 真实会话显示 4 个任务，分属 3 个原始轮次；后续“你好”之后不再出现全局任务列表。
- 原生表格展开显示 32、64、128 三行；打断与已播放状态保留。
- 本次为前端布局修复，没有重新执行麦克风或 WebRTC 验收。

发布以已运行的 `voice-information-20261021-final` 为基线，只替换前端资源。427 个后端 Python 文件哈希一致；数据库现有行保留，Provider 配置与密钥表哈希一致；环境变量、挂载、端口及其他常驻服务未变。保留原回滚容器和已有在线数据库备份。

证据位于 `audit/task-rounds-20261021/`：`scoped-chat.diff`、`typecheck.log`、`build.log`、`deployment.json`、`preservation.json`、`browser.json`、`before.png`、`after-placement.png`、`after-expanded.png`。测试日志位于 `audit/task-rounds-tests.log`、`audit/task-rounds-chat-test.log`。
