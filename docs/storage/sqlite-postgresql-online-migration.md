# 主业务库在线迁移：SQLite → PostgreSQL

本功能仅迁移主业务库，默认安装仍使用 SQLite。只有系统管理员显式发起任务，才会进行备份、拉取镜像和迁移。成功激活 PostgreSQL 后不支持迁回 SQLite。sandboxd 自己的数据库、对象文件和沙箱运行目录不迁移。

## 管理员操作

在「设置 → 存储与数据迁移」中使用「主业务数据库」面板。

1. 选择 Docker 自动部署，或配置一个专用的 PostgreSQL 空库。
2. 执行预检。完整性、WAL、表/字段/索引映射、未知触发器/视图/约束、维护冲突、密钥和旧更新器互锁必须全部通过。
3. 确认单向迁移并输入当前管理员密码。未修改初始密码的账户不能执行此操作。
4. 点击「强制备份并开始迁移」。可选择全部检查通过后自动切换，也可等待人工输入密码确认切换。
5. 页面显示真实备份页数、复制与校验表数、行数、增量事务、Docker 拉取分层进度及执行记录。刷新页面不影响后台任务。

普通用户和工作区管理员没有迁移或数据库连接配置权限；后端使用系统管理员依赖进行校验，并禁用旧数据库迁移/回滚入口。对象存储继续使用原有入口。

## 一致性与切换

- 先通过 SQLite Online Backup API 产生一致性备份，再检查完整性与外键，独立恢复备份并比对文件哈希。部署密钥和恢复材料一并保存在私有目录。完成之前不安装捕获触发器，也不拉取 PostgreSQL 镜像。
- 在 SQLite 同一业务事务内捕获 INSERT/UPDATE/DELETE 及提交序号。回滚不产生已提交增量，二进制按无损格式传输，NULL、JSON 原文及重复归档行得到保留。
- 使用当前 ORM 创建 PostgreSQL 表、约束和索引，覆盖全部主业务表及两个已知归档表。未知结构必须先明确映射，不能静默丢弃。
- 全量复制以外部排序的规范化行哈希逐表核对，包含重复行的数量。随后按源事务顺序重放，检查受影响主键，再在新的固定快照上进行第二轮完整核对。
- 验证目标持久化配置、外键与索引、实际读写以及会话/文档/记忆检索。迁移重放事务允许暂时延迟外键检查；常规业务事务仍即时检查外键。
- 使用稳定的 SQLAlchemy Engine 门面覆盖原先捕获的 Session 工厂、执行选项绑定及后台写入。Linux Docker 的共享文件锁覆盖其他应用进程。切换时等待已有数据库事务完成，新数据库连接短暂排队；长事务继续运行，切换重试，不强杀连接。
- 在最终屏障内确认源结构与捕获机制未变、没有脱离应用提交边界的写入，重放至源与目标提交序号完全相同，重置序列，再持久化激活意图与目标身份标记。
- SQLite 封存提交是单向切换的不可逆边界。之后全部主业务读写走 PostgreSQL。SQLite 触发器阻止后续事实数据写入；PostgreSQL 不可用时禁止回退 SQLite。

API、SSE、语音和任务进程不因迁移而重启。请求可能在最终协调阶段等待；这不等同于零延迟，也不能对主机、磁盘、网络或数据库故障承诺绝对 100% 可用。持续过载或永不结束的事务可能使切换持续等待。

## Docker 与持久状态

镜像安装 `database-migration` 可选依赖（psycopg 和 Docker SDK）。Compose 启动独立 `database-migration-agent`，只有该代理和原有 sandboxd 挂载 Docker socket，Web 应用不持有 socket。代理不暴露宿主端口，只接受私有令牌认证及固定格式任务 ID，不接受任意镜像、命令或挂载参数。

管理员发起且备份验证通过后，代理才拉取 `postgres:18-alpine`，解析为镜像 ID 创建容器。PostgreSQL 使用独立命名卷、私有网络、健康检查、重启策略和专用非超级用户；没有发布宿主 PostgreSQL 端口。镜像/卷的所有权按迁移任务标签校验。

默认持久目录：

- `/data/database-migration/job.json`：任务进度与加密连接信息。
- `/data/database-migration/active.json`：权威激活指向；数据库 URL 加密保存。
- `/data/database-migration/backups/<job-id>/`：迁移前备份、哈希及恢复密钥材料，目录私有。恢复材料包含敏感密钥，必须和备份一起受到访问控制。
- `/data/database-migration-agent/`：代理令牌和加密部署状态。
- `learngraph-main-postgres-<job-id>-data`：自动部署的 PostgreSQL 数据卷；此卷由代理创建，不属于 Compose 的静态 volumes 清单。迁移成功后不能清理。

