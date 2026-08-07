# LearnGraph Developer Docs

这是基于当前仓库代码、`README.md`、已确认设计共识与后端 API 文档生成的静态开发者文档。

直接用浏览器打开 `index.html` 即可阅读；页面不依赖外部 CDN、构建工具或远程资源。

公开站点：<https://sunnyboy-y.github.io/LearnGraph/>

## 内容

- 以 `c173b36 fix bugs 修复音频、视频等转录bug` 为基线的版本演进与设计原则
- 音频、视频、图片和全文文档的能力路由、解析、缓存与证据边界
- 系统架构与 FastAPI 应用网关
- Provider Port / Adapter 边界
- Agent Runtime、渐进式披露与原子工具模型
- 系统提示词编译策略
- Tools、Skills 与 MCP 授权模型
- Docker 沙箱、Agent Workspace、网络与 egress 审批
- Canonical Memory、事件投影、Provider Binding 与治理
- Message / SSE 持久化和断线恢复理念
- 安全、代码地图、扩展与验收规范

页面包含无需后端的交互预览：媒体输入管线、能力渐进式披露和记忆事件投影。所有动画均支持 `prefers-reduced-motion`，不加载外部脚本、字体或图片。

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

本地内部资料目录 `docs/` 与 `backend/docs/` 仍由根目录 `.gitignore` 排除，
不会被包含在 Pages Artifact 中。
