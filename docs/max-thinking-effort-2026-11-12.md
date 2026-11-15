# 最高思考档与能力映射

按本轮审核实施：独立保留 xhigh（极高），新增 max（最高）；滑条不显示当前模型不支持的档位。

## 能力契约

- 产品顺序为 off / low / medium / high / xhigh / max；max 不再在 Agent 归一化中变成 xhigh。
- 枚举型能力保留原生同名子集，排序去重；不再把五个原生档位比例压缩成四档。
- models.dev reasoning_options 同时读取 toggle、effort、budget_tokens，保留完整控制元数据；选择当前协议可执行的控制方式，而不是只取第一项。
- reviewed DashScope 映射不与官方直连档位混合。models.dev 当前主记录仍沿现有供应商优先级确定，不合并不同供应商的原生能力集合。
- 仅开关型能力只提供单个开启档；未知模型不自动宣传五档，可在高级设置中显式声明。
- 通用中转不从预算/开关控制猜测厂商 effort 枚举。用户手动映射依旧优先。
- Token 预算保留旧低/中/高预算，将原顶端预算归为 max，xhigh 使用中间预算；实际输出预算依旧限制最终下发值。

## 映射检查

使用当前 bundled 目录调用 catalog_capability_snapshot()，并只读核对线上 models.dev/api.json：

| 模型与通道 | 思考档位 |
| --- | --- |
| gpt-5.4 / Responses | low, medium, high, xhigh |
| gpt-6-astra / Responses | low, medium, high, xhigh, max |
| claude-opus-4-6 / Messages | low, medium, high, max |
| claude-opus-4-7 / Messages | low, medium, high, xhigh, max |
| deepseek-v4-pro / 官方直连 | low, high, max |
| deepseek-v4-pro / reviewed DashScope | high, max |
| qwen3.8-max-preview / reviewed DashScope | low, medium, xhigh |

模型可关闭思考时额外显示极速；thinking_required 时不显示 off。表格是目录/本地请求构造证据，不等于收费线上推理验收。

## 旧配置与跨端

- 自动能力标记 thinking_mapping_version=2。
- 仅从已持久化 official_catalog 的旧 thinking_mapping 提取迁移证据；不能用新目录反推某次旧请求。
- 只在旧、新 reasoning_parameter 相同且实际值有明确新目标时迁移，幂等保存；user_declared/provider_probe/runtime_observation 映射不自动改写。
- 历史消息和 provider trace 不批量重写。
- Web 本地偏好及 Android 原生偏好均可持久化 max；原生桥接保留 max 和映射版本。
- 未增加跨设备实时偏好服务；沿用项目现有会话快照与原生/Web 交接机制。
- 无可证实旧映射时不进行语义迁移。模型不支持的旧选择仍由现有能力归一化处理，不保证可重现缺少记录的历史 wire 值。

## 实施范围

后端模型目录、解析器、能力读取/发现、各请求 schema、Agent 工具 schema、语音上限和协议适配；Web 主聊天/重试/侧栏/文档/设置；Android 原生菜单/重试/滑条/偏好/目录；移动 Web 类型。

## 验证记录

- 后端最终核心六文件定向测试：113 passed，包含新增 max 测试、全部 bundled 模型快照校验、用户覆盖/协议隔离、DashScope gate 和 off 回归。
- 前期扩展六文件定向测试：134 passed，2 条第三方弃用警告；与 113 部分重叠，不合并计数。
- 前端核心七文件：50 passed；之后补上连续拖动重渲染回归，滑条独立4项通过，迁移4项通过。另一次主聊天恢复10项、重试13项、侧栏6项均显示通过，但组合命令因本机运行缓慢达到整体超时；不将该组合命令记录为完整成功。
- 最终预算/开关微调后，再次运行新增力度测试与全部 bundled 模型快照校验：19 passed。
- Android JDK 21 构建与三个测试类：23 项通过（7 + 13 + 3），0 failures/errors。
- git diff --check 通过，存在既有 CRLF/LF 提示。
- 全量 TypeScript 检查未通过：graph-sidebar-preview.tsx 的 Session fixture 缺字段，resource-pages.tsx 的 workspaceId 未定义。它们不在本次力度改动范围；后续全量复核耗时异常，不以未完成检查声称通过。
- 未提交、未部署、未生成发布 APK；未执行真实厂商推理或使用真实凭据。未做 Android 真机拖动验收。
