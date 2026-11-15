# 五项联网补强

本次仅补足执行状态、隔离浏览器、MCP 搜索扩展、按路由工具加载、搜索与正文分工；没有新增搜索供应商或重做设置体系。

## 1. 真实执行状态

沿用聊天现有工具卡片/折叠活动行，显示实际供应商，例如「正在搜索（OpenAI）」「搜索完成（百炼）」「读取网页失败（隔离浏览器）」。供应商来自实际适配器或已授权 MCP Server，不采用模型自述。

OpenAI 原生工具状态由真实 `web_search_call` 事件产生；百炼在实际返回搜索来源时显示完成，不因开了联网就假造“正在搜索”。上游未确认完成时显示状态未确认。普通聊天的预搜索、网页读取和重新生成也会在 I/O 前后发送真实阶段状态。

## 2. 隔离浏览器补充

Agent 新增 `browser_read_page`：仅在普通 `fetch_web_page` 失败或返回内容不足时读取已发现的 URL，不替代搜索引擎。

- 复用既有 sandboxd/沙箱权限和 Chromium，不在宿主启动用户浏览器。
- 每次新建 Chromium 和无持久 profile 的 context，不导入 Cookie、storage state 或 CDP 用户会话。
- `use_user_session=true` 明确拒绝。登录态访问本次没有开放，必须另行设计并获得单独授权，不能借用网页域名授权。
- 请求地址和返回地址都经过公共 URL/域名校验；页面子资源受域名约束，出站继续依赖既有沙箱 egress 代理。沙箱内的固定本地转发器将 policy digest 仅添加到代理这一跳（包含 HTTPS CONNECT），不发送给网页。没有受控代理和 policy digest 就失败，不转为直连。
- 只允许 GET/HEAD，禁用下载、Service Worker，不提供点击、输入、提交表单或执行任意页面脚本的接口。
- 固定读取脚本及 URL 配置暂存在会话沙箱 `work/.learngraph-browser/`，通过现有受限 Node runner 执行；不放宽 runner 对 inline code 的限制。

部署前提：现有沙箱运行镜像已安装 Chromium 与 playwright-core，沙箱 Agent 权限已启用，并已通过现有机制配置/批准该沙箱的浏览器网络出口。域名抓取授权不会自动授予沙箱联网权限。

## 3. MCP 可插拔搜索/正文契约

复用已有 MCP 注册、工具 allow-list、授权哈希和 grant。只有在现有 manifest 中声明的、已请求并获得网络授权的工具，才会按统一网页来源格式处理。不需要针对不同学术或知识库服务修改聊天主流程。

示例（在现有 MCP Manifest JSON 中配置，不需要新设置页面）：

```json
{
  "schema_version": "1.0",
  "identity": "research-sources",
  "requested_tools": ["search_papers", "read_paper"],
  "permissions": ["network"],
  "requested_resources": [],
  "requested_prompts": [],
  "web_tools": {
    "search_papers": {
      "kind": "search",
      "results_path": "papers",
      "url_field": "url",
      "title_field": "title",
      "snippet_field": "abstract"
    },
    "read_paper": {
      "kind": "fetch",
      "results_path": "",
      "url_argument": "url",
      "url_field": "url",
      "title_field": "title",
      "content_field": "text"
    }
  }
}
```

服务应返回 MCP `structuredContent` 或可解析的 JSON 文本；不从自由文本中猜测 URL。修改 manifest 会沿用既有重新授权流程。学术/企业服务本身仍由用户配置，本次不安装或授权任何外部服务。

MCP 正文调用必须通过已有域名授权，输入 URL 与返回来源继续校验公共地址与域名；企业数据权限仍由既有 MCP grant 约束。远端服务自身的内部连接/抓取由该服务负责，返回正文只是获授权服务提供的数据，不等于 LearnGraph 浏览器独立复核，不能把来源过滤宣称为控制了远端服务器的全部出站。

## 4. 路由约束不能靠激活绕过

约束同时用于工具定义、能力目录、实际执行、隔离 worker 和子任务继承。

- `disabled`：不加载或执行网页搜索工具。
- `model_native`：不加载/执行 AnySearch 或 MCP 网页搜索。百炼需要工具调用的场景只保留官方 Qwen Responses 伴生通道，不用默认外挂替代。
- `external` / `auto`：允许已有授权范围内的 MCP 搜索工具。
- `local`：不自动把远程 MCP 搜索当成本地搜索通道。
- 未声明语义的网络 MCP 工具，在原生/关闭路由下保守拒绝；不能利用哈希工具名或无效缓存声明绕过。
- `sandbox_search_web` 同样遵守本轮路由，不能把原生搜索改走沙箱里的默认外挂；沙箱检索也统一标记为摘要线索。
- 子任务由服务端写入父级有效路由，丢弃模型自填路由；后台运行和接管不能替换为更宽的通道。

已授权具体 URL 的网页读取与“搜索发现来源”分开管理；关闭搜索不是无条件撤销所有已有的逐 URL 读取授权。

## 5. 摘要与正文分工

搜索结果统一标记 `evidence_kind=search_snippet`、`page_read=false`；即使服务在搜索结果中附带 `content`，也不自动算作读过正文。只有独立页面读取实际返回的文字才标记 `page_text`。正文只代表返回的片段，不承诺读完未返回的页面部分。

MCP 与普通搜索共用已有 URL/title/index 来源列表和引用展示；跨工具/轮次按 URL 分配稳定编号，读取正文后更新同一来源的证据级别。消息列表压缩与历史重载保留证据类型、读取状态及实际供应商，不把已读正文再次降格成未分类来源。网页正文、搜索摘要和 MCP 内容一律是不可信参考资料，不具备修改系统规则或扩大工具权限的效力。

## 验收记录

- 针对性后端回归：216 项通过（含首次回答/重试 HTTP 持久化、Codex/Qwen 协议、MCP 与子任务链路）。
- 最终隔离浏览器及边界复核：40 项通过，其中 4 项实际启动 Chromium，覆盖动态正文、Cookie 隔离、越权跳转、不可读页面和长中文/emoji 输出截断。
- 浏览器代理 HTTP / HTTPS CONNECT 及缺少授权身份的拒绝：Node 测试 3 项通过。
- 前端状态、工具卡片、搜索路由和重试回归：38 项通过；完整 TypeScript 类型检查通过。
- Python 编译、Node 语法与本次改动的 Git 空白检查通过；临时补丁已清理。

## 验证边界

测试覆盖状态转换、失败/未确认闭环、供应商标识、MCP 契约与授权、摘要/正文区分、路由拒绝、子任务继承和浏览器脚本的现有 runner 约束。未使用真实账号/搜索额度，也未部署服务或借用用户登录态。

已另行运行真实 Chromium 的隔离集成测试：使用本机新建的临时无登录 profile、仅指向本地测试代理/页面夹具，不访问公网或用户浏览器会话。动态 JavaScript 正文、代理 digest 认证、跨次 Cookie 隔离、禁止越权跳转及拒绝无正文均通过。运行命令：`LEARNGRAPH_BROWSER_INTEGRATION=1 uv run --no-sync python -m pytest tests/integration/test_browser_read_live.py -q`。本机尚无生产沙箱浏览器运行镜像，故此测试不等同于已部署 sandboxd 的端到端验收；部署仍需满足现有镜像与受控出口前提。
