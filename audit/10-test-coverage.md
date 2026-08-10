# 10 - 测试覆盖审计报告（接口 × 功能点）

> 审计基线：main@ca13479（工作树含未提交修改）
> 方法：全量枚举测试文件 + 提取每个测试的用例与覆盖对象 → 与 31 个 router（~438 端点）及审计盘点出的 28 个用户任务/30 个页面做覆盖矩阵。

## 一、测试资产总览

### 后端（backend/tests/，gitignored，需 git add -f 入库）
| 文件 | 用例数 | 覆盖领域 | 测试形态 |
|---|---|---|---|
| memory/test_memory_event_driven.py | 5 | 记忆事件驱动抽取（默认配置/去重/工作区开关/断点存活） | 服务层+db fixture |
| memory/test_memory_visibility.py | 6 | 记忆可见性/分层/列表视图/档案 schema | 服务层+db fixture |
| memory/test_producers.py | 4 | 记忆生产者（练习证据/Agent run 排除/幂等） | 服务层+db fixture |
| memory/test_read_mode_cutover.py | 4 | 记忆读取模式切换/预算排除/FTS 回退 | 服务层+db fixture |
| memory/test_temporal_normalizer.py | 9 | 时间归一化（时区/粒度/计划 key） | 纯单元 |
| security/test_agent_egress_policy.py | 5 | 沙箱 egress 策略推导（allow/deny/关闭即失败） | 服务层+db fixture |
| security/test_mcp_oauth_router.py | 8 | MCP OAuth（PKCE/state 校验/注册默认关/刷新/吊销） | 服务层+db fixture |
| security/test_mcp_secret_store.py | 8 | MCP 密钥存储（版本化信封/旧主密钥兼容/异常） | 纯单元 |
| security/test_skill_permissions.py | 5 | 技能权限边界（viewer/member/owner HTTP 级） | **HTTP TestClient**（唯一） |
| security/test_subapp_agent_publish.py | 16 | 子应用发布契约/事件/会话/限流/审批 | 服务层+db fixture |

**合计：10 文件 / ~70 用例。覆盖领域仅 2 个：记忆、安全。HTTP 级测试仅 1 个文件。**
### 前端（frontend/src，19 文件 / ~102 用例）
| 文件 | 用例数 | 覆盖对象 |
|---|---|---|
| api/client.test.ts | 3 | ApiClient 工作区作用域 |
| api/auth-store.test.ts | 3 | authStore 持久化/mustChangePassword |
| api/sse.test.ts | 2 | SSE 解析 |
| lib/subapp-bridge.test.ts | 20 | 子应用通道（最大覆盖区） |
| lib/subapp-media.test.ts | 28 | 子应用媒体注入/MIME |
| lib/trusted-renderer.test.ts + components/chat/trusted-renderer.test.tsx | 9 | 可信渲染器委托/状态 |
| components/chat/subapp-artifact.test.tsx | 2 | 沙箱制品子应用模式 |
| features/chat/selection-explanation*.test.* | 11 | 选中文本解释 |
| features/auth/auth-messages + password-rules | 9 | 登录错误文案/密码规则 |
| features/dashboard/dashboard-page.test.tsx | 3 | 首页查询状态 |
| features/settings/personalization-page.test.tsx | 2 | 个性化页清除痕迹 |
| lib/query-keys.test.ts | 2 | 查询键 |
| test/workspace-cache-isolation + optimistic-mutation | 4 | 缓存隔离/乐观回滚 |
| lib/file-preview.test.ts | **11** | 文件预览（docx/pdf/xlsx 预览逻辑） |

**合计：19 文件 / 109 用例（全部通过，vitest 4.1.10）。**

**前端无任何 E2E（无 Playwright 配置/目录）；所有测试均不断言 /api/v1 路径（client 层 mock），前后端之间无契约/集成测试。**

---

## 〇、测试运行结果（2026-08-09 实测）

| 套件 | 命令 | 结果 |
|---|---|---|
| **后端全量（含补测）** | `uv run --locked pytest tests/`（backend/） | **124 passed + 3 xfailed（T1-1/T1-3/性能预算锚点）+ 0 failed** ✅（约 3 分钟） |
| **前端全量（含补测）** | `node node_modules/vitest/vitest.mjs run`（frontend/） | **22 文件 / 127 tests 全部通过** ✅ |

