# AI 自主管理任务、Prompt 与多种触发器

## 设计参考与边界

本轮通过网络读取了 OpenAI 官方 Academy 的 Tasks 说明：
https://academy.openai.com/public/clubs/work-users-ynjqu/resources/tasks

官方材料中的设计要点：用户在聊天中描述“做什么、何时做”；支持未来一次或按计划执行；提出计划后在聊天中确认保存；可从任务列表管理，也能在原会话通过后续消息修改计划与内容；明确时区、结果格式、引用与安全边界。官方文档还描述推送/邮件交付，这是 ChatGPT 产品的能力，不代表 LearnGraph 自动获得相同的邮件/系统推送渠道。

同时尝试访问 OpenAI Help Center 的 Scheduled tasks in ChatGPT 页面，但此环境返回反爬挑战，未把无法读取的页面内容冒充为已验证资料。

LiveAgent 对照来自本会话实际 CronTaskManager 工具契约：持久任务定义、Prompt 执行、读/写/删/日志、启用状态、timeout_seconds 与 remaining_executions。LearnGraph 保留原五字段 Cron 兼容，同时补充直观的一次/间隔/每天/每周模式，而不是硬把现有任务改成六字段。没有操作开发机器的个人 Cron，也没有创建用户未要求的业务计划。

事件与条件/Webhook 是 LearnGraph 本轮扩展，不声称这是 ChatGPT Tasks 的现成功能。没有实现任意宿主 Bash、自定义 Python eval 或默认绕过授权。

## AI 可直接管理

`lg_scheduled_tasks` 在 Agent 模式常驻，不再仅藏在延迟能力目录。AI 可以：
- list / read：读取持久任务定义、下次时间、倒计时、剩余次数与执行结果；
- create / update：新增、修改 Prompt、时间模式、次数与条件；
- pause / resume / delete：暂停、恢复、删除；
- runs / run / cancel：读取日志、手动测试一次、请求取消当前执行；
- catalog：查询当前工作区已有的精确事件名称。

模型工具仍要求当前身份具备工作区管理权限，不能越权访问其他工作区。AI 新建任务记录 created_via=ai，并由运行时绑定来源会话。任务定义由模型和页面共享，不维护第二份隐藏副本。

创建成功后返回已持久化 ID、schedule_summary、next_run_at、next_trigger_in_seconds、remaining_executions。模型必须以真实工具成功结果确认，不能仅口头答应“已设置”。

## 模式

任务执行类型：prompt / skill / mcp。

时间触发：trigger_type=time
- once：run_at（必须含时区偏移）或 delay_seconds；保存时延迟被归一为绝对时间，只执行一次。
- interval：interval_seconds，至少 10 秒。
- daily：time_of_day 与 IANA timezone。
- weekly：time_of_day、weekdays（周一=0，周日=6）。
- cron：保留五字段 minute hour day month weekday。

有限次数：remaining_executions=X；null 持续执行；0 停用。次数消耗与领取自动执行在同一数据库 CAS 中完成，计的是自动执行尝试，不是仅成功次数。失败不会无声补跑。手动测试不消耗计划次数，防止测试操作改变周期承诺。

一次性完成、有限次数耗尽会自动关闭 enabled 并清空 next_run_at。恢复已耗尽任务前必须明确设置新的次数；不会点击启用就偷偷无限重跑。

倒计时：页面基于服务端 next_run_at 每秒显示“X天 X小时 X分 X秒后触发”；即将到期、已暂停、次数用完、等待事件分别显示，不把浏览器计时器当实际触发执行。实际由后台 worker 触发，默认轮询精度为既有 15 秒，可配置为 1–300 秒。关闭页面仍可运行，但服务停机时不运行；错过的时间周期继续使用合并一次、禁止自动重放副作用的策略。

## Prompt 实际执行

Prompt 不只是通知文案：触发后调用普通 ChatService.create_stream，由工作区已配置模型真正生成消息，并保留正常工具、计费、权限与消息持久化链路。

默认新建结果会话；可关联来源会话，或显式选择已有可写会话继续。可选择已配置的 provider_id/model_id、agent_mode 和 web_search。Prompt 超时允许 1–3600 秒；扩展任务仍最多 600 秒。

