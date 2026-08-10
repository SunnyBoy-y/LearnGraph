# 03 - 后端性能与数据库审计（Backend Performance）

> 方法：后端/数据库代码审计（backend-db 子智能体）+ 运行时并发探针（隔离实例 8002）。

## 1. 接口耗时实测（并发探针，P50/P90/P95/P99）

| 接口 | 并发1 P50 | 并发10 P50/P95 | 并发25 P50/P95 | 并发50 P50/P95 | 错误率 |
|---|---|---|---|---|---|
| GET /health | 16ms | — | — | — | 0 |
| GET /auth/me | 22ms | 30/60ms | 81/155ms | 159/185ms | 0 |
| GET /workspaces | 18ms | — | — | — | 0 |
| GET /goals | 23ms | 64/73ms | 142/203ms | 279/348ms | 0 |
| GET /graphs | 24ms | — | — | — | 0 |
| GET /sessions | 33ms | — | — | — | 0 |
| GET /providers | 22ms | — | — | — | 0 |
| GET /memory | 31ms | — | — | — | 0 |
| **POST /auth/login*** | **—** | **1439/1481ms** | **2421/3919ms** | **5248/8200ms** | 0 |

\* demo-login 实测。**读接口并发扩展性良好（P95 <350ms @50 并发）；登录是唯一重度劣化点（P1：B1-1）**，根因=密码哈希 CPU + 会话写 SQLite 单写者锁排队（30s busy_timeout）。

## 2. 代码级发现

### 接口耗时结构
- **B1-3 [P2/确认]** 每次聊天消息重建 Provider 栈（chat.py:319-474 → factory.py:199-500：6+ provider factory、每 factory 1-3 条 DB 查询 + Fernet 解密 + WorkspaceSetting），httpx.Client 懒建无跨请求复用 → 每条消息前置 ~15-25 条串行查询 + 新 TCP/TLS 握手。建议 provider 解析按 (workspace, provider, updated_at) 缓存 + 进程级 httpx 客户端。
- **B1-4 [P2/确认]** 列表接口 N+1 鉴权（chat.py:485-492 / goals.py:94-101 / files.py:78-99 / dashboard.py:24-39）：每项 can_access_resource 3-4 条查询（authorization.py:161-237）→ 50 项 ≈150-200 条。建议服务层批量 IN 过滤。
- **B1-5 [P2/确认]** 每条消息双重内存检索（chat.py:2133-2150/2503-2516）：legacy loader + v2 builder 都执行（memory_read_mode≠events 时），结构化检索 memory_retrieval.py:94-104 全表 .all() 无 LIMIT + 同步写遥测（context_telemetry.py:23-87）。建议请求内缓存 + LIMIT + 复合索引 + shadow 默认关。
- F4 [低] 同请求重复读同一资源（session require 2 次等）；F6 [低] web search 与模型调用串行（可并行）。
- **B1-8 [P2/确认]** /files/{id}/parse 请求线程内同步 CPU 密集解析（files.py:619-643；document_learning.py:531-650 pypdf/OOXML/OCR）→ 大文件阻塞 FastAPI 线程池。document-jobs 路径（enqueue + worker）是正确形态，/parse 应改为同一机制。

### 数据库专项
- **B1-2 [P1/确认]** 会话历史无限加载（chat.py:7420-7474 _session_timeline 全量 + 分支递归祖先链；list_messages_page 先全量后 Python 截窗）；messages 仅单列 session_id 索引（models.py:493），无 (workspace_id, session_id, created_at) 复合索引；parts/provider_trace 大 JSON 列整列读取（D5）。→ SQL 分页 + deferred 列 + 复合索引。
- D1/D2 [中] 索引缺失（graph_edges 无组合索引、chat_sessions 无 updated_at 索引）；迁移字典（database.py:438-919）只建 ~20 索引且不校验索引存在 → 存量库索引漂移。
- **D3 [中/确认]** 无分页列表：sessions/goals/files/memory list 全量（research 有 limit(100) ✅ 反例）。
- **D4 [✅]** 图谱保存为修订式 PATCH（graphs.py:124-167 + GraphRevision 乐观锁），非全量重写——设计良好。
- **B1-9 [P3]** FTS 触发器写放大（database.py:357-377：自动标题→整会话 FTS 重建）；启动全量 backfill（:378-404）。
- D7 [低-中] node_questions N+1（graphs.py:175-187）。

### SQLite 连接与事务
- **B1-6 [P2/确认]** 请求路径 busy_timeout=30s + 4-5 次重试（database.py:27/94-177）→ 写锁竞争时用户可感知最坏 ~36s。建议区分请求/后台超时、写密集操作（memory access_count、usage_events）异步化。
- C2 [中高] 长事务：memory extraction sweep 单 Session 内对每 workspace 做 LLM 调用 + 改写（scheduler.py:198-288）；detached SSE worker 持 session 覆盖整个模型流。建议每 workspace/会话短事务。
- C3 [中] 写竞争面宽：durable queue 0.25s 轮询（队列空时空转 SELECT）+ outbox 5s→60s + 4 个 scheduler sweep。建议队列空退避 1-5s。

### 后台任务与恢复
- **B1-7 [P2]** 多进程部署时各进程重复注册全部调度器/worker（main.py:50-117），sweep 无跨进程互斥 → 重复写/重复 LLM 调用。建议明确单进程部署约束或 DB 级互斥。
- B2 [✅] 任务持久化 + 启动 reconcile（mark_interrupted_document_jobs / mark_interrupted_message_streams / enqueue_interrupted_chat_resumes / durable queue lease fencing）——崩溃恢复路径健全。
- B3 [低-中] memory outbox：embedding/profile projection 为 unsupported → 死信累积风险（配置侧规避）。

### 资源与隔离
- R1 [中高] = B1-8（请求线程解析）。
- R2 [低-中] 沙箱 cleanup sweep 全表扫描无过滤索引；孤儿对象存储 blob 无回收。
- R3 [低] preview + main 双进程并发 init_database（DDL/FTS 重建/usage 全表 UPDATE）启动期写锁竞争。

## 3. 结论
后端在并发读路径表现良好（P95 <350ms @50），但存在两个系统性模式：**① 请求路径写放大与长事务（登录/记忆/队列）**；**② 全量加载 + N+1 鉴权（列表/历史/检索）**。二者随用户量与数据量线性劣化。最高优先级：B1-1（登录并发）、B1-2（历史无限加载）、B1-3（Provider 重建）、B1-8（同步解析）。