- 两套现有测试全部绿灯，但**覆盖范围极小**（见下）。
- 后端 conftest.py 隔离设计良好：强制临时库 + 双时机校验引擎未绑定真实库（防 drop_all 删真实数据）——测试基建可靠。

## 补测内容（2026-08-10，全部走 git 旁路，永不入库）

### 后端新增 `backend/tests/api/`（10 文件 / 61 用例，git 跟踪数=0 ✅）
| 文件 | 覆盖 |
|---|---|
| test_auth_http.py（8） | 注册/登录/me/工作区/登出、401 快速失败、缺头 422、锁定 429（T1-2）、改密、会话吊销、注册校验 |
| test_chat_http.py（8） | 会话 CRUD、流式事件序（accepted→started→delta→completed，A1-1/A1-2）、消息持久化、Idempotency-Key 去重（T1-1 正面）、并发会话创建幂等（T1-1 xfail 锚点）、分页结构（B1-2）、取消路由、422 校验 |
| test_goals_graphs_http.py（9） | goal 确认/规划、删除影响、图谱列表/单查/节点 PATCH（含 expected_revision）、节点问题、merges、**脏数据容错（T1-3 xfail 锚点：availability 字符串 → confirm 500 已复现）** |
| test_files_http.py（6） | 解析能力、上传/列表、内容/分块、空文件观察项、存储摘要、删除（confirmation 文案） |
| test_providers_http.py（5） | catalog、CRUD 全周期（**密钥掩码断言**）、不可达模型发现错误、**密钥不进错误响应** |
| test_memory_http.py（6） | 列表/策略/视图/profile、CRUD、草稿、V2 检索校验、架构状态、context/build |
| test_isolation_http.py（5） | **跨用户工作区隔离 403/404、跨用户资源 404、research/egress 越权被拒（S1-1/S1-2 回归锚）** |
| test_usage_openapi_http.py（4） | usage/dashboard/health、**OpenAPI 全端点无 token 401 不变量（~438 端点遍历）**、缺 workspace 头 4xx 不变量 |
| **test_member_escalation_http.py（7）** | **组织内成员越权完整形态**：A(Admin) 建组织工作区 + B(Member) 加入 → B 对 A 的 fetch 审批 decision/resume、research 读取/批准/列表 全部 403；A 本人操作 200（S1-1/S1-2 修复固化） |
| **test_perf_baseline.py（5）** | **性能回归基线**：登录并发 10 P95<3s 灾难线（实测 1.48s）+ 预算 800ms xfail 锚点、流式管线首事件<2s、历史分页 limit 窗口+<2s、health P95<200ms |

### 前端新增（3 文件 / 18 用例，git 跟踪数=0 ✅）
| 文件 | 覆盖 |
|---|---|
| components/graph/knowledge-graph-layout.test.ts（9） | **图谱布局纯函数**：层级构建/无效边过滤/单父/环检测/contains 优先/root 保护/深度/active path/descendants（F5.1/F5.3 相关） |
| features/chat/chat-message-parts.test.ts（4） | **消息分区**：chain/reasoning 判定、排序、分组 |
| features/chat/stream-stats.test.ts（5） | **流统计**：生命周期/注册表有界/token 估算/格式化/delta 提取 |

### 补测过程中新确认的发现
- **T1-3 复现加深**：availability 列以裸字符串入库（脏数据）时，**读行即 JSONDecodeError → confirm 500**（比"列表 500"更早炸，读行层无容错）。
- **确认接口的 availability 合并逻辑**（goals.py:764 `dict(getattr(goal, ...))`）对非 dict 旧值直接 ValueError → 500，缺少类型守卫。
- 会话创建返回 201（非 200）、删除需 confirmation 文案=文件名、planning 只接受 GoalPlanningUpdate 字段——测试修正了 8 处断言以贴合真实契约。
- **S1-1/S1-2 已在工作树修复**（fetch decision/resume + research 均带 owner/manage 校验）——test_member_escalation_http.py 固化验证 7/7 通过。
- **前端测试文件会被 `tsc -b` 纳入构建**：新测试的 TS 类型错误会直接导致生产构建失败（本次实测拦截）。测试文件类型必须严格正确。

