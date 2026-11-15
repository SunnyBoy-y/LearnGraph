# 模型目录冲突修复与跨通道验证

日期：2026-11-11。本文对应用户授权修复后的当前工作区；首次问题证据保留在 `model-catalog-audit-2026-11-11.md`。未提交 Git、未部署，也没有执行收费的真实模型推理。

## 用户可预期的行为

1. 保存单模型参数就是明确选择，后续自动发现不会把它恢复成目录值。人工保存的上下文长度也不会再因旧的“保守默认”标记被二次缩小。
2. 生效顺序为：显式单模型设置 > 启用的组默认 > 自动目录快照/目录默认。端点协议身份始终由当前供应商类型和 URL 决定，不允许模型名或旧快照覆盖。
3. 组“应用到全部”会原地更新已知模型，不删除逐模型快照及其无关元数据。普通保存组默认不覆盖用户单独设置的模型。
4. 选择模型时优先保留有效的历史选择，其次使用有效的供应商默认模型，最后才回退可用列表。隐藏、禁用、已经不在成功发现结果中的默认模型不会被重新塞回列表；显式添加的私有模型继续可用。
5. 思考能力和工具能力独立。不能调用工具的模型仍可思考；首次发送、重试、侧栏和文档问答不会给它发送 Agent 工具，附件解析、目标流程及沙箱预检使用相同的有效模式。
6. 用户上下文使用上限会传到实际 adapter；输出预算根据有效窗口收敛。供应商的物理窗口仍单独保留在能力快照中。
7. 目录声明的 `none` 推理档会保留，`gpt-5*` 等名字不再无条件强制 thinking-only；私有别名不会被伪装成目录已知模型。
8. 私有参数由端点协议决定，普通中转不会仅因模型名是 Qwen/DeepSeek 就被强塞其他厂商字段。用户显式声明的映射仍优先；无法转换的参数在本地明确报错，不静默丢弃。
9. 辅助任务的“优先极速”和用户明确选择“关闭思考”分开处理：标题、摘要、追问、翻译、子应用后台任务优先关闭思考；若模型只能思考，则使用最低可用档。用户显式 off 对 thinking-only 模型仍按能力规则拒绝。
10. “关闭搜索”能经过共享服务装配层传到模型。教学包预算被拒时改为更小的有限预算重试，不再取消预算字段进行无限额请求。

## 原报告十项问题的落点

| 问题 | 修复位置 | 当前行为 |
| --- | --- | --- |
| C1 人工参数被发现覆盖 | `services/management.py`、`settings/provider-pages.tsx` | 人工保存标记 `user_declared`，同步保留该快照，发现响应也返回保留下来的值 |
| C2 上下文上限被全窗口覆盖 | `providers/factory.py` | 主模型及视觉通道都解析模型级上限，传到 adapter，不再重置 |
| C3 第三方 Qwen 泄漏私有字段 | `providers/factory.py`、`model_catalog.py`、`model_options.py`、`remote/openai.py` | 模型名不决定协议；通用中转不会自动猜 `enable_thinking`；DashScope 私有默认受端点限制 |
| C4 搜索转换丢参数 | `remote/openai.py` | 原生搜索携带输出预算；需要精确 effort/preserve 控制时使用兼容搜索端点，保留原有参数 |
| C5 Ollama Cloud 不可选 | `schemas/management.py`、前端 `types/providers.ts` 及各选择器 | API 增加只读 role，前端优先消费；兼容旧服务器的回退清单也包括 Ollama Cloud |
| C6 宽泛 GPT 前缀强制思考 | `models_dev.py`、`model_catalog.py`、`model_options.py` | 按目录 effort/none 解析；自动目录缓存也在后台调用时更新，显式用户快照保持固定 |
| C7 Anthropic 数值预算丢失 | `remote/anthropic.py` | 数值预算原样采用，并在任务降低总输出预算时收敛；4.6 保留合法 legacy budget；真正 adaptive-only 的不兼容配置提前明确拒绝 |
| C8 组默认保存不生效 | `model_options.py`、`services/management.py` | 组默认参与实际解析，显式模型配置优先；应用全部不删除已有快照 |
| C9 默认模型被首项替代 | 前端 `lib/model-choices.ts` | 有效默认排在回退列表首位，各入口保留有效历史选择 |
| C10 模型禁用工具仍开启 Agent | 前端共享 gate、远程 adapter 实例能力、语音工具构建 | 首次发送/重试/文档/侧栏/语音一致；无工具模型保留思考，不再强制清空为极速 |

