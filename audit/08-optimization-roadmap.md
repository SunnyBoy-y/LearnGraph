# 08 - 优化路线图（Optimization Roadmap）

> 每个建议给出：当前行为 / 当前耗时 / 主要瓶颈 / 建议修改位置 / 技术方案 / 预期收益 / 副作用 / 验证方式 / 是否上线前完成。

## A. 快速收益项（改动小、见效快）

| # | 项目 | 当前行为/耗时 | 瓶颈 | 建议修改 | 方案 | 预期收益 | 副作用 | 验证 | 上线前 |
|---|---|---|---|---|---|---|---|---|---|
| A1 | **修复生产构建崩溃（P0-1）** | 构建成功但白屏，100% 崩溃 | 未提交修改引入的打包回归（疑似 stream-stats/chat 模块循环依赖，rolldown 求值顺序） | 定位触发回归的未提交修改文件（stream-stats.ts、stream-stats-badge.tsx、chat-message-parts.ts、message-part-renderer.tsx） | 修复循环依赖或调整构建配置；建立"生产构建冒烟"CI 步骤 | 恢复生产可用性（当前完全不可用） | 需谨慎改动，回归面=全部 | Playwright 生产构建冒烟（登录→首页→图谱→对话） | **必须** |
| A2 | 登录并发劣化（B1-1） | 并发 50 → P50 5.2s | 密码哈希 + 会话写 SQLite 单写者排队 | auth service + database.py | 登录 last_seen 写降频/批量；会话创建减少同事务写入；必要时登录用独立写队列 | P50 从 5.2s→<1s | 低 | 复用并发探针（audit/scripts/run_api_probes.py） | **必须**（限流配套） |
| A3 | 认证限流 + demo 默认关（S1-3） | 5 次错密码锁 15min（账号 DoS）；注册无限制 | 无 IP 级限流 | auth.py + config.py | IP 滑动窗口限流；注册加邮箱/邀请开关；enable_demo_login 默认 False | 消除账号 DoS 与注册灌水 | 低 | 复用爆破/注册探针 | **必须** |
| A4 | 审批/研究越权（S1-1/S1-2） | 任意成员可代决定/恢复付费任务、读他人研究 | 缺 actor 校验 | fetch_authorizations.py / research.py | 与 egress_approvals.py:369 对齐：decision/resume/approve/cancel 加 `requested_by==actor or workspace.manage`；list 默认本人 | 消除越权付费与敏感内容泄露 | 低 | 双账号 API 越权用例 | **必须** |
| A5 | SSE 心跳（A1-1） | 工具/退避期 60s+ 无字节 | 无 keep-alive 事件 | api/routers/chat.py + services/chat.py | 工具执行与退避期间按 5s 发 `: keep-alive` 或 tool.running 事件；sleep 改心跳循环 | 消除静默断流，符合上线门槛"长任务明确反馈" | 低 | 带工具任务抓 SSE 时间戳 | **必须** |
| A6 | 上传配额（S1-4） | 上限 20GiB 无配额 | 配置默认值过大 | config.py + files.py | 默认 512MiB + 工作区配额硬拒绝 | 防磁盘耗尽 | 低 | 超配额上传用例 | **必须** |
| A7 | 首 Token 前阶段反馈（A1-2） | 搜索/构建上下文静默最多 ~30s | initial_events 时序 | chat.py | preflight 后立即发 message.accepted + phase.searching；搜索并行化 | 消除首 token 前静默 | 低 | 流式计时探针 | 建议 |
| A8 | 错误文案可指导（U1-3） | 500 → "Internal Server Error 重试" | 错误映射未下沉到 UI | 前端错误组件 + 后端 error.code 清单 | 映射 error.code 到可理解文案 + 原因/重试/配置指引 | 用户可判断状态 | 低 | 错误场景 UI 检查 | 建议 |
| A9 | 心跳随隐藏暂停（U1-2 部分） | auth/me 26/min 含后台 | AuthProvider 裸 setInterval | auth-context.tsx:90-92 | document.hidden 时跳过心跳 | 减少后台请求 | 低 | Network 采样 | 建议 |

## B. 中等改造项（局部架构调整）