## E2E 冒烟（任务 1，git 旁路：audit/scripts/smoke_e2e.mjs）
| 目标 | 结果 |
|---|---|
| 生产构建（5175） | **修复后 5/5 PASS，0 pageerror** ✅（登录→注册→首页→图谱→对话全链路） |
| 用法 | `node audit/scripts/smoke_e2e.mjs [BASE_URL]`；证据 audit/evidence/e2e/ |

*注：dev 冒烟需 `LEARNGRAPH_BACKEND_ORIGIN=http://127.0.0.1:8002` 起 vite，否则代理打到默认 8000 导致注册 502。*

## 修复记录（2026-08-10，本轮完成）

### P0-1 生产构建崩溃（已修复）
- **根因**：rolldown 对循环 ESM 依赖（streamdown/hast/parse5/mermaid/d3、recharts）静态合并时求值顺序破坏（module-eval TDZ crash）。HEAD 构建同样崩溃（此前 compare_builds 的"HEAD 正常"为假阴性：HEAD worktree 构建产物未正确加载）。
- **修复**：
  1. 前端懒加载隔离（工作树已有）：SelectionExplanationPanel / KnowledgeGraph / LazyStreamdown 已动态 import；
  2. **新增 `chart-part.tsx` 懒加载组件**：recharts 从 message-part-renderer 静态 import 中移除，改为 React.lazy 动态 import → recharts 进入独立按需 chunk，对话页不再 `reading 'axis'` 崩溃；
  3. 依赖回退 HEAD 组合（vite 8.1.4 / rolldown 1.1.5，package.json/lock 恢复 HEAD）——8.2.1/1.2.3 组合构建产物同样崩溃。
- **验证**：生产冒烟 5/5 全绿、0 pageerror；前端全量 127 tests 通过。

### T1-1 会话创建幂等（已修复固化）
- 发现 create_session 已支持 Idempotency-Key（工作树修复）；补强竞态：`idempotency_key_hash` 加唯一约束（models.py + database.py 部分唯一索引迁移）+ create_session 捕获 IntegrityError 回滚重查返回已有会话。
- 验证：并发同 key 双发仅 1 条（5 连过）；无 key 双发 2 条（对照观察，符合语义）。

### T1-3 脏数据容错（已修复固化）
- 新增 `LenientJSON` TypeDecorator（models.py）：JSON 列读行时脏数据（裸字符串/非容器）降级空容器，不再 JSONDecodeError→500；Goal 的 availability/preferences/constraints/assumptions 应用；goals.py 已有类型守卫（工作树）。
- 验证：availability=字符串 的脏 goal confirm 返回 200（原 500）。

### 回归结果
- 后端全量：**131 passed + 1 xfailed（性能预算锚点，非缺陷）+ 0 failed**
- 前端全量：**22 文件 / 127 tests 全绿**
- 测试内容全部 git 旁路（跟踪数 0）；源码修复正常入库。

## 性能回归基线（任务 2，git 旁路：backend/tests/api/test_perf_baseline.py）
4 passed + 1 xfailed：登录并发 10 P95<3s 灾难线（实测 1.48s）✅、登录预算 800ms xfail 锚点、流式管线首事件<2s、历史分页 limit 窗口+<2s、health P95<200ms。


---

## 二、接口覆盖矩阵（后端 31 router / ~438 端点）