每次运行以 scheduled:{run_id} 绑定幂等提交键。新会话标记 scheduled_task 与任务/run ID。结果记录含 chat_session_id、message_id、文本，页面可以打开结果会话。

后台 watchdog 检查取消与截止时间并续租。取消走普通 MessageControl/消息取消，不新建模型调用，更不把“发出停止请求”伪装成已停止。若模型/工具要求额外授权，任务结果明确提示打开会话审批；不会自动替用户同意。

事件内容以非可信数据块附在 Prompt 后，不能变成 system 指令、扩大工作区权限或携带未审核密钥。

## 事件、条件与外部接入

- event：订阅工作区 AuditEvent 中一个精确 action，例如目录中真实存在的 file.upload。
- condition：同一事件流附加结构化谓词；只支持确定性比较，不执行代码。
- webhook：专用随机令牌认证的外部事件入口。

谓词路径支持 resource_id、resource_type、outcome、actor_id、payload.<key>；操作符 eq/ne/contains/in/gt/gte/lt/lte/exists；多个条件必须全部满足。

已有文件/图谱/会话等可识别资源会复核执行身份读权限，避免使用事件元数据绕过资源 ACL。事件输入有 16 KiB 上限、敏感字段拒绝/脱敏。订阅只消费创建/重新启用后的事件，持久游标、run 去重键和冷却时间防止同一事件重复执行；自动化定义的增删改审计不会被用来递归触发自己。

受认证的自定义事件：
`POST /api/v1/scheduled-tasks/events`

```json
{"event_type":"report_ready","event_id":"report-unique-id","payload":{"score":90}}
```

订阅名称为 event.report_ready。event_id 按工作区与事件类型去重。

Webhook：
1. 创建 trigger_type=webhook 的任务；
2. 在页面点击 Webhook，生成/轮换令牌；
3. POST /api/v1/scheduled-tasks/{id}/trigger，使用 X-Automation-Token 请求头；
4. 请求体仍为 event_type、payload 和可选 event_id。

只存令牌哈希，明文只在生成时显示一次；轮换立即撤销旧令牌。令牌不放 URL、不出现在模型任务列表或 Prompt。关闭/耗尽任务拒绝接收。Webhook 凭据只授权该任务，不是工作区通用 API 密钥。

## 数据与升级

新增字段保持旧表/旧 Cron 兼容。数据库修订 v1.17.0，增量迁移对已有 scheduled_extension_tasks / scheduled_extension_runs 补列并保留定义与执行历史。没有重建或清空生产数据。

任务数每工作区上限 200；后台仍复用已有 4 并发 worker、租约、revision CAS、单槽幂等与 dispatch CAS，防止多进程和重复回调重放。

## 本轮验证内容

最终结果：后端相关专项与安全回归 **202 passed**，前端相关回归 **27 passed**，既有真实 Docker/sandboxd 池化/非池化端到端 **2 passed**。TypeScript 类型检查、前端生产构建与差异空白检查均通过。构建保留既有大 chunk 提示，不影响成功。

浏览器以隔离的内存任务数据检查了列表、倒计时、每日 Prompt 表单、事件条件表单：1041×638 视口下对话框位于视口内、长表单可滚动、页面无横向溢出、所查交互文字最小 14px；临时预览代码与服务已删除/停止。未伪称进行系统推送、邮件发送或真实收费模型推理验收。

- 原有 Skill/MCP 调度兼容；
- AI 常驻工具与 list/read/create/update/pause/resume/delete，含 JSON Schema 内联引用验证；
- 一次延迟归一、有限次数原子耗尽、手动测试不消耗、每日/每周时区计算；
- 事件过滤、游标、工作区隔离、重复 event_id、Webhook 令牌轮换；
- Prompt 真实 ChatService 流和 Message 持久化（可控本地模型桩，不调用真实收费模型）；
- Prompt 取消不伪报成功、旧表增量迁移重复执行；
- 页面模式、X 次、Prompt、条件编辑、倒计时、来源标记与工作区切换。

OpenAI 官方推送/邮件作为参考，没有在 LearnGraph 中虚构已实现的外部通知渠道。当前交付以执行记录及结果会话为准。
