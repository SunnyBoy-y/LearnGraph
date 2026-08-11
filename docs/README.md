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
- 事件溯源记忆系统：事件存储、投影、混合检索、Context Builder 动态装配与防注入
- 产物与分享：Artifact 不可变版本、分享令牌、软删除与卡片索引
- 可信组件与交互式子应用：服务器持有模板、CSP 锁死、postMessage 数据通道、interaction contract
- Message / SSE 持久化、批量落库、心跳与断线恢复理念
- 媒体输入管线：音频、视频、图片和全文文档的能力路由、解析、缓存与证据边界
- 安全、代码地图、扩展与验收规范

页面包含无需后端的交互预览：媒体输入管线与能力渐进式披露。所有动画均支持 `prefers-reduced-motion`，不加载外部脚本、字体或图片。

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
