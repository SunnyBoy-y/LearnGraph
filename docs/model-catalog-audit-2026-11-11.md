# 默认模型目录更新与硬编码冲突审查

审查日期：2026-11-11。对象为当前工作区源码，不含 `.git`、测试临时目录、历史部署副本、构建产物和依赖。用户所称 `model.dev` 对应本项目实际使用的 `models.dev`。

> 这是首次审查时的历史记录，正文中的“仅报告、未修复”描述当时状态。后续已按用户要求实现修复，当前行为、跨通道覆盖和验证结果见 `model-catalog-fixes-2026-11-11.md`。

## 本次已执行的变更

- 更新 `backend/app/providers/models_dev_snapshot.json`，通过现有 `build_snapshot()` 构建，保留按模型名称合并、规范供应商优先级与字段白名单，不重写目录规则。
- 官方来源：`https://models.dev/api.json`，实际下载时间 `2026-11-11T06:21:59.037104+00:00`。
- 上游原始响应 SHA-256：`d821c11956a5e995ae246e98eb45c304dbd98e79bec9956a77b54dc258e744be`，写入快照 `_meta.source_sha256`，便于追溯。
- 旧快照：2026-07-26，172 个供应商、2,727 个去重模型。
- 新快照：235 个供应商、3,977 个去重模型。相对旧快照新增 1,932、移除 682，共同模型中 1,962 条记录变化；不是仅追加新型号。
- 新增维护脚本 `backend/scripts/update_models_dev_snapshot.py`，可在仓库根目录运行：

```bash
uv run --project backend python backend/scripts/update_models_dev_snapshot.py
```

- 新增离线测试 `backend/tests/unit/test_bundled_models_dev_snapshot.py`，验证目录一致性、全部模型默认能力可通过当前验证器、精确小数、来源哈希、空/无效/失败响应不覆盖旧快照、离线价格不用于计费。
- **未修改**用户/工作区/供应商配置、运行时缓存、模型选择与适配器业务逻辑。以下问题仅报告，没有擅自修复。

### 生效边界

`backend/app/providers/models_dev.py:171-183` 优先加载运行时网络缓存，之后才加载 bundled 快照。因此本次更新的是仓库默认离线目录，不代表正在运行的实例立刻切换目录；已有缓存的部署仍按原有每日刷新/手动刷新机制处理。离线目录仍标记 `origin=bundled`，不发布 `pricing_records`，不能用作实时计费依据。

## 发现汇总

P1 表示可能直接导致配置失效、能力不可用或不兼容请求；P2 表示功能语义/参数映射冲突。优先级不代表已发生线上事故。

| 编号 | 优先级 | 问题 | 验证方式 |
| --- | --- | --- | --- |
| C1 | P1 | 手动修改的模型参数可能被下次发现静默覆盖 | 前后端静态调用链 |
| C2 | P1 | 运行时把用户上下文使用上限重置为完整窗口 | 静态赋值链 |
| C3 | P1 | 第三方 Qwen 网关被强塞 DashScope 私有关闭参数 | 实际请求体构造函数离线复现 |
| C4 | P1 | DashScope 原生搜索切换丢失推理档位、状态保留和输出上限 | 实际请求体构造函数离线复现 |
| C5 | P1 | 前端供应商白名单漏掉后端支持的 Ollama Cloud | 前后端静态契约对比 |
| C6 | P2 | `gpt-5*` 前缀覆盖目录中可关闭推理的声明 | 新快照 + 实际解析函数离线复现 |
| C7 | P2 | Anthropic 数值思考预算被固定档位预算替换 | 实际请求体构造函数离线复现 |
| C8 | P2 | 组默认 API 接受配置，但运行时明确忽略 | API 静态链 + 实际合并函数离线复现 |
| C9 | P2 | 新会话回退到列表第一项，而非供应商显式默认模型 | 前端静态调用链 |
| C10 | P2 | 主聊天 Agent 门禁忽略模型级禁用工具能力 | 首次发送与重试静态对比 |

