# PostgreSQL + Redis 全链路迁移评估

评估日期：2026-09-18。目标按用户确认的“多人团队、更高并发、允许一次维护停机”制定。本文是代码与方案评估，没有执行数据库迁移或修改运行配置。

## 结论

方向合理，建议推进，但应拆成“PostgreSQL 兼容与安全迁移”和“多实例运行及 Redis 削峰”两部分验收。PostgreSQL 保存业务事实、任务状态、租约和事件；Redis 承担共享限流、任务投递通知、热点缓存和可重建的实时通知。

当前不具备直接切换条件。数据库配置页能探测 PostgreSQL，不能执行 PostgreSQL 数据导入或运行时切换；Redis 仅做 PING 探测。工程量主要在搜索语义、事务及多进程行为、迁移验证和恢复，而不在连接字符串。

完整目标的基础工作量估计为 **44–68 人日**；增加约 20% 风险余量后为 **53–82 人日，预算建议按 55–85 人日安排**。两名熟悉项目的后端工程师、测试/运维兼职参与，预计 **7–10 个日历周**；单人约 **11–17 个工作周**。这是工程判断，不是已验证的实施承诺；没有当前生产压测和全量迁移演练数据。

## 1. 评估依据与边界

主要依据：

- [现有全量迁移方案](../doc/LearnGraph_SQLite全量迁移PostgreSQL升级方案_v1.0.md)：仍标记为设计方案，覆盖主库、sandboxd、部署、备份、回退，没有 Redis 实施章节或人日估算。
- [历史并发压测](../doc/LearnGraph_并发压测报告_v1.0.md)：2026-08-25，旧测试镜像；20 路并发曾有 1 次连接池耗尽。不能当作当前版本吞吐上限或失败率。
- [长期记忆方案](../doc/long-term-memory-system-implementation-plan.md) 第 1288 行：Redis 后续只做调度/通知，DB Outbox 为恢复依据，与本建议一致。
- 当前工作区源码，包括尚未提交的学习包功能；未覆盖用户现有改动。源文件行号均以本次检查为准。

完成了源码搜索、模型 AST 统计、独立 SQLAlchemy 元数据的 PostgreSQL DDL 编译，以及三个本地 SQLite 文件的只读元数据/计数检查。未连接生产 PostgreSQL/Redis，未启动真实迁移，未运行跨库业务集成测试或新一轮负载测试。

主库当前 **181 张 ORM 表**：核心模型 137、扩展 13、Memory 16、迁移台账 8、学习包 7。另有 sandboxd 的 5 张状态表，以及主库三套 FTS5 投影和其内部表。原方案“约 165 表”已过时，不能作为导入白名单。

本地 `backend/data/learngraph.db` 逻辑大小约 10.9 MiB，含 FTS 内部表共 190 张表，但只有 6 条 message、33 条 stream event、85 条 memory event；本地 sandboxd 库约 56 KiB，sandbox/execution 计数为 0。根目录 `data/learngraph.db` 无业务表。这些是本机样本，**未确认等于 Docker 生产卷**，不足以估算正式停机时间或容量。

## 2. 当前实现与目标之间的差距

### 2.1 数据迁移界面仍是控制面骨架

[migrations.py](../backend/app/services/migrations.py) 第 723 行拒绝非 SQLite/local 执行器，返回 `migration_executor_unavailable`；第 924 行数据库 commit 返回 `database_runtime_rebind_unavailable`。不能把“连接探测通过”当成“支持生产迁移”。

建议保留预检、进度、报告能力，用独立迁移命令与服务重启完成切换。既然允许维护停机，就不必在首版实现进程内热替换全部 Engine/Session。

### 2.2 ORM 有复用价值，但数据库语义仍不兼容

181 张表及声明索引可以离线编译成 PostgreSQL DDL，说明不用整体重写 ORM。该结果只证明可生成 SQL，不证明真实建表、历史升级或业务正确。

已复现确定性问题：[models.py](../backend/app/domain/models.py) 第 3260 行的 `uq_egress_auth_request_pending` 只声明 `sqlite_where`。SQLite 输出包含 `WHERE status = 'pending'`，PostgreSQL 输出没有该条件，变成全状态唯一。历史终态审批会阻止相同键再次申请，必须补齐 `postgresql_where` 并检查存量索引。