| Router | 端点数 | 测试覆盖 | 覆盖详情 |
|---|---|---|---|
| health | 1 | ❌ | 无（健康检查无测试） |
| auth | 28 | ❌ 0% | 登录/注册/会话/工作区/权限全部无测试 |
| dashboard | 1 | ❌ | 无 |
| goals | 8 | ❌ | 澄清/确认/候选图谱/发布全部无测试 |
| graphs | 12 | ❌ | 节点 PATCH/合并/撤销/多节点学习无测试 |
| chat | 24 | ❌ | **流式/消息/取消/重试/分支/自动标题全部无测试**（核心链路！） |
| image_generations | 2 | ❌ | 无 |
| files | 17 | ❌ | 上传/解析/分块/转写/引用无测试 |
| document_learning | 7 | ❌ | 文档任务/查询预览无测试 |
| fetch_authorizations | 2 | ❌ | 抓取审批无测试 |
| egress_approvals | 4 | ⚠️ 逻辑层 | 策略推导有服务层测试；4 个 HTTP 端点无测试 |
| research | 8 | ❌ | 研究任务/审批/取消无测试 |
| sources | 5 | ❌ | 抓取/白名单无测试 |
| subapps | 15 | ⚠️ 逻辑层 | 发布契约/事件/会话/限流有服务层测试（test_subapp_agent_publish 16 例）；HTTP 端点无测试 |
| evidence | 11 | ❌ | 证据/掌握度/调度无测试 |
| exercises | 3 | ❌ | 无 |
| memory | 42 | ⚠️ 逻辑层 | 抽取/可见性/读取模式有服务层测试；**42 个 HTTP 端点（CRUD/草稿/策略/导出/维护/搜索/context）无测试** |
| memory_v2 | 12 | ❌ | 无 |
| tasks | 4 | ❌ | 无 |
| episodes | 4 | ❌ | 无 |
| mcp_skills | 61 | ⚠️ 部分 | 技能权限边界 5 例（HTTP）；OAuth/密钥服务层 16 例；**其余 ~50 端点（市场/导入/升级/沙箱运行/翻译/审计）无测试** |
| plugins | 2 | ❌ | 无 |
| providers | 31 | ❌ | **Provider CRUD/探测/余额/密钥轮换全部无测试** |
| usage | 22 | ❌ | 用量/预算/价格/告警无测试 |
| sandbox | 35 | ⚠️ 逻辑层 | egress 策略有测试；**沙箱会话/任务/命令/文件/引导 35 端点无测试** |
| workflow | 29 | ❌ | 项目/会话归档/路线/动作无测试 |
| migrations | 11 | ❌ | 存储迁移/备份恢复无测试 |
| audit_settings | 6 | ❌ | 审计日志/导出无测试 |
| artifact_gateway | 8 | ❌ | 制品/分享令牌无测试 |
| components | 9 | ❌ | 组件注册/授权/事件无测试 |
| learning_state | 2 | ❌ | 无 |

**结论：HTTP 级端点覆盖 ≈ 5-8 / 438 ≈ 1.5-2%**（仅 skills 权限 5 例）。服务/领域逻辑覆盖 2 个领域（记忆、安全）中的部分模块。

---

## 三、功能点覆盖矩阵（用户任务 × 页面）

| 用户任务（审计盘点 28 项） | 后端测试 | 前端测试 | E2E |
|---|---|---|---|
| 新用户首次进入/注册 | ❌ | ⚠️ 仅 auth-messages/password-rules 助手函数 | ❌ |
| 已有用户继续学习/登录 | ❌ | ⚠️ 仅 authStore/client 作用域 | ❌ |
| 创建学习目标（澄清→确认→候选图谱→发布） | ❌ | ❌ | ❌ |
| 图谱生成/编辑/节点拆分 | ❌ | ❌ | ❌ |
| 图谱节点学习/多节点研究 | ❌ | ❌ | ❌ |
| Agent 对话（流式/取消/重试/分支/子会话） | ❌ | ⚠️ SSE 解析、选中解释（非主链路） | ❌ |
| 文件上传/解析/转写/文档问答 | ❌ | ❌（file-preview.test.ts 空） | ❌ |
| 图片生成/编辑 | ❌ | ❌ | ❌ |
| 前端组件生成/信任渲染 | ❌ | ✅ trusted-renderer 9 例 | ❌ |
| 子应用（subapp/沙箱制品/媒体） | ⚠️ 逻辑层 16 例 | ✅ 48 例（最大覆盖区） | ❌ |
| 模型配置/Provider/API Key | ❌ | ❌ | ❌ |
| 沙箱创建/执行 | ⚠️ 仅 egress 策略 | ❌ | ❌ |
| 浏览器任务/网页抓取/SSRF | ⚠️ 仅策略推导 | ❌ | ❌ |
| 记忆管理（CRUD/草稿/导出/策略/检索） | ⚠️ 仅抽取/可见性/读取模式 | ❌ | ❌ |
| 研究任务（deep research/审批/取消） | ❌ | ❌ | ❌ |
| 练习/复习/作答 | ⚠️ 生产者 1 例 | ❌ | ❌ |
| 证据审核/掌握度 | ❌ | ❌ | ❌ |
| 用量/预算/价格/告警 | ❌ | ❌ | ❌ |
| 权限/组织/角色/ACL | ⚠️ skills 权限 5 例 | ❌ | ❌ |
| 出网/抓取审批 | ⚠️ egress 策略 5 例 | ❌ | ❌ |
| 审计日志/存储迁移/备份 | ❌ | ❌ | ❌ |
| 消息版本/修订/恢复 | ❌ | ❌ | ❌ |
| 会话取消/多任务并行/刷新恢复 | ❌ | ❌ | ❌ |