## 详细证据

### C1：人工参数修改没有切换来源标记，发现流程会撤销修改

- 位置：`frontend/src/features/settings/provider-pages.tsx:4794-4800,4820,4827-4833,4910`；保存辅助函数 `:4331-4341`；`backend/app/services/management.py:1826-1834,1970-1979`。
- 触发：编辑自动发现生成的 `official_catalog` 快照，或点击“填入官方默认参数”后再手动调整并保存。
- 冲突：前端保留 `capability_source`，人工字段变化只设置 `edited`；保存接口原样保留来源。同步仅保护 `user_declared/provider_probe/runtime_observation`，会重新覆盖仍为 `official_catalog` 的记录。
- 影响：保存成功的上下文、输出上限、推理映射等可能在下次发现时恢复目录值。聊天页自动调用发现，因此不必主动点击刷新也可能触发，见 `frontend/src/features/chat/chat-pages.tsx:3695-3699` 与 `backend/app/services/management.py:3443-3444`。
- 建议：明确区分“按官方快照跟随更新”与“用户已修改并固定”；人工编辑/保存后应以可靠的用户声明标记保护，而非单凭原快照来源判断。

### C2：用户上下文上限被硬性回写成完整窗口

- 位置：`backend/app/providers/factory.py:613-632,651-660`，尤其 `:620`。
- 触发：有效配置存在 `context_limit_tokens < context_window_tokens`，例如窗口 256,000、用户上限 64,000，或目录给出独立输入上限。
- 冲突：模型配置保留了独立上限，但工厂直接执行 `context_limit_tokens = context_window_tokens`，再把它传给 adapter 并回写 capability snapshot。相邻注释反而承诺保留用户选择的上限。
- 影响：用户节流/上下文控制失效；独立输入限制可能被突破。这里只证明预算输入被覆盖，不声称已发生产商拒绝。
- 建议：优先读取有效 `context_limit_tokens`，再用物理窗口夹紧，并覆盖工厂与下游预算消费者的回归测试。

### C3：按 Qwen 模型名前缀选择适配器后，私有参数泄漏到第三方网关

- 位置：`backend/app/providers/factory.py:500-505,733-747`；`backend/app/providers/remote/openai.py:1768-1773`。
- 触发：普通 `openai_compatible_chat` 中转端点、模型名前缀为 qwen/qwq、思考选择 off；包括用户已经声明 `reasoning_effort -> none` 的情形。
- 实测：在 `https://relay.invalid/v1` 上仅构造请求、不联网，得到：

```json
{"reasoning_effort":"none","enable_thinking":false}
```

- 冲突：显式映射之外无条件追加 `enable_thinking`；相邻 `enable_search`、`preserve_thinking` 都有 DashScope origin 检查，这里没有。
- 影响：不支持该私有字段的严格网关可能拒绝请求；不把离线构造等同于已验证实际 HTTP 400。
- 建议：私有字段由实际协议/端点能力决定，不能仅由模型名称决定；保留用户 wire 映射。

### C4：开启原生搜索后，请求重组丢失已有配置

- 位置：`backend/app/providers/remote/openai.py:1847-1856,1858-1895,1988-2007`。
- 触发：DashScope origin、开启原生搜索、纯文本且无函数工具的流式路径。
- 冲突：`_native_dashscope_body()` 先调用 `_apply_call_options()`，然后仅搬运 `enable_thinking/thinking_budget/max_tokens`；不搬运 `reasoning_effort/preserve_thinking`，也不调用 `_apply_output_budget()`。
- 实测：兼容请求构造存在 `reasoning_effort="max"` 和 `preserve_thinking=true`；同一 adapter 的原生搜索请求 parameters 二者皆无。即使 adapter 的 `max_output_tokens=65536`，该上限也不进入 native body。
- 影响：开启搜索前后，推理档位、状态保留与输出预算不再一致，可能退回上游默认。此处不主张把兼容参数原样搬入原生协议；需按原生字段规范映射或显式报不支持。
- 建议：统一生成语义参数，再做目标协议转换，增加搜索开关前后的参数完整性测试。

