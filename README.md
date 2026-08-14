<p align="center">
  <img src=".github/assets/learngraph-readme-hero.png" alt="LearnGraph：一张会随学习持续生长的知识路线图" width="100%">
</p>

<h1 align="center">LearnGraph</h1>

<p align="center">
  <strong>让人从 AI 学习，高效进入并掌握陌生领域。</strong><br>
  <span>从一个真实目标出发，获得一张随学习持续生长的知识路线图。</span>
</p>

<p align="center">
  <img alt="Project status: early development" src="https://img.shields.io/badge/status-early_development-E5A93D">
  <img alt="React 19" src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white">
  <img alt="SQLite WAL" src="https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite&logoColor=white">
  <a href="https://sunnyboy-y.github.io/LearnGraph/"><img alt="Developer Docs" src="https://img.shields.io/badge/docs-developer_guide-08745C"></a>
  <a href="./LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-2EA44F"></a>
</p>

<p align="center">
  <a href="#-为什么是-learngraph">核心特色</a> ·
  <a href="#-核心亮点">技术亮点</a> ·
  <a href="#-产品截图">产品截图</a> ·
  <a href="#-一次完整的学习旅程">学习旅程</a> ·
  <a href="#-当前能力">当前能力</a> ·
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-技术架构">技术架构</a> ·
  <a href="https://sunnyboy-y.github.io/LearnGraph/">开发者文档</a> ·
  <a href="https://github.com/SunnyBoy-y/LearnGraph">GitHub</a>
</p>

> [!IMPORTANT]
> LearnGraph 目前处于早期开发阶段，适合本地体验、研究和共同开发，请勿用于生产环境。

---

## ⚡ 核心亮点

| | 亮点 | 一句话说明 |
| --- | --- | --- |
| 🌱 | **可生长的知识路线图** | 从一句真实目标出发，生成可审核、可修订的知识图谱；随着学习进展持续调整，用户始终掌握决定权。 |
| 🤖 | **Agent 级学习智能** | Agent 会主动规划执行：找资料、组织解释、设计练习、调用工具；能力采用**渐进式披露**，效率高，成本可控。 |
| 🛡 | **Docker 隔离沙箱** | Agent 执行代码、处理文件、构建预览都在 Docker-only 的隔离工作区：**默认断网 + 审批制出网** |
| 🧠 | **事件溯源长期记忆** | 记忆不是"摘要 + 向量库"，而是**事件溯源底座 + 分层投影 + 混合检索 + Context Builder 动态装配**：可重放、可溯源、可治理、防注入。 |
| 🃏 | **Magic Card 可信组件** | AI 生成的不只是文字，还有可交互、有状态、可收集数据的页面组件|
| 🔎 | **人在回路的可信成长** | 每次对话、作答、解释、实践都沉淀为带来源的证据；重要判断（图谱、联网、付费操作）永远由用户审核确认，结论可溯源、可复盘。 |
| 🔌 | **多模型 Provider 支持** | 支持接入 OpenAI / Anthropic / Qwen(DashScope) / DeepSeek / Ollama / Copilot 等多种协议模型；供应商私有能力也被支持 |

---

## 🖼 产品截图

<table>
  <tr>
    <th width="50%">主对话页面</th>
    <th width="50%">可交互web组件生成</th>
  </tr>
  <tr>
    <td><img src=".github/assets/chat.jpg" alt="LearnGraph 主对话页面：知识图谱、智能体、主交互页面图"></td>
    <td><img src=".github/assets/product-artifact-preview.png" alt="LearnGraph 可交互式web组件"></td>
  </tr>
  <tr>
    <th>随心练习速览解释</th>
    <th>文档学习与溯源问答</th>
  </tr>
  <tr>
    <td><img src=".github/assets/exam.png" alt="LearnGraph 交互练习与滑词解释"></td>
    <td><img src=".github/assets/product-document-learning.png" alt="LearnGraph 文档学习与溯源问答"></td>
  </tr>
</table>

---

## 🧭 一次完整的学习旅程