| # | 项目 | 当前行为 | 瓶颈 | 建议修改 | 方案 | 预期收益 | 风险 | 验证 | 上线前 |
|---|---|---|---|---|---|---|---|---|---|
| B1 | 历史分页 + 复合索引（B1-2） | 每轮全量读消息+大 JSON 列 | 无 LIMIT/无复合索引 | chat.py _session_timeline/list_messages_page + models.py:493 | SQL 层分页；parts/provider_trace deferred；补 (workspace_id, session_id, created_at) 索引；分支祖先缓存 | 长会话历史读取 O(page) | 中 | 500+ 消息会话性能用例 | 建议（数据量大前） |
| B2 | Provider 栈复用（B1-3） | 每消息 15-25 条前置查询 + 新 TLS | 无进程级缓存 | factory.py + openai.py | 按 (workspace, provider, updated_at) 缓存解析；httpx.Client 进程级共享 | 首 token 前置耗时显著下降 | 中（缓存失效需按配置变更） | 首 token 计时对比 | 建议 |
| B3 | 消息级幂等扩展（T1-1） | 会话创建双发=2 条 | 无幂等键 | chat.py create_session | POST /sessions 支持 Idempotency-Key（消息层已有实现可复用） | 双击/重试不再产生重复会话 | 低 | 并发双发探针 | 建议 |
| B4 | 列表批量鉴权（B1-4） | N+1 鉴权 3-4 查询/项 | 逐项 can_access_resource | authorization.py + 列表服务 | 批量 IN 过滤可访问 ID | 列表随数据量线性劣化消除 | 中 | 50 项列表耗时对比 | 建议 |
| B5 | /parse 异步化（B1-8） | 请求线程阻塞数秒-数十秒 | 同步 execute_job | files.py:619-643 | /parse 改入队（复用 document-jobs 机制）+ 轮询 | 大文件解析不再阻塞线程池 | 中（前端需适配任务轮询） | 大文件解析期间其他接口响应 | 建议 |
| B6 | 图谱节点 memo + 布局复用（F5.1/F5.2, U1-1） | 折叠全节点重建；选择 1.7s | nodeTypes 未 memo；双倍布局 | knowledge-graph.tsx | nodeTypes 包 memo；data 引用稳定；skeleton/最终布局共用 hierarchy | 大图谱交互显著提速 | 低-中 | 300 节点选择/折叠计时 | 建议 |
| B7 | 工具定义缓存 + 历史增量（A1-3） | 每轮全量重发工具+转录 | 无轮级缓存 | agent_runtime.py + chat.py | 工具定义按版本缓存；结构化转录按 token 预算滑窗 | 多轮 Agent 延迟/成本二次方劣化消除 | 中 | 5 轮工具链请求体大小对比 | 上线后 |
| B8 | 工具宿主超时（A1-5） | 单工具悬挂卡死整链 | 无超时包装 | chat.py:12411 + agent_runtime.py:5949 | 工具级宿主超时 + future.result(timeout) | 悬挂可控 | 低 | 悬挂工具注入测试 | 上线后 |

## C. 架构升级项（系统设计变化）

| # | 项目 | 当前 | 方案 | 收益 | 建议时机 |
|---|---|---|---|---|---|
| C1 | 持久化任务队列 + 可恢复 Agent | 内存 detached 线程；关页继续计费 | 任务/消息级持久化状态机 + 队列（durable queue 已有基础）；关页策略（默认取消/后台继续二选一透明化） | 计费透明 + 崩溃恢复完整 | 上线后第一优先 |
| C2 | 多进程部署安全（B1-7） | 多进程重复 sweep | DB 级互斥/leader 选举，或文档明确单进程约束 | 水平扩展可行 | 上线后（团队版前） |
| C3 | SQLite→PostgreSQL 路径（B1-1/B1-6） | 单写者锁排队 | 基础设施依赖已声明（psycopg/minio/redis）；按部署规模切换 | 登录/写并发大幅提升 | 多用户上线前评估 |
| C4 | 全链路 Trace + 性能预算监控 | 无分层耗时观测 | 请求级 trace（各阶段耗时）+ 性能预算断言（登录 P95/首 token/图谱交互） | 延迟问题可归因 | 上线后 |
| C5 | 图谱分层加载/虚拟化（F5.3） | 1000+ 节点全量 DOM | onlyRenderVisibleElements + 按 zoom LOD 裁剪 | 大图谱流畅 | 上线后 |
| C6 | 多租户资源配额（S1-4 延伸） | 无配额 | 工作区级配额（存储/调用/预算）+ 对账 | 防单用户耗尽全局 | 上线后 |

## 实施顺序建议
1. **立即（阻断）**：A1（P0 构建崩溃）→ A4（越权）→ A3（认证限流/demo）→ A6（上传配额）→ A5（SSE 心跳）。
2. **首轮上线配套**：A2（登录并发）→ A7/A8/A9 → B3（会话幂等）。
3. **上线后 2-4 周**：B1/B2/B4/B5（数据规模防护）→ B6（图谱交互）→ C1（计费透明）。
4. **团队版前**：C2/C3/C4/C6。