[core/migrations/__init__.py](../backend/app/core/migrations/__init__.py) 第 158、210 行历史 ADD COLUMN 仍含 `DATETIME`；部分迁移对非 SQLite 直接返回。新空库因 `create_all` 可能绕开这些分支，不能据此认定旧库升级路径有效。

还须检查：LenientJSON 原文保留、SQL NULL 与 JSON null、UTC/时区、VARCHAR 实际长度、布尔/整数范围、二进制、排序与 NULL 顺序、序列校准、唯一键与外键。首版不要同时大规模把所有 JSON/TEXT 改成 JSONB。

### 2.3 三套全文检索是必做项

- 会话检索：[session_retrieval.py](../backend/app/services/session_retrieval.py) 第 209 行非 SQLite 直接 503。
- 文档检索：[document_learning.py](../backend/app/services/document_learning.py) 第 631、676、1119 行直接写 FTS 表并调用 MATCH/bm25。
- Memory：[memory_projector.py](../backend/app/services/memory_projector.py) 第 124、344、352 行非 SQLite 跳过 FTS；[memory_retrieval.py](../backend/app/services/memory_retrieval.py) 第 240 行跳过 FTS 评分。降级返回不等于检索质量不变。

沿用原方案的 `pg_trgm + GIN + 英文 tsvector` 作为起点合理，但中文 1/2 字查询、无可提取 trigram 的模式可能退化扫描；不能承诺所有中文查询都由索引高效支持。[PostgreSQL pg_trgm 文档](https://www.postgresql.org/docs/current/pgtrgm.html)

先建立中英文、短词、混合词、编辑/删除、active revision、跨工作区隔离的固定测试集，再定义候选召回、Top-K 和稳定排序门槛。BM25 与新评分逐值一致不作为目标。用真实数据的执行计划确认索引生效；必要时增加预分词方案，作为范围变更评估。

### 2.4 单进程假设是团队扩容的主要阻塞

目前 [docker-compose.yml](../docker-compose.yml) 第 38 行明确禁止提高 uvicorn workers。迁库不会自动消除这些约束：

- **启动会误处理其他实例的在途任务**：[main.py](../backend/app/main.py) 第 80–81 行启动时调用恢复函数；[chat.py](../backend/app/services/chat.py) 第 766 行扫描所有 pending/streaming assistant message；[document_learning.py](../backend/app/services/document_learning.py) 第 1465 行扫描所有 running 文档任务。新增或重启实例时可能把仍由另一实例执行的任务标成中断。必须按 owner/heartbeat/lease 判断死亡。
- **调度锁需要原子抢占**：[process_lock.py](../backend/app/core/process_lock.py) 第 46–75 行先读过期再更新，没有旧 token/expiry CAS；release 也先读再删除。并发实例可能都认为获取成功，或旧 owner 删除新 owner 的锁。改为条件 UPDATE/DELETE 或明确生命周期的 PostgreSQL advisory lock。
- **Memory Outbox 的安全模式默认关闭**：[config.py](../backend/app/core/config.py) 第 384 行 `memory_outbox_strict_leases=False`；[memory_outbox.py](../backend/app/services/memory_outbox.py) 非严格 claim 允许 leased 状态；[memory_outbox_runtime.py](../backend/app/services/memory_outbox_runtime.py) 使用固定 worker 名。需唯一 worker ID、严格 generation 校验、续租和失效隔离。
- **消费者仍是串行**：[durable_queue.py](../backend/app/services/durable_queue.py) 第 477 行逐个等待 `run_one_durable_job` 完成。加 Redis 后若不拆 worker，后台吞吐不变。claim 已有 CAS/token 基础，应增强而不是推倒重写。每次处理新建 DurableQueue，内存中的 workspace round-robin 状态不能形成跨任务、跨 worker 的全局公平。
- **共享限流缺失**：[admission.py](../backend/app/core/admission.py) 和 [rate_limit.py](../backend/app/core/rate_limit.py) 都是进程内计数。扩副本会放大全局上限，需进程本地保护与分布式配额共同生效。
- **长连接与实时会话**：[api/routers/chat.py](../backend/app/api/routers/chat.py) 第 151 行为本地队列+线程，独立 worker 的会话覆盖生成过程；[voice/runner_registry.py](../backend/app/voice/runner_registry.py) 第 54 行为本地 registry。语音已具备跨实例关闭的数据库 watchdog，但 WebRTC 会话归属、重连路由和 drain 仍须验证，不能把所有本地状态直接搬进 Redis。

首版明确分离 API、task worker、scheduler；使用统一 owner/lease；SSE 根据持久事件 cursor 恢复，Redis 只帮助通知。语音保留专属进程/实例亲和，不承诺跨进程无缝迁移正在进行的媒体连接。

### 2.5 连接占用要独立整改

[database.py](../backend/app/core/database.py) 第 41 行仅为 SQLite 配置了连接池；PostgreSQL 分支使用 SQLAlchemy 默认参数。不能把现有 SQLite 池参数当成 PG 调优。

历史连接池问题之后，项目已新增 admission gate，超载可以快速返回 429；这改善错误体验，但不代表每条流持有连接的成本已消失。代码注释描述约两路 Session/连接占用，实际 checkout/事务持续时间必须重新采样。

目标：鉴权/准备、批量保存、终态提交分别使用短事务，模型/HTTP/沙箱等待期间释放不必要的数据库连接。按“全部 API 副本 + worker + scheduler + preview + sandboxd + 运维预留”汇总连接上限，不逐服务独立拍数；PgBouncer 只在实测连接压力需要时引入，且复核会话锁与事务级租户上下文。

### 2.6 sandboxd 与部署链路不可漏掉

[sandboxd/store.py](../sandboxd/sandboxd/store.py) 使用直接 sqlite3、问号参数、INSERT OR REPLACE、RLock，需独立 PostgreSQL Store 和幂等/取消/执行完成竞态测试。建议一个 PostgreSQL 实例、主库与 sandboxd 两个逻辑库、隔离角色；这是运维起点，不是故障隔离或高可用承诺。

[Dockerfile](../Dockerfile) 第 42 行只装 voice extra；psycopg/redis 在可选 infrastructure extra。[health.py](../backend/app/api/routers/health.py) 仍返回硬编码 sqlite，livez 不检查数据库。[docker-update.sh](../scripts/docker-update.sh) 仍按 SQLite 备份与旧镜像回滚。以上均需接通，/data 中附件、Memory 文件、主密钥和 sandbox volume 数据继续保留。

## 3. 对原方案的具体评价

应保留的设计：双库边界、兼容桥接版本、全局停写、一致快照、离线导入、事实与派生索引分离、全量校验、第一笔 PG 写入前后的不同回退策略、真实恢复演练。

需要修订的设计：

1. **更新规模与业务矩阵**：181 张主库表，增加学习包生成/批改/租约、实时语音、共享限流、实例启动恢复。主库表清单从当前 schema 与 ORM 双向生成，未知表阻断并人工解释。
2. **“只读候选”需专门启动模式**：目前启动会执行 DDL、seed、任务恢复，部分读路径也会触发写入。候选启动禁止这些动作，管理诊断可以显示候选状态，流量 readiness 保持拒绝；写 API 在隔离可写测试库测试，不能要求只读候选测试所有写功能。
3. **收紧第一次写入界线**：现有方案在 canary 成功后才记录 first_write。实际上启动副作用或 canary 首次写入便可能产生分叉。应在授权 runtime 写入前持久记录切换边界，并将任何成功 PG 写入视为不能直接回 SQLite；canary 失败也不能自动回旧库。
4. **恢复不能等同零丢失**：恢复到切换前或较早 dump 会丢失之后提交，除非另有可恢复至所需位置的 WAL/事件。禁止“恢复旧 PG 备份”仍宣称保留全部新写入。原方案把异机 WAL archive/PITR 与近零 RPO 放在一起，需补充归档延迟、故障模式与同步确认配置；异步归档不天然是零丢失。[WAL 归档文档](https://www.postgresql.org/docs/current/continuous-archiving.html)；[同步复制文档](https://www.postgresql.org/docs/current/warm-standby.html)
5. **序列只解决唯一编号，不解决提交顺序**：Memory 使用 global_position 游标。并发下事务 A 取得较小位置、事务 B 先提交较大位置，如果消费者越过 B 后 A 才提交，会漏掉 A。现有 [events_after](../backend/app/repositories/memory_events.py) 第 123 行与 [replay](../backend/app/services/memory_event_store.py) 第 258 行需加入该场景测试。这是对 PG 多写者的迁移风险推断，不是本次已复现的生产故障。优先用逐事件 Outbox/幂等投影管理处理状态；若保留全局 cursor，需安全水位或明确的提交序列化方案。PG sequence 也允许空洞，不能“等所有整数填满”。[序列文档](https://www.postgresql.org/docs/current/functions-sequence.html)
6. **RLS 明确启用范围**：已有 [postgresql_memory_rls.sql](../backend/app/core/migrations/postgresql_memory_rls.sql)，但本次未找到 Python 的 set_config/SET LOCAL 接线。若启用该脚本，必须补交易内 tenant/workspace/user 上下文、运行角色隔离、worker 跨租户处理和连接复用清理测试；否则 policy 可能拒绝正常访问。服务层授权始终保留；全库 RLS 重构不纳入首版。
7. **简化不影响保证的部分**：允许停机时不用双写、CDC、热重绑或跨库分布式事务。全量有序逐行规范化哈希可先替代复杂 Merkle 实现，仍保留逐表计数、主键与内容核对。小库整库事务可行，数据大时改可校验分块导入，不预先承诺固定窗口。

## 4. Redis 如何实现削峰填谷

### 4.1 数据分工

PostgreSQL 负责：账号权限、消息/图谱/学习状态、文件元数据、Memory Event/Outbox、durable_jobs、审计、幂等键、任务最终状态及租约。Redis 丢失后，这些事实不受影响。

Redis 负责：tenant/workspace/provider 维度的速率和并发配额、任务 ID 投递、短 TTL 且带版本的热点缓存、实时通知。凭据明文、授权决定、审批结果、已确认消息不能只存在 Redis。

适合缓冲的负载：文档解析、索引重建、学习包构建/批改、Memory 抽取、研究轮询。实时对话与语音不能放进无限积压队列，采用短等待/快速拒绝、明确排队状态与并发上限；保存用户输入、审批、账务/用量事实仍走可靠持久化。

### 4.2 推荐交付顺序

第一步：PG durable queue 使用 `FOR UPDATE SKIP LOCKED` 或安全 CAS，保留 lease token，支持独立有界 worker、优先级和工作区公平。它本身已能削峰。SKIP LOCKED 适合队列表竞争，不应用于一般一致性查询。[PostgreSQL SELECT 文档](https://www.postgresql.org/docs/current/sql-select.html)

第二步：Redis 先上线共享限流与少量实测热点缓存；然后以 Streams 作为可重建的任务通知。不要用换中间件掩盖 SQL/事务问题，也不要首版重写整个 Celery/RQ 式调度框架。

推荐投递链：业务事务同时写业务状态、durable_job 和 dispatch outbox → 提交 PG → dispatcher XADD job_id/generation → worker 按 ID 原子领取 PG lease → 执行 → 提交结果/状态 → XACK。当前 enqueue 自行 commit，需要改造事务边界，否则业务状态与任务创建仍可能分裂。

Redis Streams 使用 consumer group、pending 检查与超时 claim，按至少一次处理设计；重复投递靠 PG 幂等键和 lease/generation 限制。PG 已成功、XACK 前崩溃时，再次投递只能读取已完成结果，不能重复扣费或重复执行有副作用操作。Redis ACK 丢失/数据丢失时，定期按 PG queued/expired 状态重新投递，不能只扫描“从未发送”的 outbox。[XREADGROUP](https://redis.io/docs/latest/commands/xreadgroup/)；[XAUTOCLAIM](https://redis.io/docs/latest/commands/xautoclaim/)

Redis Streams 不能替代外部副作用的幂等协议：调用模型、付费供应商或沙箱命令后崩溃的“不知道是否成功”状态，需供应商幂等键、执行账本或人工可见的恢复策略，不能承诺 exactly-once。

### 4.3 故障、缓存和反压

- Redis 故障：缓存回源时限速/防击穿，任务回退到有界 PG 扫描或暂停消费者；共享限流采用明确的保守策略。不能每个副本各自放开全部全局额度。
- PostgreSQL 故障：核心写入拒绝确认成功，不能改为“先写 Redis 算成功，稍后补库”。AOF everysec 仍存在近期写入丢失窗口。[Redis 持久化文档](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)
- Pub/Sub 只能做可丢通知；重连从 PG 事件日志补齐。它的至多一次语义不适合唯一任务投递或唯一 SSE 重放依据。[Redis Pub/Sub 文档](https://redis.io/docs/latest/develop/pubsub/)
- SSE 保留已有批处理思路（目前 128 条/1 秒及终态立即提交），明确已提交与仅展示的 delta 边界；完成事件与最终内容持久化后才确认终态。跨实例以持久事件 cursor 补齐，不能依赖某一实例的内存队列。
- 缓存键至少包含工作区、资源 revision 和必要的授权范围。提交后失效/更新，短 TTL 加抖动与请求合并，处理回源与失效竞态。权限撤销与 Memory forget 必须绕过或立即失效旧缓存。
- 队列与缓存不共享不受控的淘汰策略：逻辑 DB 不能隔离 maxmemory。按资源预算隔离实例，或专用 noeviction 队列实例，写满后反压；任务 payload 保留在 PG，Redis 只存小型通知。
- 设置每租户待处理上限、队列字节预算、最长排队时长、优先级、最大重试与死信；对长任务统一 heartbeat/lease fencing，不能只有学习包续租。

队列只能把峰值工作延后。只有长期平均到达率低于消费能力时，积压才会消退；持续超载仍需拒绝/配额、减少工作量或扩容。限流不是“提高总算力”。

## 5. 工作拆分与估算

口径：一人日为一名熟悉项目的工程师一天有效工作，含该工作包的开发与局部验证。第 10 项另计跨模块回归和故障演练；不存在按表逐个 CRUD 重写的假设。估算基于维护停机、单 PG 主实例、团队级多 API/worker，非多地域/自动 HA。

1. **清单、生产基线和验收契约：2–3 人日。** 当前两库 manifest、数据质量、并发场景、资源预算、RPO/RTO 与搜索基准。
2. **PG 配置、schema 和 SQL 适配：5–7 人日。** 独立版本化迁移、角色、类型、索引、历史 DDL、schema 检查；Memory RLS 最小接线与隔离验证。
3. **三套搜索替换：5–8 人日。** PG 检索投影、回填、兼容评分与固定测试集。
4. **短事务及并发正确性：5–8 人日。** Session 生命周期、任务 claim/heartbeat/fencing、调度锁、Memory Outbox、游标风险。
5. **API/worker/scheduler 分离与多实例恢复：5–8 人日。** owner 化启动恢复、SSE 跨实例补齐、语音亲和/重连验证、入口 drain、共享资产路径约束。
6. **sandboxd PostgreSQL Store：3–5 人日。** 五表适配、幂等/取消竞态、状态恢复。
7. **迁移器、数据校验和切换流程：5–8 人日。** 两库转换、规范化哈希、索引重建、序列、candidate、失败回退；迁移界面准确显示外部命令进度与结果。
8. **Redis 接入与反压：6–10 人日。** 共享限流、少量热点缓存、Outbox→Streams、恢复补投、ACK/重试/死信、Redis 故障策略。
9. **部署、备份与 readiness：3–4 人日。** 镜像依赖、Compose、连接预算、dump/restore、保留原资产卷、升级脚本、诊断指标。
10. **集成、压测和两轮演练：5–7 人日。** 真 PG/Redis CI、多租户隔离、混合负载、kill/restart、完整数据演练与恢复。

基础合计 44–68 人日；20% 余量后约 53–82 人日。规划预算可取 55–85 人日。先做基线与 PG 垂直样板（聊天保存/重放、一个搜索路径、一个队列任务），3–5 人日后收敛估算；这部分计入上述工作包，不额外叠加。

团队排期参考：第 1 周基线/样板；第 2–4 周 schema、搜索、sandboxd、事务；第 4–6 周 worker 分离、Redis、迁移器/运维；第 6–8 周端到端与演练；预留至第 10 周处理数据/检索/并发问题。schema 与事务接口是依赖点，不可简单用总人日除以人数。上线后 7 日观察可以与收尾并行。

不含的范围：不停机双写/CDC、PostgreSQL 主从自动切换、多地域、全库 RLS 重构、新搜索服务、大规模遗留脏数据清洗、完整多主机沙箱平台。附件和沙箱工作区仍含本地卷依赖，若团队目标要求多宿主无状态扩容，需另评估对象存储/共享文件和沙箱调度，不能仅靠 PG+Redis 宣称完成。

## 6. 推荐实施次序与验收门槛

1. **定基线**：确定真实生产卷、峰值活跃生成数、文档/学习任务到达率、附件体积和可用维护窗口。关注活跃模型流/任务，不只看注册人数或 HTTP QPS。
2. **桥接版本**：保持现网 SQLite，隔离 PG 验证全部业务；独立 migrator，业务启动只验证 revision，禁止 Preview DDL。更新测试 fixture，使 PG 测试使用专用临时库且不会被 [tests/conftest.py](../backend/tests/conftest.py) 第 35 行重新强制为 SQLite。
3. **多实例能力**：先修 owner/lease、启动恢复、短事务和 admission，再扩 API/worker。不能以数据库可连接作为开启多 worker 的开关。
4. **Redis 分功能启用**：从共享限流、可重建缓存开始，再启用 Streams 投递；每一项均有 Redis 不可用/重启/丢数据演练。
5. **全量演练**：真实数据受控副本，两库冻结快照→导入→校验→重建索引→只读候选→隔离写测试→备份恢复，至少两轮；测量阶段耗时与磁盘峰值。
6. **正式维护切换**：关闭新任务入口，排空或明确终止长任务，停所有旧写者（API、preview、scheduler、worker、sandboxd、动态执行等），最终快照；两库全部通过再启 PG 写入，canary 后逐步恢复流量。

正式停机时间应由“排空 + 快照 + 导入 + 索引重建 + 校验 + smoke + 可用回退余量”实测决定。当前本地 10.9 MiB 样本不能支持承诺十分钟或一小时完成。

必须验收：

- 事实表计数、主键集合、规范化内容哈希一致，外键/唯一约束和序列正确；源档案和密钥可恢复。
- 会话/文档/Memory 搜索质量与作用域通过固定测试集；审批反复申请、学习包生成/批改、语音和附件预览可用。
- 并发 claim 单有效 owner；过期 owner 无权提交结果；重启一个实例不会终止其他实例的聊天和文档任务。
- Redis 丢通知/重启、consumer 在提交后 ACK 前退出、PG 短暂不可用、worker 超时、供应商重试均不会丢失已经确认的核心事实；重复副作用有明确处理。
- 以当前版本做 5/10/20/40 路混合场景和基于实际目标的持续/突发负载；这些档位是建议测试点，不是吞吐承诺。先用固定延迟/响应的供应商模拟器隔离数据库成本，再以真实模型进行小规模验证。
- 同时记录成功率、429、首事件/首 token 的 p95/p99、数据库池等待、事务持续时间、队列最老等待、积压回落、Memory lag、重复执行和成本；将模型等待与数据库等待分开。
- 升级脚本使用 readiness/schema 作为接流量门禁，同时保留独立 liveness；PG 写入后的旧库自动回退被技术上阻断，恢复旧备份的数据损失边界可见。

最终建议：批准进入桥接版本与基线阶段；以 PostgreSQL 可靠持久化和多实例正确性作为前置条件，Redis 按测得的热点与突发任务逐项接入。原方案可作为迁移骨架，必须补充团队并发改造、Redis 故障语义及本报告列出的切换/恢复修订，才能成为可执行计划。
