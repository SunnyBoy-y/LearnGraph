# LearnGraph SQLite / PostgreSQL 兼容与迁移安全审查

审查日期：2026-11-12。
审查对象：当前工作区（包含已有未提交修改），不是单独的 HEAD/release。
范围：主业务 ORM/数据库路由、历史 schema 升级、搜索适配、SQLite → PostgreSQL 迁移、站点备份恢复、测试和 CI。

## 结论

1. 不能认定 PostgreSQL 已经与 SQLite 所有操作完全等价；当前有可定位的升级、默认值、搜索和时区差异。
2. 迁移不是简单导表：已有备份验证、事务级 CDC、行内容校验、最终屏障及源库封存等重要保障；但本次没有取得真实 PostgreSQL 全流程验收结果，不能据此承诺生产无损迁移。
3. 现有 CI 不能强制每次数据库改动均经双库验证。应以“共享模型/业务实现 + 少量显式方言适配 + 双库契约测试 + 不允许跳过的 PG CI”防止漂移，而不是依赖人工同步两份业务代码。
4. 双库代码兼容不等于同时双写两个运行数据库。当前迁移设计是在切换前以 SQLite 为主，切换后以 PostgreSQL 为主；没有反向 CDC，不能直接切回旧 SQLite 当作无损回滚。

## 验证边界

- 未读取真实数据库文件或 .env/凭据，未连接真实 PostgreSQL，未执行迁移、恢复、切库或修改业务代码。
- 三路独立只读审查覆盖兼容性、迁移恢复、测试/CI；关键缺陷另做了隔离复核。
- 两项最小复现使用当前源码中的函数/方法，通过 AST 提取后在内存 SQLite、最小模型/受控路由依赖下执行；不是完整应用或真实 PostgreSQL 验收。
- 现有定向 pytest 测试组曾输出进度，但运行超时，未获得完整通过/失败汇总。不得把进度点或旧测试日志当作本次测试通过证据。

## 发现（按修复优先级）

### 1. P1：旧 PostgreSQL schema 升级包含裸 DATETIME

证据：
- backend/app/core/migrations/__init__.py:158-166：sandbox_executions.cancel_requested_at。
- 同文件 :210-224：subapp_interaction_events.occurred_at。
- backend/app/core/database.py:1119-1125：PG 启动执行 create_all 后继续 apply_schema_migrations。

条件与后果：旧 PostgreSQL 对应表已存在而列缺失时，ALTER 使用 DATETIME，不能作为 PostgreSQL 的普通日期类型执行，升级会失败。当前完整新库可能不进入分支，因此 fresh create_all 测试不能证明历史升级安全。这不等于每次首次 SQLite→新 PG 迁移都必然失败。

建议：统一通过 CreateColumn/SQLAlchemy 类型按 connection.dialect 编译 DDL；一并核查 native_billing.py:56-59 的类型字符串拼接。为缺列旧 PG 建立独立升级 fixture。

验证级别：静态源码/调用链核实，未在真实 PG 执行。

### 2. P1：布尔字段补列猜测 DEFAULT 1，旧数据被错误回填

证据：
- backend/app/core/migrations/voice_reliability.py:37-55：非空、无 server_default 的 Boolean 一律附 DEFAULT 1。
- backend/app/domain/models.py:3503：VoiceSessionRecord.runtime_ready 的 Python default=False。

条件与后果：旧库缺 runtime_ready 时，SQLite 会把旧记录设为 true，与模型默认 false 相反；PG 生成 BOOLEAN NOT NULL DEFAULT 1，也存在布尔默认值类型不兼容。

最小源码复现结果：
- SQLite legacy runtime_ready: 1 ; model default: False
- PG generated DDL: runtime_ready BOOLEAN NOT NULL DEFAULT 1

建议：为每个新增非空列明确声明历史数据回填规则；静态 Python 默认值按方言编译 literal，不按类型猜测。动态/callable 默认值和业务回填应单列迁移逻辑。

验证级别：SQLite 错误回填已在内存最小模型复现；PG DDL 已编译展示，但未连接 PG 执行。

### 3. P1：SQLite 全站恢复缺少持久化崩溃恢复日志

证据：
- backend/app/services/site_restore.py:127-131：先替换受管目录。
- 同文件 :162-177：后恢复数据库、会话状态、环境配置。
- 同文件 :100-101,178-197：回滚清单主要保存在内存，普通 Exception 才触发回滚。
- backend/app/core/database_routing.py:249-251：启动阻断依赖 site-restore.json，而该 SQLite 恢复路径未建立同等级 journal。