1. **说出目标**：用自然语言描述想学什么、为什么学以及时间约束，系统动态澄清真正影响路线的关键信息。

2. **审核路线**：LearnGraph 生成初始目标图谱，由用户确认节点、前置关系、范围和优先级后发布。

3. **与 Agent 一起学习**：围绕单个或多个节点展开对话，结合个人文件、来源检索、练习与工具执行完成学习任务。

4. **让证据推动成长**：对话、作答、解释和实践产出沉淀为带来源的证据，持续更新能力状态、置信度与复习风险。

5. **获得下一步行动**：系统结合目标权重、知识前置关系、能力缺口和时间安排，推荐当前最值得投入的学习行动。

```text
真实目标与资料 → 目标澄清 → 初始目标图谱 → 用户审核
       ↑                                      ↓
用户审核路线更新 ← 下一步行动 ← 能力图谱变化 ← Agent 学习与证据
```

## 🔄 G-R-E-M-A 学习闭环

| 阶段 | 产出 | LearnGraph 如何处理 |
| --- | --- | --- |
| **G · Goal** | 结构化目标 | 澄清真实学习目标，保留用户确认与约束 |
| **R · Representation** | 目标图谱 | 生成可审核、可修订、带版本的知识结构 |
| **E · Evidence** | 证据记录 | 将学习行为与产出转换为带来源的可追溯证据 |
| **M · Mastery** | 能力状态 | 基于证据解释掌握状态、置信度与复习风险 |
| **A · Action** | 下一步行动 | 综合目标权重、前置关系、能力缺口和时间形成推荐 |

目标图谱和能力图谱是 LearnGraph 的两个长期视图：

- **目标图谱**记录为了目标需要学习的知识结构，重要更新经过用户审核；
- **能力图谱**记录用户实际形成的能力，由练习、解释和实践等可追溯证据持续驱动。

---

## ✅ 当前能力

| 领域 | 已接入的产品与代码能力 |
| --- | --- |
| **目标与图谱** | Goal 澄清与确认、候选图谱审核、目标图谱、能力图谱与图谱工作台；图谱生成支持极速/思考模式与流式根预览 |
| **学习对话** | Session、Message/MessagePart、SSE 事件溯源流、断线恢复与重试、消息版本和分支、结构化消息渲染 |
| **资料与来源** | 文件上传、解析状态、本地对象存储、文档学习、联网来源与引用（Qwen 原生搜索来源 + 角标）；文搜图/图搜图链接可经可信宿主下载器完成图片净化、来源固化并注入无网沙箱（多图支持 `urls` 并行下载），GitHub 文件/目录/仓库按固定 commit 生成哈希清单后注入 |
| **证据与行动** | Evidence、Mastery、练习、作答反馈、复习风险和下一步行动相关流程 |
| **长期记忆** | 事件溯源记忆（加密事件流、分层投影、混合检索、Context Builder 预算装配）、学习状态衰减、文件修订失效、记忆治理 |
| **Agent 与扩展** | 模型、搜索、研究、MCP、Storage 等 Provider 边界；渐进式工具披露；沙箱能力探测与受控执行；Skill 市场与本机导入 |
| **可信组件** | 8 类内置交互组件（选择题/填空/表格/天气卡/指标卡…）+ 第三方组件 Manifest 注册授权 + 双向交互子应用（interaction contract + 事件驱动 Agent） |
| **产物与分享** | Artifact 不可变版本快照、只读分享令牌（可撤销）、软删除、会话卡片自动索引 |
| **沙箱执行** | Docker-only 隔离的 Agent Workspace，默认断网、Egress 审批制出网、热容器池复用；受限文件读写、命令执行、宿主侧 ASR 桥和产物发布；配额、超时、幂等、审计与 SSE 状态回传；预构建镜像（ACR）与真实进度 |
| **工作区治理** | 登录（首次强制改密）、Membership、RBAC/ACL、用量、审计、迁移预检、部署 Profile（个人/团队/云） |

## 🗺 后续规划

