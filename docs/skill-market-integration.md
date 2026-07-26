# LearnGraph Skill 与 MCP 市场接入方案

> 更新于 2026-07。本文档记录官方/用户 Skill 的划分机制（已实现）、外部市场调研结论（已核实）与后续接入路线图。

## 1. 官方 Skill 与用户 Skill 的划分（已实现）

### 1.1 定义

| 层 | 内容 | 管理方式 |
| --- | --- | --- |
| **官方 Skill** | 产品自身的工作流：图谱生成、学习路线规划、间隔复习、Canvas 可信组件、目标编排 | 随代码发布（`backend/app/skills/<dir>/SKILL.md`），系统自动安装/刷新/授权，用户不可删改 |
| **用户 Skill** | 市场安装、GitHub/手动导入、本机探测导入、自建文件包、声明式 Skill | 用户管理，走授权（allow_once / always / deny）流程 |

### 1.2 官方 Skill 注册表

数据驱动注册表位于 `backend/app/services/skill_package.py`：

- `OfficialSkillSpec` + `OFFICIAL_SKILLS`：每个官方 Skill 一条 spec（key、名称、版本、目录、授权理由、可选 `contextual_activation`）。
- `ensure_official_skill_package(s)`：幂等安装/刷新（按 `origin_hash = sha256(SKILL.md)` 判断内容变化），并维护持久 `always` 授权。新增官方 Skill 只需放一个 `SKILL.md` 目录 + 注册表加一条 spec。
- 当前官方 Skill：`canvas-emit-trusted-component`、`goal-learning-route`（仅 Goal+Agent 模式注入）、`graph-generation`、`roadmap-planning`、`review-coach`。

### 1.3 一等标志与保护

- `skills.is_official`（新列，SQLite 走 `_ensure_sqlite_skill_package_columns` 增量迁移；旧行由 `is_official_skill_record` 的兼容判断兜底）。
- **保留命名空间**：`assert_skill_identity_not_reserved` 在所有用户入口（创建包、声明式安装、市场安装、手动导入、本机导入）拒绝占用官方 skill_key 或 `source=learngraph_system`，错误码 `official_skill_identity_reserved`。与可信组件平面的 `builtin_component_identity_reserved` 对齐。
- **不可删改**：官方 Skill 的删除、撤销、声明式改写、文件写入/删除/建目录均返回 403 `official_skill_protected`；查看、校验、翻译不受限。
- **自动到位**：`GET /skills`、每个 Agent 轮次、demo 种子都会幂等地 ensure 官方 Skill，非 demo 部署的新工作区同样可见。
- 前端扩展中心将列表分为「官方 Skills」与「用户 Skills」两组，官方行带 `官方` 徽章、隐藏删除/撤销/启停，编辑器入口降级为「查看」。

## 2. 外部市场调研结论（2026-07 已联网核实）

### 2.1 Agent Skills 开放规范（agentskills.io）

- `SKILL.md` 必填 `name`（1–64、小写字母数字连字符，须与目录同名）与 `description`（1–1024）；可选 `license`、`compatibility`、`metadata`（任意 string map，惯例放 author/version）、实验性 `allowed-tools`。
- 目录约定：`scripts/`、`references/`、`assets/`；三级渐进加载（元数据 ~100 token → 正文 <5000 token → 按需读文件）。
- 规范无版本号/变更日志，趋于冻结；官方校验器 `skills-ref validate`。
- **本项目对齐**：frontmatter 解析器已升级（支持引号、`>`/`|` 折叠块，嵌套映射不再污染顶层键）；官方 SKILL.md 采用规范字段。

### 2.2 各来源接入能力

| 来源 | API | 认证 | 稳定性 | 推荐用法 |
| --- | --- | --- | --- | --- |
| **MCP Registry** | `registry.modelcontextprotocol.io/v0.1/servers`（`search`、`cursor`、`updated_since`） | 匿名读 | 最高（冻结 v0.1 + OpenAPI） | 直接同步/搜索；`updated_since` 增量 |
| **ClawHub** | `clawhub.ai/api/v1`（`/skills`、`/search`、`/download`、`/skills/{slug}/scan`、批量 `security-verdicts`） | 读免认证 | 高（官方文档 + OpenAPI） | 搜索发现 + 复用其安全裁决 |
| **skills.sh** | `skills.sh/api/v1`（`/skills` 排行、`/search`、`/audit`） | Vercel OIDC | 中（API 有文档但认证耦合 Vercel） | 排行/审计富化；内容仍从 GitHub 拉 |
| **GitHub 直连** | codeload tarball `…/tar.gz/{sha}` + code search `filename:SKILL.md` | 可选 token | 高 | 首要包源；按 commit SHA 锁定内容 |