## 额外修复的调用旁路

### 语音生成

原 pipecat 直接用 OpenAI 客户端，实际丢失 factory adapter、模型预算和协议差异。本次增加 `voice/model_bridge.py`：

- Pipecat 消费标准 chunk，但真正网络请求由 factory 已选定的 `stream_chat()` adapter 完成；支持 Responses、Anthropic、DeepSeek、Qwen、通用 Chat、Ollama、Ollama Cloud、Copilot 和 Codex 生成协议。
- 保留模型自己的推理选项、端点、密钥、请求头及输出预算，避免重拼第二套 `extra_body`。
- 工具调用只发一次完整结果；保留原始 reasoning/response_items，供原生工具续传使用。
- 阻塞迭代放在线程中，使用有界队列；迭代器构造失败、生成失败和 close 失败均传递给消费者，不会无限等待。
- 取消会关闭传输、等待有界清理，并恢复临时输出预算；清理后的不可变 usage 在失败/打断时仍记录一次。
- 模型热切换在回合边界替换实际 provider，清空旧的协议续传状态，同步模型名与工具集；不再只改变显示名而仍请求旧模型。
- 工具 gate 同时覆盖初始化、热切换和 pipecat activation/reactivation，防止后续激活重新宣传已禁用的工具。

### 后台任务与视觉伴随

- 主模型和专用视觉 factory 都使用有效逐模型配置及上下文上限，禁用/隐藏的视觉模型不会继续被使用。
- Anthropic 请求构造检查当前总输出预算，因此记忆抽取将输出改为 4096、摘要改为 2048 后，不会继续下发超过该预算的 thinking budget。调整后的实际值记录在 adapter 的 `effective_reasoning_budget_tokens`。
- DeepSeek 和 Ollama 的独立流式入口补上了输出预算；Ollama Cloud 使用原生 `options.num_predict`，不混入 OpenAI token 字段。
- Ollama Cloud 补齐 `ModelProviderPort.stream_answer()`，旧式纯文本/后台调用也走同一原生请求与用量处理。
- 标题、活动摘要、推荐问题、听写文本整理、市场翻译和子应用事件后台调用增加“优先极速但兼容纯思考模型”的策略，同时关闭非任务所需的搜索。

### 协议边界保留

- Ollama Cloud 当前适配器仍明确拒绝不支持的结构化输出，没有为了统一选择器而伪造支持。
- DashScope 精确 effort/preserve 控制使用兼容模式搜索时，不承诺原生 `search_info` 来源列表和原生角标；这项传输取舍用于保留用户配置，避免原生重组静默丢参。
- Codex 登录端点按独立客户端契约处理：所有生成形态使用流式响应；结构化生成收集终态响应后再走共享 JSON 验证，字符串 input 转为结构化消息。
- **Codex 输出额度例外**：不向该登录端点发送公共 Responses 的 `max_output_tokens/max_completion_tokens/max_tokens`。adapter 明确记录 `capabilities.output_budget_enforcement="upstream_default"` 和 `output_budget_notice`；配置上限仅参与本地上下文/费用估算，不宣称已由上游强制执行。需要服务端强制单次输出上限时，应使用支持该字段的 API 通道。Codex 这一差异没有用“所有通道已经支持相同字段”掩盖。

## 通道覆盖矩阵

下表区分请求构造/接口测试与静态共享路径确认；没有逐一对全部在线模型做收费测试。