条件与后果：目录切换后发生断电/进程强制退出等，可能出现旧 DB+新文件或新 DB+旧密钥配置，重启未必 fail-closed。普通异常回滚不能覆盖进程丢失。这是 SQLite 全站恢复的安全缺口，不应误报成 PG CDC 本身必然丢行。

建议：参考 site_restore_postgresql.py 的持久 journal/提交标记/启动互锁；在每个替换边界注入进程中断测试。

验证级别：静态审查，未对业务文件做故障注入。

### 4. P2：路由在持锁期间等待连接池，阻塞连接归还

证据：backend/app/core/database_routing.py:298-305,322-331,347-356。

原因：connect/raw_connection 持有 _condition 等物理池槽；checkin/invalidate 回调也需同一把锁。

最小源码复现：pool_size=1、max_overflow=0、pool_timeout=0.3，C1 持有连接，另线程 C2 checkout 后立即归还 C1：
- checkin blocked seconds: 0.33
- competing checkout: ['TimeoutError']

后果：本可由 C1 归还释放的槽位不能及时释放，直到 C2 超时。影响 SQLite 和 PostgreSQL 的可用性及迁移 drain，不等同于已证明数据损坏。

建议：不要持有路由 condition 做阻塞 checkout；同时设计“待获取连接计数/路由代次/租约”避免释放锁后引入切库竞态，并补充满池+切换并发契约。

验证级别：当前源码方法在最小受控路由/内存 SQLite 下已复现。

### 5. P2：PostgreSQL 文档检索未覆盖 SQLite 的章节继承投影

证据：
- backend/app/services/document_learning.py:651-684：FTS content 包含继承章节路径与正文。
- 同文件 :1114-1116：PG 改走适配查询。
- backend/app/core/postgresql_search.py:68-74：仅匹配 FileTextChunk.content。

条件与后果：长 Markdown 章节切成多个块，后续块正文没有重复标题，章节词仅在 section_path 时，SQLite 能召回而 PG 漏召回。记录可完整迁移，但用户看到的搜索行为不等价。

建议：统一 searchable_text 定义/投影；PG 搜索投影表或包含 section_path 的同一检索表达式，并为中英文、标题继承、通配符、NULL 建共用结果契约。若排序算法有意不同，应明确排序容差与召回要求。

验证级别：静态数据构造/查询链核实，未做真实双库差分测试。

### 6. P2：去除 UTC 时区后与 PG 带时区列比较，依赖会话时区

证据：
- backend/app/services/memory_retrieval.py:90：UTC now 被 replace(tzinfo=None)。
- 同文件 :126,130：用于 valid_until 筛选。
- backend/app/core/database.py:44-53、core/database_routing.py:93-104：未保证所有 PG 会话强制 UTC。

条件与后果：PG 会话 TimeZone 非 UTC 时，naive 时间可能按本地时区解释，导致过期判断偏移。session_browser.py:124-125 的固定 offset 日期聚合同样需要显式时区语义。

建议：绑定 aware UTC 时间；日期分组显式先转换目标时区。测试至少涵盖 UTC、Asia/Shanghai 及夏令时边界；会话强制 UTC 可作为补充，不能替代正确类型语义。

验证级别：静态审查，非 UTC PG 行为尚需集成测试验证。

### 7. P2：迁移预检不能证明完整约束语义保留（自定义/漂移 schema 条件）

证据：backend/app/services/database_migration.py:85-95,299-302,537-550。

原因：索引比较未涵盖 collation；CHECK 只比较数量；目标按 ORM metadata 重建。

条件与后果：源库有与既有 unique 同列的 COLLATE NOCASE 唯一索引，或 CHECK 改了表达式但数量相同，可能通过预检，而目标约束语义不同。默认纯 metadata 部署不一定触发。

建议：完整归一化比较 CHECK、unique/index expression、collation、partial predicate 等；无法映射的对象必须明确阻止迁移，不得静默放宽。

验证级别：静态预检/建库链核实。

## 已有的迁移安全能力

- database_migration.py:398-439：SQLite 备份、恢复副本与哈希验证。
- 同文件 :611-628,669-743：baseline/增量内容验证，重放与 checkpoint 同事务。
- 同文件 :874-929：最终屏障检查 schema/捕获结构/未跟踪写入；PG marker→SQLite seal→manifest 顺序。
- database_routing.py:189-273：封存后阻止静默退回 SQLite。
- site_restore_postgresql.py:45-80,184-225 与 postgresql_backup.py:223-245：PG 恢复 journal、数据库事务与恢复 marker。

这些能力是积极证据，但不等于完整证明所有业务操作/历史升级/异常组合均正确。