种子仓库（已核实存在）：`anthropics/skills`（Apache-2.0，docx/pdf/pptx/xlsx 目录为 source-available 需标注）、`google/skills`（Apache-2.0，GCP 大目录）、`vercel-labs/agent-skills`（MIT）、`supabase/agent-skills`、`obra/superpowers`（许可证入库时核对）。

### 2.3 安全（语义供应链风险）

arXiv:2605.11418 证实 SKILL.md 的 description 本身即攻击面：检索期文本诱饵可赢得嵌入检索（最高 86% pairwise），描述措辞可使 Agent 在 77.6% 的对照中偏向恶意变体。2026-01 一项 42k skill 爬取发现 26.1% 存在至少一个漏洞。结论：

1. 扫描对象必须包含 **description/正文**（诱导性触发语、越权指令），不只是 scripts。
2. 安装时锁内容哈希（本项目已有 `origin_hash`/`content_hash`/`authorization_hash` 链），内容变化即失效授权（已有）。
3. 复用 ClawHub `security-verdicts` 与 skills.sh `/audit` 作为第三方信号。

## 3. 已落地的接入（本次）

- **配置**（`backend/app/core/config.py`，前缀 `LEARNGRAPH_`）：
  `SKILL_LOCAL_PROBE_MODE`、`SKILL_MARKET_REFRESH_ENABLED`、`SKILL_MARKET_GITHUB_TOKEN`、`CLAWHUB_ENABLED`/`CLAWHUB_API_URL`、`SKILLS_SH_ENABLED`/`SKILLS_SH_API_URL`（默认关）、`MCP_REGISTRY_ENABLED`/`MCP_REGISTRY_URL`、`EXTERNAL_CATALOG_TIMEOUT_SECONDS`。
- **联邦索引（搜索层）**：`ExternalCatalogService`（`skill_catalog_sources.py`）+ 三个只读端点：
  - `GET /skills/market/catalogs` — 目录清单与启用状态；
  - `GET /skills/market/external-search?catalog=clawhub|skills_sh&q=` — 外部 Skill 搜索（仅发现，不直接安装）；
  - `GET /mcp/registry/search?q=` — MCP Registry 搜索，前端「注册 MCP」对话框可一键预填 server_key/来源/版本/端点。
- **市场官方标识**：`SkillMarketCardView.official`（`learngraph/*` 来源），前端卡片带官方徽章。
- GitHub 拉取支持可选 token（提升速率限制）。

## 4. 路线图（建议）

**阶段 1（已完成）**：官方注册表 + is_official + 保护 + 外部目录搜索 + MCP Registry 搜索预填。

**阶段 2 — 安装闭环（已完成）**（`skill_github_import.py`）：
- **GitHub 固定导入**：`POST /skills/github/preview` 解析 `owner/repo[/path][@ref]` 或 github.com URL → commits API 解析 commit SHA → git trees API 一次列目录 → 列出候选 Skill（frontmatter 元数据 + 文件统计）；`POST /skills/github/install` 按预览时的 commit 锁定安装全量文本文件（scripts/references 等，跳过二进制并如实报告），溯源 `{owner, repo, path, ref, commit}` 写入 `manifest_json.github`，`origin_type="github_import"`、`version=commit[:12]`。
- **安装前权限预览**：预览返回 `required_permissions`（含 scripts → `sandbox.execute`）、`allowed-tools` 声明、文件数/体积/跳过数，前端安装对话框先展示再安装。
- **更新检查与升级**：`POST /skills/{id}/check-update` 对比上游 commit；`POST /skills/{id}/upgrade` 替换包内容、更新溯源并强制重新授权（内容哈希变化令旧授权失效）。回滚（保留旧版本行）延后到阶段 3。