这些目录、主部署密钥及 PostgreSQL 卷必须一起保留。`LEARNGRAPH_DATABASE_MIGRATION_STATE_DIR` 可指定共享持久目录，所有主业务进程必须使用相同配置。首次部署本功能需要正常更新应用版本；迁移本身不要求重启。

## 失败与恢复

封存前失败保持 SQLite 权威，允许管理员取消或继续；不删除原库或已验证备份。恢复执行校验目标任务所有权、备份与已完成检查，并从持久检查点继续。应用启动时恢复已授权的未完成任务。

封存后异常只能恢复 PostgreSQL 指向。启动时依据源封存标记、目标激活身份、最终提交检查点和备份哈希恢复；证据不完整时停止激活，不能新建空库或偷偷选择 SQLite。已封存源库即使丢失激活文件也不能作为正常 SQLite 服务启动。恢复事故应保留所有卷与状态文件。

此实现的跨进程在线协调面向 Linux Docker；Windows 本机开发使用同进程协调，不作为多进程在线迁移部署方式。

## 其他入口与兼容边界

- `core/database.py` 与 `database_routing.py`：主业务连接、启动路由及已有绑定的统一切换。
- `sqlite_capture.py`：同事务增量日志、回滚及原库封存。
- `database_migration.py`：预检、备份恢复、全量/增量验证、激活及恢复状态机。
- 现有 chat、billing、memory、队列、语音、调度、认证 `last_seen`、请求收尾写入通过上述共享连接路径进入同一主库；不仅拦截 HTTP 写方法。
- `scheduler.py` 的归档删除、`subapp_analyze.py` 的时间聚合、`process_lock.py` 的租约竞争已适配 PostgreSQL。
- 会话、文档和记忆原生搜索使用 PostgreSQL `pg_trgm` 与授权范围过滤。SQLite FTS 是派生投影，不作为事实数据迁移；加密事件事实仍完整迁移。匹配与过滤已验证，排序算法与 SQLite BM25 不完全相同。
- `api/routers/migrations.py`、`services/migrations.py`、`services/management.py` 中重复的旧数据库迁移入口均被阻止；旧回滚入口不能改变主库指向。
- 迁移期间禁止与对象迁移维护、恢复、服务更新并发。旧更新器的自动安装必须关闭且无进行中更新任务，否则预检不通过。
- **原有全站备份、恢复和自动更新入口继续支持 PostgreSQL。** 全站包使用原生一致性快照并整体加密；恢复仍先暂存、再停服应用，在事务内校验后提交，保持当前 PostgreSQL 目标和单向激活身份。SQLite 包不能覆盖已激活的 PostgreSQL。
- **自动更新和应用版本回退保留 PostgreSQL。** 更新前生成实际主库的原生备份并验证宿主副本；目标镜像必须声明 PostgreSQL 能力，更新与失败回滚均核对数据库身份。版本回退只切换镜像，保留升级后数据。具体命令及中断恢复见 `backend/SITE_BACKUP.md`。
- 自定义 PostgreSQL 目标必须专用且为空，并具备创建业务结构及安装 `pg_trgm` 的权限。已使用的库、结构不兼容、无效数据或验证失败都不能切换。
- sandboxd 独立 SQLite、导入配置用只读 SQLite 和工具自有数据库继续原行为；主业务库中的 sandbox 元数据随主库迁移。

## 验证记录（2026-10-18）

验证使用独立 Docker 网络和数据卷，没有重建或迁移现有正式应用容器。

- 真实初始化库：188 个 ORM 主业务表及 2 个既有归档表，11 项预检通过；识别并保留旧增量初始化脚本中的等价唯一索引语义。
- Docker 自动拉取/部署到完成：162 次 API 写入、162 次读取均成功；另一个 Linux 进程在持有迁移前绑定的情况下完成 1,163 次写入，零错误。
- 逐条核对测试 ID/名称：目标 162 个会话、1,163 条锁记录全部存在。封存 SQLite 只有 1,075 条锁记录，之后 88 条只进入 PostgreSQL。目标无无效索引、无默认延迟约束，激活身份匹配。
- 探针最大一次组合操作耗时 1,906ms。该指标包含一次写入、读取及当次相关控制请求，不是单个请求延迟，也不是生产负载 SLA。
- 后端迁移、代理、管理员 HTTP、SQLite 写门专项 23 项通过，另补充 2 项最终屏障故障保护测试通过；最终激活路径追加复测通过。记忆、SQLite 锁、语音回收、站点备份和更新器相关回归 116 项通过。
- 隔离应用迁移后重启，仍指向 PostgreSQL，全部测试记录数量一致。PostgreSQL 18.6 原生自定义格式备份生成成功，`pg_restore --list` 读取 1,771 项目录；此检查不等同于完整灾备演练。
- 前端管理员可见性、确认/密码、状态及原有设置相关测试通过，并提供同视口实际渲染的修改前、迁移中、完成后截图。