### C5：Ollama Cloud 被静态前端白名单过滤

- 位置：`frontend/src/types/providers.ts:194-206`；消费者 `frontend/src/features/chat/chat-pages.tsx:3523-3530`、`frontend/src/features/chat/use-rail-conversation-model.tsx:36-43`、`frontend/src/features/resources/document-chat-panel.tsx:286`。
- 对照：`backend/app/providers/catalog.py:596-608,664-666` 已把 `ollama_cloud` 作为可发现模型的供应商；工厂 `backend/app/providers/factory.py:719-720` 也有 adapter。
- 冲突：前端 `MODEL_PROVIDER_TYPES` 没有 `ollama_cloud`。
- 影响：可配置、后端可发现的供应商，在多个聊天模型选择入口被排除；目录数据更新无法修正此白名单。
- 建议：供应商“可选角色”和“Agent/structured 支持”分开判断；优先使用后端 catalog，避免简单把所有模型供应商等同于工具能力供应商。

### C6：宽泛 GPT 前缀强制 thinking-only，覆盖新目录的 none 档

- 位置：`backend/app/providers/models_dev.py:345-355,387-397,403-414,469-479`；`backend/app/providers/model_options.py:185-194`。
- 真实新目录证据：`models["gpt-5.1"].reasoning_options[0].values` 为 `["none","low","medium","high"]`；`gpt-5.2` 也包含 `none`。
- 冲突：`none` 被过滤、off 映射成 null，之后 `startswith(("gpt-5", "o1", "o3", "o4", "gpt-oss"))` 又无条件设置 `thinking_required=True`。
- 实测：使用本次 bundled 快照调用 `resolve_model_call_options({}, "gpt-5.1", thinking_mode="off")`，抛出 `ModelCapabilityError: The selected model only supports thinking mode; fast mode is unavailable`；`gpt-5.2` 相同。
- 额外边界：mock 目录 `reasoning=false` 时，该前缀仍强制恢复推理；无目录记录的相同前缀也能返回非空 overlay，而 `model_catalog.py:197` 只用 overlay 的真假标记 `models_dev_known`。
- 影响：目录刷新不能解锁这些模型的 off 档。完整用户级覆盖仍可改变配置，不能据此声称所有用户覆盖都失效。
- 建议：优先保留目录明确声明的 `none`，将真实不能关闭的协议限制缩小到有证据的模型与端点组合。本次未真实调用 OpenAI，证据是目录与本地策略矛盾。

### C7：Anthropic 保存的数值预算被固定档位值替代

- 位置：`backend/app/providers/remote/anthropic.py:217-252`；对照 `backend/app/providers/model_options.py:247-254`。
- 触发：传统 enabled+budget 分支，用户 `reasoning_parameter="thinking_budget"`，某档映射为正整数。
- 实测：使用 `claude-3-7-sonnet-20250219`，档位 low、实际预算 1234，`_apply_call_options()` 得到 `{"thinking":{"type":"enabled","budget_tokens":4000}}`，不是 1234。
- 冲突：adapter 把 actual 转为字符串，非固定档位则退回产品档位，再使用写死的 4000/10000/20000/32000；未消费数值预算及对应 provider_options。
- 影响：参数配置/trace 与真正 wire budget 不一致，用户预算控制失效。
- 建议：语义层明确 effort 与数值 budget 两类配置；协议层保留已声明的数值，并按目标模型实际限制验证。

### C8：组默认设置 API 成功保存，但 resolver 明确不读

