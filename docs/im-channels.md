# LearnGraph 消息渠道：飞书私聊

首版只支持个人工作区里的飞书私聊文本问答。连接后在飞书发的问题、LearnGraph 生成的答案会保存到独立的「飞书 · 学习助手」会话。网页可以继续同一条会话；网页新消息不会主动推送到飞书。个人微信尚未开放。

## 自托管部署

1. 在飞书开放平台创建企业自建应用，启用机器人、允许发送消息和获取用户发给机器人的单聊消息。在「事件与回调」中选择长连接，添加 `im.message.receive_v1`。发布版本并确认应用可用范围覆盖测试用户。
2. 在宿主机根目录 `.env` 指定 `LEARNGRAPH_FEISHU_APP_ID` 和 `LEARNGRAPH_FEISHU_APP_SECRET`。不要把 Secret 发给机器人或提交到 Git。API 和监听进程必须使用同一个飞书应用和数据库。
3. 从包含此功能的源码构建并启动：`docker compose --profile feishu up -d --build`。只运行一个 `feishu-listener` 实例；不用公开回调端口。查看 `docker compose logs feishu-listener` 排查连接问题。源码部署可用 `uv sync --project backend --extra feishu`，并在与 API 相同的运行环境下从 `backend` 启动 `uv run --no-sync python -m app.services.im_feishu`。
4. 在 LearnGraph 切换到个人工作区，打开「设置 → 工具与扩展 → 消息渠道」，生成绑定码。10 分钟内将 `/bind …` 发给机器人私聊，然后发送一个文本问题。

## 验收及限制

- 飞书私聊收到答案；网页会话列表能看到同一轮问答。群聊、非文本消息忽略。
- 用两个不同用户验证身份和会话隔离。解绑后未发送的回复不再投递；已发给飞书的消息不会撤回。
- 重放同一事件只入队一次；监听收到消息后先提交数据库。生成和发回是两个任务，重试发送不会重跑模型。
- 「已配置」表示凭据已由部署方提供，不代表飞书 WebSocket 已连接成功。配置需用实际 Bot 和目标用户联调。
- 测试：从 `backend` 执行 `uv run --extra test pytest tests/test_im_channels.py`。