源代码中的 PostgreSQL 集成测试必须在连接前核验显式测试项目、所有权令牌、目标容器及其全部卷和网络，库名或 localhost 均不能作为隔离证明。只允许独立 `migration_test` / `maintenance_test` 库，每个测试使用随机 schema；可通过 `IsolatedAgent.run_pytest` 在已标记的隔离项目内启动固定测试工作容器，不接受正式端点或未经核验的连接地址。

机制参考：[SQLite Online Backup API](https://www.sqlite.org/backup.html)、[PostgreSQL 事务约束控制](https://www.postgresql.org/docs/18/sql-set-constraints.html)、[Docker Engine API](https://docs.docker.com/reference/api/engine/)。

## PostgreSQL 全站维护补充验收

原全站加密备份/恢复和自动更新已接入 PostgreSQL，管理员入口继续可用。镜像包含 PostgreSQL 18 原生客户端，完整恢复仍遵循停服流程；应用更新沿用原有短暂重启流程。

使用独立 Compose 项目、独立数据卷及本地版本镜像验证了以下链路，没有发布测试版本：

- 新安装为 SQLite；管理员迁移后，旧 SQLite 加密包被拒绝恢复。
- PostgreSQL 实际全站备份、上传校验暂存、停服覆盖恢复；上传文件、Memory 和数据库记录一致，历史登录会话撤销。
- 管理员启用自动策略，调度器实际执行原生备份及 app/preview/egress-proxy 三容器升级。
- 手动应用版本回退、坏镜像自动回滚、更新器重启恢复都保持 PostgreSQL 身份与升级后数据；不具备 PostgreSQL 能力的目标镜像在替换前被拒绝。
- `/data/restored.env` 配置跨容器重启生效；不同自动生成主密钥下的恢复在不挂载源码的镜像中通过，数据库指向重新加密后仍为 PostgreSQL。
- 普通用户的备份、恢复、更新与自动安装设置请求均返回 403。
- 主机升级脚本的 PostgreSQL 备份与卷备份通过；不匹配测试项目时在维护调用前拒绝。

可复用的隔离验收入口是 `updater.tests.postgresql_roundtrip`，必须使用全新 `--root` 目录，附加维护验证需在首次验收时使用 `--keep` 保留该栈。`postgresql_config_roundtrip` 与 `postgresql_key_roundtrip` 补充配置及密钥恢复验证；`shell_roundtrip` 在同一已通过验收的目录验证升级脚本拒绝错误项目、原生备份和宿主复制路径边界。

所有入口强制校验 `.docker-test-isolation.json`、实际根目录、随机项目名、资源所有权标签、全部挂载（包括只读卷）、独立网络及实际 HTTP 端口。缺少清单的旧测试栈不能接管，不能通过 Python 优化模式跳过校验。Compose 始终显式固定项目，清除继承的 Compose 文件、项目和 profile 环境变量；未知指令、额外网络、共享镜像标签、外部卷、挂载目标或镜像覆盖均拒绝。受管 PostgreSQL 还须匹配该夹具记录的迁移任务与独占卷。

宿主升级脚本验收统一通过 `shell_roundtrip --root` 调用，每次 Docker 调用均经过独立的 `shell_docker_gate`，产品脚本即使遗漏某处分支也不能读取正式卷或执行正式容器。该适配器只把测试发布拉取替换为本地镜像，并用经核验的镜像覆盖模拟产品 Compose 的 `LEARNGRAPH_IMAGE` 替换。覆盖原生备份、错误 SHA、复制边界、SQLite 能力拒绝、成功升级、坏镜像回滚及 Compose 替换失败回滚；成功和回滚都检查三个业务容器健康和 PostgreSQL 身份。失败清理保留隔离清单与日志；备份保留策略按完整文件名处理空格及换行。

测试控制路径不能经符号链接或目录联接跳出隔离目录。宿主脚本写操作必须显式设置 `LEARNGRAPH_EXPECTED_PROJECT`，且容器项目与工作目录匹配；测试目录中的清单禁止改选正式项目。sandboxd 实测不再在收集阶段探测默认服务或读取开发令牌，外部模型容器也必须核验归属。旧 OCI smoke 拒绝宿主/WSL 环境；前端 live 测试必须先提供 `LEARNGRAPH_TEST_FIXTURE_ROOT` 和与 Docker 实际绑定一致的 `CODE_TRAINER_LIVE_URL`，然后才允许浏览器登录或写入记录。

客户端使用与事务恢复机制参考 [pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html)、[pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html)。