- 位置：`backend/app/services/management.py:1861-1906`；`backend/app/providers/model_options.py:130-138`。
- 冲突：API 写 `model_defaults`、启用 `model_defaults_enabled`；resolver 注释和实现明确只认逐模型快照，不消费这些字段。
- 实测：将目录默认窗口 mock 为 256,000，传入启用的组默认窗口 64,000，实际合并结果仍为 256,000。
- 加重条件：`apply_to_all=true` 还会执行 `capabilities.pop("models", None)`，移除已存在且原本有效的逐模型设置，却不会让提交的组默认在 resolver 生效。
- 影响：API 成功语义与运行行为矛盾；apply-to-all 路径风险更高，应优先处理。
- 建议：既然产品策略改为逐模型配置，应禁用/迁移旧 API，或把组批量操作落实为逐模型写入；不建议未经确认恢复旧组模板优先级。

### C9：显式默认模型没有影响列表首选

- 位置：`frontend/src/features/chat/chat-pages.tsx:3883-3897`；`frontend/src/lib/model-choices.ts:98-120`；侧栏同类逻辑 `frontend/src/features/chat/use-rail-conversation-model.tsx:124-130`。
- 对照：设置页提供“设为默认”，见 `frontend/src/features/settings/provider-pages.tsx:1999-2007,4073-4081`；后端也保留有效默认值，见 `backend/app/services/management.py:3423-3436`。
- 触发：没有历史选择，或旧选择已无效，且配置默认模型不是可选列表第一项。
- 冲突：前端回退直接用 `modelOptions[0]`，列表辅助函数仅补入 default_model，不保证它排在第一项。
- 影响：实际提交其他模型；上游列表排序变化可以改变默认调用与成本。
- 建议：保留有效历史选择，其次选有效显式默认，最后才回退第一项。

### C10：主聊天 Agent 门禁与重试门禁不一致

- 位置：`frontend/src/features/chat/chat-pages.tsx:3759-3769,3975-3977,6057-6058`。
- 触发：供应商支持工具，但当前模型目录/人工配置明确 `supports_agent_tools=false`。
- 冲突：主聊天门禁只检查供应商级能力与 remote，重试门禁却额外检查模型级 `supports_agent_tools !== false`；`backend/app/providers/models_dev.py:458-459` 已正确输出模型级能力。
- 影响：首次发送仍可能提交 `agent_mode:true`，与目录声明、同页面重试行为不一致。未执行完整后端工具循环，不将其描述为已证实的上游报错。
- 建议：复用同一有效模型能力判定函数，让首次发送、重试、侧栏等入口一致。

## 验证与范围限制

离线回归命令，在 `backend` 目录执行：

```bash
uv run --extra test python -m pytest tests/unit/test_bundled_models_dev_snapshot.py tests/test_native_pricing_catalog.py tests/unit/test_output_cap_defaults.py tests/unit/test_model_pricing_sync.py -q
```

- 最终结果：38 项测试通过。
- 对全部 3,977 个去重模型执行 `validate_model_capability_update(catalog_capability_snapshot(model_id))`，无无效快照。
- C3/C4/C6/C7/C8 调用了实际纯解析/请求构造函数，使用 mock 或 `.invalid` 主机名，无真实模型请求、无真实凭据、无推理调用费用。
- 其余发现为可定位的静态代码矛盾，未执行浏览器端到端复现。38 项通过不意味着上述业务冲突已经修复；它们属于此次另行发现、尚未修改的行为。
- 没有把所有常量都当成问题：已评审厂商参数、可覆盖默认值、必要的协议转换、规范供应商排序本身不构成缺陷。本报告关注实际覆盖配置、遗漏字段或前后端契约矛盾。
- 移动端独立 `mobile/src` 未发现同类模型名/推理参数硬编码证据，不代表全项目所有硬编码都已穷尽。

## 建议修复顺序

1. 先保证用户配置不会丢失/失效：C1、C2，以及 C8 的 apply-to-all 路径。
2. 修复协议转换中的越界/丢参：C3、C4、C7。
3. 清理重复事实来源：C5、C6、C9、C10；以目录事实、用户显式配置、实际端点协议分别承担各自职责。
