# LearnGraph Developer Docs

这是基于当前仓库代码、`README.md`、已确认设计共识与后端 API 文档生成的静态开发者文档。

直接用浏览器打开 `index.html` 即可阅读；页面不依赖外部 CDN、构建工具或远程资源。

公开站点：<https://sunnyboy-y.github.io/LearnGraph/>

## 内容

- 基于当前 main 历史（起点 `ddf3a1c` v0.1 首发，HEAD `57fd37f`）的版本演进与设计原则；旧设计文档中的 `c173b36` 基线来自 rebase 前分支，不在 main 历史
- 系统架构与 FastAPI 应用网关
- Provider Port / Adapter 边界、能力快照与按供应商门控（Qwen 原生搜索通道、DashScope ASR）
- Agent Runtime、渐进式披露与原子工具模型
- Docker 沙箱、Agent Workspace、Egress 审批制出网、热容器池与预构建镜像
- 外部来源可信获取：图片 URL 安全下载/净化、GitHub commit 固定快照、不可变来源凭据与无网沙箱注入
- 事件溯源记忆系统：事件存储、投影、混合检索、Context Builder 动态装配与防注入
- 产物与分享：Artifact 不可变版本、分享令牌、软删除与卡片索引
- 可信组件与交互式子应用：服务器持有模板、CSP 锁死、postMessage 数据通道、interaction contract
- Message / SSE 持久化、批量落库、心跳与断线恢复理念
- 媒体输入管线：音频、视频、图片和全文文档的能力路由、解析、缓存与证据边界
- 安全、代码地图、扩展与验收规范

页面包含无需后端的交互预览：媒体输入管线与能力渐进式披露。所有动画均支持 `prefers-reduced-motion`，不加载外部脚本、字体或图片。

## Sandbox Network Security Model

普通代码沙箱的底层默认仍是 `network_mode="none"`。当部署启用审查版出网时，每个
沙箱只加入一个按沙箱创建的内部网络，该网络唯一的可达对端是强制 HTTP CONNECT 代理；
代理在连接时重新解析并分类目标，不信任之前解析过的 DNS 结果。外部内容由 Broker 写到
宿主侧不可变 artifact/workspace，而不是把公网路由交给代码沙箱。

代理同时支持 HTTPS `CONNECT` 和有界的 HTTP `GET` / `HEAD`；后者只转发安全请求头，
不转发上传 body、Cookie 或 `Proxy-Authorization`。每个 policy revision 还有请求数、
总字节和并发预算，超限返回 `429` 并写审计。

任务级能力由服务端从角色和工具集合计算，客户端只能缩小、不能提升：

| 能力 | 代码沙箱公网路由 | 允许的 Broker / 工具 |
| --- | --- | --- |
| `OFFLINE` | 无 | 本地文件、Shell、数据和构建工具 |
| `FETCH` | 无 | 受审查的搜索、网页抓取和公共文件下载 |
| `BROWSER` | 无 | 独立固定 Browser runner，仍走同一 CONNECT 策略 |
| `RESTRICTED_EGRESS` | 仅沙箱 → 强制代理 | 明确审查过的 Git 等原生网络工具 |

模型选择结果会随任务持久化为 `network_policy`；不兼容当前能力的工具不会注册到模型中，
而不是只依靠 prompt 禁止。未知新增工具默认要求 `RESTRICTED_EGRESS`。

### Fetch / Download 数据流

```text
Agent 工具调用
  -> 服务端 exact-host 授权
  -> Host Fetch / Acquisition Broker
  -> 解析全部 A / AAAA 并逐项校验
  -> 只连接已校验的公网 IP
  -> 每个 redirect 重新校验 scheme / host / DNS / IP
  -> 网页抓取仅 HTTP 80 / HTTPS 443；文件下载仍仅 HTTPS 443
  -> 响应大小、超时、redirect 数、请求与字节配额
  -> 安全文件名 / 可选扫描器接口 / SHA-256
  -> 写入 session workspace
  -> 离线 Sandbox 读取文件
```

公共文件下载只接受无凭据的 HTTPS 443 URL，逐主机审批，解析并清洗 `Content-Disposition`，
阻止路径穿越和危险设备名，拒绝用不同内容覆盖已有路径。未配置恶意文件扫描器时，
receipt 会诚实记录 `not_configured`，不会伪造“已杀毒”。

### 明确边界

- Sandbox 没有 public IP、published port、SSH、RDP、任意 TCP forward 或 SOCKS 出口。
- `curl` / `wget` 只有在受限 Egress 容器中且 exact-host 已审批时才可能成功；不具备任意公网能力。
- 普通 `OFFLINE` / `FETCH` 沙箱不能通过 socket、代理环境变量绕过，因为网络层没有对应路由。
- 地址分类同时覆盖 IPv4、IPv6 和 IPv4-mapped IPv6，并用 `is_global` 作为最终 fail-closed 门禁。
- 宿主 Secret、Provider key、MCP credential 和 Docker daemon socket 不进入普通沙箱环境。
- Browser 当前是隔离固定 runner 内的 Chromium fallback；通用的 Browser Broker 和多租户 Browser Worker 属于 Phase 2。
- Package egress、认证 Preview Gateway、Malware Scanner 和 gVisor/Kata/Firecracker 属于后续 Phase。

### 迁移与兼容

- 旧任务没有 `network_policy` 时，按角色与工具重新派生，保持原有安全默认。
- 旧 policy 文件没有 `capability` 时按 `RESTRICTED_EGRESS` 解析。
- 客户端字段只是意图提示，服务器永不因它开启 Docker NAT、host network 或公网入口。
- 多租户部署应额外配置 `LEARNGRAPH_SANDBOX_DENY_CIDRS` 覆盖 Pod、Service、VPC、数据库、Redis、队列和内部 API 网段。

回归覆盖位于 `backend/tests/unit/test_agent_execution_profiles.py`、`backend/tests/unit/test_external_acquisition.py`、
`backend/tests/security/test_sandbox_network_capabilities.py`、`backend/tests/security/test_agent_egress_policy.py` 和
`backend/tests/security/test_sandbox_network_hardening.py`、
`backend/tests/unit/test_fetch_egress_refresh_and_fake_ip.py`。

## 本地预览

在仓库根目录运行：

```powershell
python -m http.server 4173 --directory docs
```

然后打开 `http://127.0.0.1:4173/`。

## GitHub Pages

`.github/workflows/deploy-developer-docs.yml` 会在 `main` 分支中的
`docs/` 发生变化时，把该目录作为独立 GitHub Pages Artifact 发布。

仓库首次启用时，需要在 GitHub 的 **Settings → Pages → Build and deployment**
中将 Source 设为 **GitHub Actions**。之后可以通过推送触发部署，也可以在
Actions 页面手动运行 `Deploy developer docs`。

`backend/docs/` 由根目录 `.gitignore` 排除；`docs/` 本身是已跟踪并发布到 GitHub Pages 的开发者文档目录。