**阶段 3 — 聚合与审核（审核部分已完成）**：
- **四层审核已就位**：
  1. 格式校验 — `POST /skills/{id}/validate`（原有）。
  2. **静态扫描（新）** — `skill_security_scan.py` + `POST /skills/{id}/security-scan`：约 25 条规则覆盖危险命令（rm -rf、下载执行、反向 shell）、凭据访问（SSH 密钥、.env、浏览器 Cookie、系统凭据库）、动态执行、中英文提示注入/检索诱饵/隐瞒与外传指令（对应 arXiv:2605.11418 攻击面）、混淆（零宽字符、超长 base64、data: URI）。所有安装入口（GitHub/市场/手动/本机）自动附带扫描，结果存 `validation_report.security_scan`，GitHub 预览也做 SKILL.md 快扫；风险为建议性，中/高风险在授权前以徽章提示。
  3. **语义审核（新）** — `skill_semantic_review.py` + `POST /skills/{id}/semantic-review`：用工作区远程模型判定描述与行为一致性、选择操纵、隐瞒/越权/外传，输出 pass/warn/fail + 风险分 + 理由，按内容哈希缓存。
  4. 沙箱评测 — `POST /skills/{id}/sandbox-run`（原有，Docker-only）。
- **ClawHub 聚合入缓存（已完成）** — `skill_clawhub_sync.py`：市场「刷新缓存」时按下载量拉取 ClawHub Top 条目（请求 `nonSuspiciousOnly=true`，被 ClawHub 标记可疑的条目不入库）upsert 进 `skill_market_cache`，`source_type="clawhub"`、`fetch_status="external"`；**发现即链接**——不镜像第三方文件（`files_json` 为空，安装按钮自动禁用），卡片回链 ClawHub 详情页复核后再走 GitHub/归档导入。设置 `LEARNGRAPH_CLAWHUB_ENABLED` 可关。
- 待做：ClawHub 安装时批量 `security-verdicts` 校验；skills.sh 排行富化；MCP Registry 增量同步（`updated_since`）建立本地 MCP 目录页。

**回归测试**：`backend/tests/test_skill_platform.py`（标准库 unittest，`python -m unittest tests.test_skill_platform`）钉住上述全部行为：frontmatter 解析、官方注册表幂等/保护/保留命名空间、安装时静态扫描、渐进披露与 `lg_skill_read` 授权守卫、GitHub 引用解析、ClawHub 同步（mock 网络）。

**阶段 4 — 生态**：
- **渐进披露注入（已完成）**：注入改为预算制——单技能正文 ≤`LEARNGRAPH_SKILL_PROMPT_INLINE_CHAR_LIMIT`（默认 4000 字符）且累计 ≤`…_TOTAL_CHAR_BUDGET`（默认 16000）时内联全文；超预算技能进「Skill catalog」目录（key + 一句话描述），模型按需调用新工具 **`lg_skill_read(skill_key, path?)`** 读取完整 SKILL.md 或 `references/` 文件（三级披露）。上下文激活技能（goal-route）始终内联；读取要求与注入相同的持久授权，每次读取记录 ExtensionInvocation 审计。官方技能正文精简（~0.5–3.5k），默认全部内联不受影响。
- 从成功对话/工作流一键生成 Skill 草稿（LearnGraph 特色：把一次成功学习过程提炼为可复用技能）。
- 团队/私有可见性（个人 / 团队 / 公开）；Skill 依赖 MCP 的声明（`metadata.learngraph.required-mcp`）。

## 5. 设计原则

1. **官方层是产品壁垒**：官方 Skill 与 LearnGraph 图谱 API、复习证据、沙箱深度绑定，第三方不可替代；通用 Skill 走聚合。
2. **市场只存元数据**：外部内容按 commit/哈希锁定，不长期镜像第三方文件树。
3. **发现与安装分离**：skills.sh/ClawHub 用于发现与信任信号，安装一律落到固定 GitHub commit 或显式导入，内容可审计。
4. **Skill ≠ Tool ≠ MCP**：Skill 教流程（注入指令），Tool/MCP 提供能力（函数调用），注册中心分开呈现（Skills Hub vs MCP 页签）。