## CI 与测试缺口

1. .github/workflows/ci.yml:181-194 仅安装 test+voice，未配置真实 PG 测试端点/迁移依赖。
2. tests/services/test_primary_database_migration.py:147-157 和 test_postgresql_site_backup.py:23-29 在无端点时 skip。因此 CI 绿灯不代表 PG 已测试；这是验收门禁缺口，不是现行代码必然损坏数据的证据。
3. full_catalog 测试 :318-328 用当前 metadata.create_all 建源库，不能覆盖旧 PG ALTER；:337-346,385-411 的业务插入/断言并非所有 mapper 的完整 CRUD。
4. core/database.py:2095-2114 的 SQLite shape guard 主要检查列名存在，不涵盖完整类型、nullable/default、PK/FK、unique/index；PG 启动也缺少等价完整 guard。
5. core/migrations/__init__.py:25-28,521-529 的 checksum 仅 revision+description，不覆盖实现。修改既有 apply 函数可能不触发 checksum mismatch；应禁止改写已发布 migration，或对不可变 SQL/迁移产物做稳定内容校验。

## 如何让每次数据库改动同步维护

### 原则：一个业务事实源，不维护两套平行业务代码

- 共享 SQLAlchemy models、repositories、事务边界和类型/时间规范。
- 只把全文检索、upsert、锁和确有必要的 DDL 差异收敛到带接口契约的 dialect adapter。
- Schema 迁移使用单一版本链，每个 revision 中显式处理两种 dialect；可渐进引入 Alembic，不必立即重写整个迁移系统。
- ORM 抽象和 Alembic 都不能自动证明语义一致，契约测试是必需条件。

### 必须进入 CI 的四层验证

1. 离线静态契约：显式加载全部模型；两种 dialect 编译所有表/索引；revision 唯一性；检查未保护的方言 SQL；校验模型注册清单。注意编译检查不能代替 PG 真实执行。
2. 双库 schema/历史升级契约：当前建库与 n-1/已支持旧版本→最新分别在两库执行；保留 sentinel 数据；再次启动验证幂等；反射比较类型、nullable/default、PK/FK、unique/index；有意差异显式 allowlist。
3. 共用业务契约：同一测试在专用 SQLite/PG engine/session fixtures 执行；覆盖 CRUD、JSON、布尔、时间/微秒、大整数、NULL、FK/unique、rollback/savepoint、幂等写入、搜索召回、租户隔离与并发。全局 conftest 强制 SQLite，不应直接用全局 URL 替换来绕过安全门。
4. 迁移故障契约：持续写入期间迁移；级联删除/大事务/重启重放；重复恢复；每个切换边界故障注入；校验行数与规范化逐行内容、约束、文件关联、密钥解密、登录和业务烟测。

### PG 门禁必须 fail-closed

- 新增 required 的 database-contract CI job，安装 database-migration extra。
- 保留 tests/docker_test_safety.py 的容器所有权、独立网络/卷、专用数据库校验；不要为了普通 Actions PG service 绕过安全门。
- 测试专属 marker/strict 开关：严格 CI 中缺 URL、缺驱动、无法启动 PG 或目标测试被 skip 均失败；检查 executed>0 且预期 PG 用例没有 skip。日常离线开发可保留显式 skip。
- 在分支保护中将该 job 设为 required；不能仅依赖本地 pre-commit，后者可被跳过。
- 不只用路径过滤运行：公共 helper、模型导入和依赖升级也可能影响 DB。至少 PR required gate 与发布前都执行双库测试；长时故障/规模测试可定时跑。

可复用现有隔离入口：updater/tests/postgresql_roundtrip.py:42-58 和 updater/tests/docker_isolation.py:466-496。必须新建专用隔离目录与测试栈，不能复用生产容器/卷。

## 上线迁移放行条件

1. 修复上述 P1，补 P2 的边界回归与决定明确支持的语义差异。
2. 用真实数据副本进行隔离演练，验证完整 schema、逐行内容、文件及加密配置；本审查未读取真实数据，不能判断其现存质量。
3. 在持续写入、进程中断、重启及恢复场景中完成 PG 真实集成测试，保存结果与恢复证据。
4. 备份数据库、受管文件与必要密钥/配置，并证明可恢复，而非仅证明备份文件存在。
5. 正式切换后将 PG 作为唯一写入事实源，保留源 SQLite 为封存取证/迁移前快照；若需回退，必须设计反向迁移/增量对账，不能直接修改路由指向旧 SQLite。

本次只交付审查与建议，未实施业务修复、CI 改造或数据库切换。