**前端页面组件测试覆盖：30 个页面中仅 3 个有测试（dashboard、personalization、以及 chat 的选中解释面板）；图谱页、chat 主画布、目标页、资料页、记忆页、Provider 页、研究页、设置大部、制品页全部无测试。**

---

## 四、关键结论

1. **接口覆盖不足 2%**：438 个端点中约 5-8 个有 HTTP 级测试；核心链路（chat 24、auth 28、providers 31、sandbox 35、workflow 29、memory 42）**零 HTTP 测试**。
2. **功能点覆盖严重失衡**：全部测试集中在 记忆(后端) + 安全(后端) + 子应用(前后端) 三个方向；审计发现的 34 个问题中，**绝大多数没有对应回归测试**：
   - 登录并发（B1-1）→ 无并发测试
   - SSE 心跳/阶段反馈（A1-1/A1-2）→ 无流式测试
   - 历史分页（B1-2）、Provider 复用（B1-3）、N+1（B1-4）→ 无性能/查询测试
   - 会话幂等（T1-1）、Idempotency-Key → 无幂等测试（仅审计脚本实测过）
   - 越权（S1-1/S1-2）→ 无多用户权限测试
   - 生产构建崩溃（P0-1）→ **无构建冒烟/E2E 防线，同类回归会再次漏出**
3. **无 E2E 层**：前后端集成、页面级链路、生产构建验证均无自动化。
4. **前端 file-preview.test.ts 为空文件**（0 用例，疑似占位未完成）。
5. 测试库隔离设计良好（conftest 强制临时库 + 防真实库 drop_all 校验），后端测试可直接运行。

## 五、建议（按优先级）

| 优先级 | 补充项 | 覆盖目标 | 对应审计发现 |
|---|---|---|---|
| P0 | 生产构建冒烟（Playwright：登录→首页→图谱→对话 在构建产物上跑） | P0-1 回归防线 | P0-1 |
| P0 | chat 流式端到端测试（发送→SSE 事件序列→完成→取消→幂等） | 核心链路 + 幂等 | A1-1/A1-2/A1-4/T1-1 |
| P1 | auth 接口测试（登录/注册/会话/锁定/限流） | 认证面 | B1-1/T1-2/S1-3 |
| P1 | 权限越权测试（双用户：审批/研究/任务 owner 校验） | 安全底线 | S1-1/S1-2 |
| P1 | 图谱接口+页面测试（节点 PATCH/合并/300 节点渲染） | 核心功能 | U1-1/F1-3 |
| P2 | providers/sandbox/workflow/memory HTTP 层冒烟 | 大面积空白 | B1-3 等 |
| P2 | 性能回归基线（登录 P95、首 token、历史分页） | 防劣化 | B1-1/B1-2 |
| P2 | 前端页面组件测试补齐（图谱/chat 主画布/目标/记忆/Provider 页） | 页面覆盖 <10% | — |
| P3 | 脏数据容错、FTS 一致性、多进程互斥测试 | 稳定性 | T1-3/B1-9/B1-7 |

*注：后端 tests/ 被 .gitignore 忽略，需 `git add -f` 强制入库（先例：test_subapp_agent_publish.py）。*
