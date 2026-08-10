# LearnGraph 上线前审计 — 环境记录（专项版：用户体验 / 性能 / 稳定性）

> 记录时间：2026-08-09（本地）
> 审计规范：uploads/1786278521114/pasted-text-1.txt

## 1. Git 状态
- 分支：`main`
- Commit：`ca134798ac686182a64215945f0f30f28da480f1`（`fix: 完善登录小屏、图谱键盘操作与消息恢复`）
- 工作区存在**未提交修改**（以下文件为修改态，审计结论需考虑该基线）：
  - backend：`app/api/routers/chat.py`、`app/api/routers/memory.py`、`app/api/routers/memory_v2.py`、`app/core/database.py`、`app/providers/remote/anthropic.py`、`app/providers/remote/codex_provider.py`、`app/providers/remote/openai.py`、`app/services/chat.py`
  - frontend：`src/components/chat/message-part-renderer.tsx`、`src/features/chat/chat-message-parts.ts`、`src/features/chat/chat-pages.tsx`、`src/features/memory/memory-page.tsx`、`src/index.css`、`src/types/sessions.ts`
  - 未跟踪：`backend/_latency_probe*.json`、`frontend/src/components/chat/stream-stats-badge.tsx`、`frontend/src/features/chat/stream-stats.ts`、`scripts/dev.mjs`

## 2. 服务与端口（审计用隔离实例，独立于用户自用 5173/8000/8001）
| 服务 | 地址 | 说明 |
|---|---|---|
| 前端 dev（Vite） | http://127.0.0.1:5174 | 隔离测试实例；代理 /api → 8002 |
| 前端生产构建 preview | http://127.0.0.1:5175 | 由 `npm run build` 后 `vite preview` 提供（若构建成功） |
| 后端 API（uvicorn app.main:app） | http://127.0.0.1:8002 | 隔离实例，独立数据库 |
| Subapp preview | http://127.0.0.1:8003 | 隔离实例 |
| 用户自用服务（未触碰） | 5173 / 8000 / 8001 | 属用户自有环境，审计不修改 |

## 3. 数据库
- 类型：SQLite（WAL，busy_timeout=30s，`retry_sqlite_locked` 重试机制）
- 审计实例库：`backend/data/audit_test.db`（独立创建，含演示种子）
- 真实库 `backend/data/learngraph.db` 仅**只读**访问（provider 配置导入来源）
- 迁移：`init_database()` 启动时增量迁移（旧库依赖 database.py 迁移字典）

## 4. 模型供应商（审计实例）
- DeepSeek：`openai_responses`，base_url=https://api.deepseek.com，**status: healthy**（probe 通过）
  - 模型：deepseek-v4-flash / deepseek-v4-pro（discovered_model_ids）
  - 密钥来源：从真实实例 provider_secrets 行**密文复制**（keyring 共享主密钥 v1，fernet_sha256_v1）；报告不暴露任何密钥明文
- 密钥存储：OS Keyring（WinVaultKeyring，service=LearnGraph，active_key_version=1）
- 未配置：Firecrawl / 图像 / 搜索等其余 provider（不在本次审计范围）

## 5. 功能开关（审计实例生效值）
- Agent：`memory_agent_run_enabled=True`、`subapp_event_agent_enabled=False`（默认关闭，需显式开启）
- 沙箱：`sandbox_enabled=True`、`sandbox_backend=docker`（Docker 29.0.1 可用；容器镜像按需解析）
- 联网：`LEARNGRAPH_SANDBOX_EGRESS_ENABLED`（沙箱出站默认开启，egress 审批链 D2.1 默认开启）
- 浏览器工具：经沙箱（未单独确认）
- 记忆：写入 dual（shadow 5%）、读取 events、context_builder_v2、outbox worker 开启、治理默认开启
- 会话：auth_session_hours=12，demo 登录启用（demo / learn-graph-local，workspace=demo-workspace）

## 6. 测试工具链
- Playwright 1.62.1（全局 @playwright/test，`C:\Users\13600\AppData\Roaming\npm\node_modules`），浏览器缓存：chromium-1234 等
- Node v24.18.0 / npm 11.16.0 / uv 0.11.26 / Python 3.14.6
- 后端 API 前缀 `/api/v1`；鉴权 Bearer + `X-Workspace-ID` 头

## 7. 网络环境
- 本机回环测试；外部依赖（DeepSeek API）走真实公网

## 8. 部署方式
- 开发模式 dev（5174）+ 生产构建 preview（5175）；性能结论优先基于 5175 生产构建，若不可用则标记局限

## 9. 关键限制（本轮）
- 长时稳定性（数小时 soak）受单轮时间预算限制，采用并发脉冲 + 资源检查替代，结论标注为观察/推断
- 模型真实调用会消耗用户 DeepSeek 配额，测试次数受限