| 生成入口 | 实际实现路径 | 本次验证 |
| --- | --- | --- |
| 普通聊天、SSE、重试 | `chat_service_factory → factory → remote adapter` | HTTP 模式切换、附件发送、共享配置及协议请求矩阵 |
| Agent、工具回合 | 同一 factory/adapter，模型级 tool gate | 工具聚合/流式、工具 worker 装配、协议 tool 请求矩阵 |
| 侧栏、文档/资源问答、原生渲染追问 | 共享前端 model choices/gate；后端同一聊天服务 | 前端侧栏、文档与偏好恢复测试；共享请求构造 |
| 教学包、出题/批改、目标/图谱 | 功能模型路由 → factory | 教学包/练习路由、结构化生成、有界重试、目标图谱 HTTP 测试 |
| 标题、活动摘要、推荐问题、文本整理 | 聊天服务装配，`prefer_fast`、禁用搜索/Agent | 共享装配转发、会话标题、追问 anchor gate、模式切换 HTTP |
| 市场/技能翻译 | factory → generate_json | 路由目标、关闭搜索、优先极速与翻译回归 |
| 后台 Agent、子 Agent、子应用事件 | 共享 factory/聊天服务装配 | 子 Agent、工具 worker、子应用 HTTP；其余路径只读确认共享 |
| 记忆抽取/摘要、视觉伴随 | factory + 当前任务输出限制 | Anthropic 任务缩预算请求、专用视觉 factory、全部模型默认验证 |
| 前台全双工语音 | pipecat → provider bridge → 同一 remote adapter | 假 provider + 实际 pipecat 服务契约、工具续传、切模型、异常、取消、usage、激活 gate |

ASR、TTS、embedding 属独立音频/向量协议，未把文本推理字段应用到这些接口；现有语音相关回归保留。

## 验证结果与限制

- 后端相关回归套件：596 项通过。
- Codex 独立协议适配后再次定向验证：98 项通过（包含配置、四种生成形态协议矩阵、翻译及追问接口）。这是重复核验的一部分，不能与 596 相加作为不同测试数量。
- 前端：`npm run typecheck` 通过；8 个相关测试文件、64 项通过。覆盖同模型 ID 能力变化后真实发送标志更新、无工具模型保留思考且不进行沙箱预检、纯思考重试、文档/侧栏/设置及默认模型规则。
- `git diff --check` 无空白错误。
- HTTP 生成请求测试使用 `httpx.MockTransport`，语音使用假 provider 和实际已安装的 pipecat 接口；没有使用真实密钥，没有执行线上收费推理。
- 本轮测试仍输出第三方弃用警告，以及若干语音测试销毁/AsyncMock 的未 await 警告；不是零警告状态，相关回归未失败。没有将这些警告当作模型协议已在线验收的证据。
- 未运行全仓全部测试、未执行生产部署。第三方中转或厂商未来变更仍需针对实际环境验收；本次保证范围是已定位调用路径的一致配置/协议处理和本地回归，不是任意外部服务永不报不兼容。

## 回归入口

新增核心测试：

- `backend/tests/unit/test_model_configuration_contract.py`
- `backend/tests/unit/test_model_protocol_compatibility.py`
- `backend/tests/unit/test_voice_model_bridge.py`
- `frontend/src/lib/model-choices.test.ts`

后端集中复核可以先运行：

```bash
cd backend
uv run --extra test python -m pytest tests/unit/test_model_configuration_contract.py tests/unit/test_model_protocol_compatibility.py tests/unit/test_voice_model_bridge.py -q
```

前端复核：

```bash
cd frontend
npm run typecheck
npm test -- src/lib/model-choices.test.ts src/components/chat/retry-answer-menu.test.tsx src/features/chat/composer-prefs-restore.test.tsx src/features/chat/side-conversation-rail.test.tsx src/features/resources/document-chat-panel.test.tsx src/features/settings/model-overrides.test.tsx src/features/settings/provider-import-popover.test.tsx src/test/practice-model-setting-link.test.tsx
```
