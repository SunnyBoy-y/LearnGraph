# LearnGraph 消息渠道：飞书与个人微信私聊

首版支持个人工作区里的**飞书**与**个人微信（ClawBot / iLink）**私聊文本问答。连接后在飞书或微信发送的问题、LearnGraph 生成的答案会分别保存到独立的「飞书 · 学习助手」与「微信 · 学习助手」会话。网页可以继续同一条会话；网页新消息不会主动推送到 IM 客户端。

## 飞书接入

### 方式一：扫码一键接入（推荐）

1. 在 LearnGraph 切换到个人工作区，打开「设置 → 工具与扩展 → 消息渠道 → 飞书」。
2. 点击「扫码一键接入」，页面通过飞书官方设备授权接口（`https://accounts.feishu.cn/oauth/v1/app/registration`）生成授权二维码与确认链接。
3. 使用手机飞书扫码（或在桌面浏览器打开链接）并确认创建个人助手应用，系统会自动配置机器人能力、消息权限及 `im.message.receive_v1` 长连接事件。
4. 授权完成后，LearnGraph 自动加密保存应用凭据并尝试自动绑定当前飞书账号；若未自动绑定，点击「生成绑定码」并在 10 分钟内将 `/bind …` 私聊发给机器人即可。

### 方式二：手动配置 / 自托管环境变量

1. 在飞书开放平台创建企业自建应用，启用机器人、允许发送消息和获取用户发给机器人的单聊消息。在「事件与回调」中选择长连接，添加 `im.message.receive_v1`。发布版本并确认应用可用范围覆盖测试用户。
2. 可直接在「设置 → 工具与扩展 → 消息渠道 → 飞书 → 手动配置 App ID / Secret」填入凭据（由 API 内置监听管理器自动拉起监听），或在宿主机根目录 `.env` 指定 `LEARNGRAPH_FEISHU_APP_ID` 和 `LEARNGRAPH_FEISHU_APP_SECRET` 并启动 `docker compose --profile feishu up -d --build`。不要把 Secret 发给机器人或提交到 Git。
3. 源码部署可用 `uv sync --project backend --extra feishu`，并在与 API 相同的运行环境下从 `backend` 启动 `uv run --no-sync python -m app.services.im_feishu`。
4. 在 LearnGraph 切换到个人工作区，生成绑定码并在 10 分钟内将 `/bind …` 发给机器人私聊。

## 个人微信接入（ClawBot / iLink）

1. 在 LearnGraph 切换到个人工作区，打开「设置 → 工具与扩展 → 消息渠道 → 个人微信」。
2. 点击「扫码登录微信」，后端向微信 iLink Bot 接口申请登录二维码并在界面渲染。
3. 使用手机微信「扫一扫」扫码并确认登录授权；授权完成后自动加密保存凭据并绑定当前微信账号，后端自动启动长轮询监听接收私聊文本消息并回发答案。
4. 若微信侧登录态失效（`errcode = -14`），页面状态会提示「登录已失效」，重新扫码即可恢复。可随时点击「退出登录」断开连接并清除凭据。

## 验收及限制

- 飞书与微信私聊均可收到答案；网页会话列表能看到对应的「飞书 · 学习助手」或「微信 · 学习助手」会话。群聊、非文本消息忽略。
- 用两个不同用户验证身份和会话隔离。解绑或退出登录后未发送的回复不再投递；已发出的消息不会撤回。
- 重放同一事件只入队一次；监听收到消息后先提交数据库。生成和发回是两个任务，重试发送不会重跑模型。
- 网页端「设置 → 工具与扩展 → 消息渠道」内置可随时查看的飞书与微信接入教程弹窗。
- 测试：
  - 后端：`uv run --project backend --extra test pytest backend/tests/test_im_channels.py`
  - 前端：`npm --prefix frontend run test -- src/features/settings/im-channels-settings.test.tsx`