- **更多主流模型适配**：持续扩展文本、视觉、推理、图片生成和语音模型，让不同学习任务可以匹配更合适的模型能力。
- **可更换的 Agent 内核**：在统一的 Goal、Graph、Evidence、Mastery 和 Action 契约之上接入不同 Agent Runtime，支持按场景选择和演进智能体内核。
- **桌面端与移动端**：围绕连续学习体验建设桌面客户端和移动客户端，让路线、资料、对话、练习与复习跨设备衔接。
- **更丰富的学习工具生态**：继续完善 Skills、MCP、可信组件、文档学习和研究能力，让 Agent 可以组合更多专业工具完成真实学习任务。
- **更完整的端到端验收**：持续覆盖跨模块浏览器场景、真实远程 Provider、权限边界和完整 Agent 学习闭环。
---

## 🚀 快速开始

### 环境要求

| 工具 | 版本 |
| --- | --- |
| Node.js | 20+ |
| npm | 10+ |
| Python | 3.11+ |
| [uv](https://docs.astral.sh/uv/) | 最新稳定版 |
| Docker | 可选，仅沙箱能力需要 |

### 安装并启动

```bash
git clone https://github.com/SunnyBoy-y/LearnGraph.git
cd LearnGraph
npm run dev:install
```

`dev:install` 会在前端或后端缺少 `.env` 时，自动从对应的 `.env.example` 创建本地配置；已有 `.env` 会原样保留。随后脚本按照 `frontend/package-lock.json` 和 `backend/uv.lock` 安装依赖，并联合启动前后端。后续可直接运行：

```bash
npm run dev
```

| 服务 | 默认地址 |
| --- | --- |
| Web | `http://127.0.0.1:5173` |
| API | `http://127.0.0.1:8000` |
| OpenAPI | `http://127.0.0.1:8000/docs` |
| Health | `http://127.0.0.1:8000/api/v1/health` |

需要修改端口时：

```bash
npm run dev -- --frontend-port 5174 --backend-port 8001
```

### 公网访问（内网穿透，可选）

项目不内置内网穿透配置。需要从公网访问时，请自行运行 FRP / 端口转发等工具，并**手动配置环境变量**：

| 本地端口 | 需要公网映射 | 用途 |
| --- | --- | --- |
| `5173` | 必需 | Vite 前端、同源 `/api`、SSE、实时听写 WebSocket |
| `8001` | 使用交互式子应用/卡片时需要 | 独立 subapp preview 源，必须映射到另一个公网端口 |
| `8000` | 可选 | 仅当需要公网直接访问 OpenAPI/API 时；普通页面由 `5173` 代理 |

手动配置步骤：

1. `frontend/.env` 填 `LEARNGRAPH_PUBLIC_ORIGIN=<你的公网入口>`（Vite 会自动加入 allowedHosts 并作为公网 origin）；
2. 使用交互式子应用/卡片时，`backend/.env` 再填 `LEARNGRAPH_SUBAPP_PREVIEW_ORIGIN=<你的 preview 公网入口>`；
3. 服务不在运行 FRP 的同一台机器上时，额外加 `--lan` 或设 `LEARNGRAPH_LISTEN_HOST=0.0.0.0` 让服务监听外部地址；
4. 需要浏览器直连后端（不走 Vite 代理）时，自行配置 `LEARNGRAPH_CORS_ORIGINS`。

示例（以 `https://my-tunnel.example.com` 为例）：

```bash
# frontend/.env
LEARNGRAPH_PUBLIC_ORIGIN=https://my-tunnel.example.com

# backend/.env（仅使用交互式子应用时）
LEARNGRAPH_SUBAPP_PREVIEW_ORIGIN=https://my-tunnel.example.com:23351
```

`npm run dev` 会读取上述环境变量并完成 allowedHosts / CORS / preview origin 接线；脚本不再提供 `--public-origin` 之类的命令行捷径，公网访问一律由环境变量显式配置。

### Docker Compose（可选）

适合想先跑起来、或不想在本机装 Node / Python 的自托管体验。镜像同时包含前端生产构建和 FastAPI；浏览器走同源 `/api/v1`。

```bash
docker compose up --build
```

| 服务 | 默认地址 |
| --- | --- |
| Web + API | `http://127.0.0.1:8080` |
| 子应用 Preview | `http://127.0.0.1:8001` |
| Health | `http://127.0.0.1:8080/api/v1/health` |

数据写在 named volume `learngraph-data`。未设置 `LEARNGRAPH_MASTER_KEY` 时，入口脚本会生成一把主密钥并保存在卷里，这样页面里保存的 Provider Secret 重启后仍能解密。首次启动请看 `app` 容器日志里的管理员临时密码。

Agent 沙箱要调用**宿主** Docker Engine，并且 bind mount 路径对 dockerd 必须是宿主机路径。Linux 可用覆盖文件：

```bash
export LEARNGRAPH_DATA_DIR=/var/lib/learngraph
export DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up -d --build
```

Windows / Docker Desktop 请继续用上面的 `npm run dev` 跑后端，让本机 Docker 提供沙箱；不要把 Compose 里的 named volume 路径传给 dockerd。

更完整的变量说明见仓库根目录 `.env.example` 和 `backend/.env.example`。


### 首次登录

空数据库首次启动时会创建 `admin` 管理员，并只在后端控制台打印一次高强度临时密码。首次登录后请立即修改密码。

默认配置会创建 Demo 身份，也不会启用本地演示模型。如需显式开发演示，可在本地 `.env` 中单独开启，并与真实功能验收区分。

<details>
<summary><strong>本地配置文件说明</strong></summary>

`npm run dev` 和 `npm run dev:install` 每次启动时都会检查 `frontend/.env` 与 `backend/.env`。缺失文件会从同目录的 `.env.example` 自动创建，已经存在的文件不会被修改或覆盖。需要自定义配置时，直接编辑生成的本地 `.env` 即可。

Provider API Key 默认由操作系统安全凭据库保护。首次在页面保存 API Key 时会自动生成版本化主密钥，无需在 `.env` 中配置 `LEARNGRAPH_MASTER_KEY`。托管部署可显式选择 `environment` 兼容模式并注入高熵主密钥。

</details>

---

## 🏗 技术架构

```text
Browser / React 19 + TypeScript + Vite
├─ React Router · TanStack Query · React Flow
├─ Streamdown / AI Elements · trusted-renderer · subapp-bridge
└─ ApiClient: Bearer + X-Workspace-ID + JSON/SSE
                         │
                         ▼
FastAPI /api/v1
├─ routers: HTTP/SSE 契约、认证、权限与错误边界
├─ services
│  ├─ 学习闭环: Goal · Graph · Chat · Evidence · Mastery · Action
│  ├─ 记忆:     Event Store · Projection · Retrieval · Context Builder
│  ├─ 运行时:   Agent · Tools · Skills · MCP · 可信组件 · 子应用事件
│  ├─ 沙箱:     Agent Workspace · Egress 审批 · 热容器池 · ASR 桥
│  └─ 产物:     Artifact Gateway · 分享令牌 · 卡片索引
├─ repositories: 工作区作用域的数据访问
└─ provider ports
   ├─ local:  文件存储
   └─ remote: 模型(Chat/Embedding/ASR/Image) · 搜索 · 抓取 · 研究 · 沙箱
                         │
                         ▼
SQLAlchemy 2 · SQLite(WAL) · local filesystem · Docker(可选)
```

SQLite(WAL) 是当前 MVP 的规范业务事实源。SSE 负责传输，Session、Message、MessageVersion、MessagePart、流事件和记忆事件仍会持久化（支持断线恢复、重放与审计）。前端统一访问 LearnGraph 后端，由服务端完成认证、工作区授权、Provider 调用和事实写入。

<details>
<summary><strong>查看仓库结构</strong></summary>

```text
LearnGraph/
├─ frontend/             React + TypeScript + Vite
│  └─ src/
│     ├─ api/            领域 API 与统一客户端
│     ├─ features/       页面和业务交互（auth/chat/goals/graph/memory/artifacts…）
│     ├─ components/     UI、图谱与消息渲染
│     └─ lib/            trusted-renderer · sandboxed-html-preview · subapp-bridge…
├─ backend/
│  └─ app/
│     ├─ api/routers/    HTTP/SSE 路由
│     ├─ services/       业务用例（chat/agent_runtime/memory_*/sandbox_*/artifact_*…）
│     ├─ repositories/   数据访问
│     ├─ providers/      Ports 与适配器（ports/ remote/ local/ catalog）
│     ├─ skills/         沙箱能力 Skill 包（pdf/docx/表格/PPT/媒体/生图/抓取…）
│     └─ domain/         模型与 Schema（含记忆事件模型）
├─ docs/                 可公开部署的开发者 HTML 文档（GitHub Pages）
├─ scripts/              跨平台启动与检查脚本
├─ Dockerfile            应用镜像（前端生产构建 + FastAPI）
├─ docker-compose.yml    自托管编排（Web/API + Preview）
└─ .github/assets/       README 公共素材
```

</details>

## 🧪 开发与检查

| 命令 | 作用 |
| --- | --- |
| `npm run dev` | 使用已有依赖联合启动前后端 |
| `npm run dev:install` | 从锁文件安装依赖并启动 |
| `npm run check` | 前端 lint/生产构建 + 后端语法/应用导入检查 |
| `npm run check:install` | 从锁文件安装依赖后执行全部检查 |
| `npm run check:frontend` | 仅执行前端检查 |
| `npm run check:backend` | 仅执行后端检查 |
| `npm run build:frontend` | 构建前端生产产物 |
| `docker compose up --build` | 用容器启动 Web/API 与 Preview |

`npm run dev` 会从 5173 开始自动选择第一个可用的前端端口，终端会显示实际地址。公共代码快照不包含内部开发文档、测试夹具或浏览器产物，因此 `npm run check` 不代表真实 E2E 或远程 Provider 验收已经完成。

涉及模型、搜索、研究或关键业务流程的发布，还应使用真实配置、真实 HTTP/SSE 和真实浏览器操作完成验证。

## 🔐 安全与可信边界

- `X-Workspace-ID` 是作用域提示；后端会重新校验 Membership、权限与资源范围。
- 沙箱默认拒绝网络；联网必须通过 Egress 审批链（域名白名单、连接时地址重分类、审计）。
- 外部图片和 GitHub 源码由宿主侧可信下载器获取，沙箱不会临时联网；下载逐主机审批、逐次 DNS 公网分类，图片重编码净化，GitHub ref 固定为 commit，并持久化 SHA-256 与来源凭据。
- Provider Secret 由后端版本化加密保存，不进入浏览器、日志、SSE、审计或导出。
- 模型、搜索、研究和沙箱能力均采用显式可用性状态，调用结果与失败边界可以追踪。
- Docker 沙箱不可用时返回明确状态，宿主机不会成为隐式执行环境。
- 数据库、上传内容、缓存、构建产物与真实凭据均由 Git 排除。
- 学习资产面向 Markdown、JSON 等开放格式导出，支持长期持有和迁移。


## 📚 开发者文档

完整的技术细节（架构、API 网关、Agent Runtime、渐进式工具披露、Skills、MCP、记忆系统、Docker 沙箱、可信组件、安全边界与验收规范）见[开发者文档](https://sunnyboy-y.github.io/LearnGraph/)。

## 🤝 参与贡献

欢迎在 [SunnyBoy-y/LearnGraph](https://github.com/SunnyBoy-y/LearnGraph) 提交 [Issue](https://github.com/SunnyBoy-y/LearnGraph/issues) 或 Pull Request。



## 📄 License

LearnGraph 基于 [MIT License](./LICENSE) 开源。

## 🙏 鸣谢

感谢 [CC-Switch](https://github.com/farion1231/cc-switch) 的开源贡献。本项目的 GitHub Copilot 接入，以及 Baidu Qianfan Coding Plan、火山 Agentplan、OpenRouter、Longcat、Kimi、Kimi For Coding、ModelScope 和 Xiaomi MiMo 快捷配置参考了 CC-Switch 的供应商预设与适配工作。

## 友情链接

学AI上L站！

https://linux.do/
