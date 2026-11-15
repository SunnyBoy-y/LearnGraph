# ChatGPT 官方登录使用 OpenAI 原生搜索

## 范围

LearnGraph 的 `codex_chatgpt` 是 ChatGPT 账号的 Codex 登录通道，不是 ChatGPT 网页会话。此改动使用该通道的托管 `web_search` 工具，不使用网页 Cookie，也不把 OAuth token 发送到公共 API 或第三方搜索服务。不能据此承诺与 ChatGPT 网页版具有完全相同的搜索结果、额度或工具权限。

## 启用步骤

1. 重新构建并重启运行中的 LearnGraph 前后端，使本次源码改动生效。只刷新旧的前端包或只改数据库不会更新 Agent 工具装配。
2. 在 Provider 管理中选择已通过 ChatGPT 官方登录的 Codex Provider，点击「发现模型」刷新目录能力。
3. 若模型或分组已有手动能力覆盖，在模型能力的「联网能力」中开启「支持托管网页搜索」，并将「默认联网路由」设为「仅原生」。也可使用「自动」，它在模型声明支持原生搜索时优先使用原生。
4. 在聊天中选择该 Provider 下的文本模型，打开「联网」后发送问题。普通聊天、Agent 模式及重新生成均支持原生路由。
5. 检查请求/回答追踪中的 `search_route` 应为 `model_native`。回答应使用上游实际返回的 URL 引用；没有来源不能视为搜索已执行。

AnySearch 可以保持启用供其他模型使用，无需全局删除。显式选择 `external` / `local` 的旧偏好仍保留；要改用官方搜索，需要切回自动或仅原生。

## 修复内容

- `model_catalog.py`：Codex 文本通道声明托管搜索能力，不将该能力按 GPT 名称授予任意兼容网关或图片模型。
- `model_options.py`：运行时刷新旧的官方目录 `hosted_web_search=false` 缓存；明确的用户单模型/分组设置仍有效。
- `network-search-route.ts` / `chat-pages.tsx`：自动路由优先原生；发送、重新生成及旧偏好恢复采用一致的解析逻辑；关闭联网不会注入搜索工具。
- `codex_provider.py`：启用时保留普通函数工具并附加 `web_search`，显式发送 `external_web_access=true`；继续使用原有 OAuth 头、流式响应及 URL annotation 引用解析。
- `chat_service_factory.py`：模型的实际路由解析为 Codex 原生时，不在请求 runtime 和隔离 worker 中装配外部 SearchProvider。
- `chat.py`：Codex 原生搜索不暴露或执行 `search_web` / `parallel_web_research`，避免历史函数调用重新触发 AnySearch；极速/思考模式明确允许原生搜索。Qwen 的独立搜索伴生通道不受该隔离规则影响。

## 验证与边界

回归测试使用假凭据与 `httpx.MockTransport`，不访问真实账号、不消耗真实搜索额度。覆盖原生/离线/外部请求、函数工具共存、URL 引用回传、旧目录能力、AnySearch 存在/缺失、Agent runtime、隔离 worker、路由缓存及上游拒绝时不静默切换外部搜索。

本次没有进行真实账号上游实测，也没有部署运行中的服务。上游是否接受某个账号/模型的工具，由真实请求结果决定；遇到拒绝会显式报错，不自动使用 AnySearch。

仅声明 `hosted_web_search=true` 不是远端验证；已登录也不意味着所有账号、模型或未来版本都支持相同工具。该直连后端没有稳定的公开 API 合同，后续变化需要继续适配。

## 协议参考

- OpenAI Codex 配置文档「Web search mode」：`https://learn.chatgpt.com/docs/config-file/config-basic`，说明 cached/live/disabled 的区别。
- OpenAI Responses 网页搜索文档：`https://developers.openai.com/api/docs/guides/tools-web-search`，说明 `web_search`、`external_web_access` 和 `url_citation`。公开 Responses 文档不等同于对 ChatGPT OAuth 直连后端的支持保证。
