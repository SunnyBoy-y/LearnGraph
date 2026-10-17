# 图谱学习节点升级体系

状态：产品与工程设计稿；当前已增加后台管线、正式学习页和测评记录。实际实现与验证边界见《node-learning-implementation.md》。随附早期交互原型仍使用内置示例。
日期：2026-09-17。依据：当前工作区代码与本次需求，未联网。

## 1. 产品目标与核心决策

把图谱中的一个节点升级为一个可持续学习的单元：节点身份、版本化教材、交互活动、任务点、评测与学习证据共同组成它。点击节点打开真正的学习页面；聊天作为局部答疑入口，携带节点、章节、选中文本与当前任务上下文。

核心闭环：进入节点 → 阅读或操作 → 完成任务点 → 接受验证 → 获得节点成就 → 返回图谱继续下一步。

必须区分四个概念：

- **内容已准备**：有可用的已发布学习包；不代表用户学过。
- **任务已完成**：用户阅读、作答或操作满足任务规则；不等于掌握。
- **关卡已通过**：一场符合要求的评测通过；发放装饰与成绩凭证。
- **掌握度**：已有 Evidence / Mastery / LearningNodeState 系统综合证据形成的判断；不能直接从阅读进度或金边推断。

## 2. 当前实现与接入点

- `frontend/src/features/graph/graph-pages.tsx`：GraphNodeInspector 的主要学习入口调用 studyFromNode；升级为“进入教材 / 开始实验 / 开始测评”，旁边保留“向导师提问”。
- `frontend/src/components/graph/node-presentation.ts`：practice 与 assessment 目前共同显示为“实验 / 测验”；应拆开展示、图标、主按钮和通关样式。
- `frontend/src/features/graph/graph-plan-model.ts`：学习计划已有 review / practice / assessment → Practice Session 路由，并非所有入口都只有聊天。新功能应统一入口解析，避免图谱、计划、右栏各走一套规则。
- `backend/app/domain/models.py`：GraphNode 已有 teaching_strategy；Exercise、PracticeSession、AnswerRecord、Evidence 与 DurableJob 已存在。
- `backend/app/services/durable_queue.py`：可复用数据库持久队列、去重键与租约。其 enqueue 当前内部 commit，不能直接假设它与图谱发布共享原子事务；需要事务性 outbox 或新增可延迟提交的 enqueue 接口。
- `backend/app/api/routers/graphs.py`：已有节点问题历史接口，可辅助回填历史交互；不能只靠 mastery_stars 判断是否问过。
- `backend/app/api/routers/learning_state.py`：已有学习证据与状态投影入口。评测结果应接入这条证据链，避免新建平行掌握度。

## 3. 节点类型：语义与呈现分离

保留后端已有 root / concept / practice / assessment，增加内容包的 experience_kind，不依靠节点名称猜测展示类型。

- **root / 课程入口**：章节目录、路径总览、阶段目标、构建进度；不自动生成一份与全课程重复的长教材。
- **concept / 知识**：教材正文、示意图、例题、反例、即时检验。允许嵌入少量交互。
- **practice / 实验或训练**：先展示任务场景，进入带状态的操作界面；正文用于操作解释、复盘。experience_kind 可为 simulation、sequence、code_lab、case_decision 等。
- **assessment / 测评关卡**：进入考试说明与试卷；组卷与评分规则独立于教材。可评估单节点或该路径的一组明确节点。

知识节点可以有一个短“出口测”；正式 assessment 节点可以是路径中间关卡或章节终点。前者验证当前概念，后者验证组合应用。页面不强迫每个节点都有考试、小游戏或长文。

## 4. 学习页信息架构

桌面：左侧章节目录（约 200px），中间正文（阅读行长约 60–75 个汉字以内，优先舒适排版），右侧可收起的任务/答疑区。移动端：单列正文，目录抽屉，底部“继续 / 提交”操作，不常驻三栏。

页头：返回图谱、面包屑、节点类型、标题、预计学习时长、版本与来源状态、继续位置。

知识页默认结构：

1. **本节能做什么**：2–4 个可观察学习目标，不写空泛“了解”。
2. **先看一个问题**：实际情境或先验小题，建立目标。
3. **关键模型**：简短段落 + 结构图 / 操作图 / 坐标图，明确单位、图例、替代文本。
4. **分步讲解**：一个块只讲一个主张；术语、定义、易错点、注意事项分别标注。
5. **例题与反例**：先允许自主判断，再逐步揭示推导。
6. **动手验证**：交互卡、变量试验或短练习。
7. **带走什么**：核心总结、可选自检、下一节点。

素材按用途生成：解释结构用 SVG/图表，解释操作用步骤示意，只有外观辨识确有必要才用图片。每张图需要 caption、alt、source_ref / generated 标记；素材生成失败时使用经过验证的文字步骤或示意，不显示失效图片。来源不足要标明“模型生成，待核验”，不可伪造引用。

用户点击术语或“为什么”可以打开局部导师，不自动向会话发送整页文字。答疑记录回链当前 block_id，返回时保留章节、输入、实验状态。

## 5. 自动预构建：开关与边界

图谱工具栏提供“自动准备新节点学习页”开关，默认关闭以免隐含模型开销；开启前在同一设置区展示构建模式与预算，不增加逐节点弹窗。

推荐模式 **沿路径准备**：目标候选范围是开启后新增、尚无有效交互的节点；优先准备路径接下来 3 个符合条件的节点。可选 **全部新节点** 与 **手动构建**。设置支持工作区默认 + 图谱覆盖，优先级明确。

每次开启产生不可变 policy_epoch / enabled_at。默认仅接收开启之后、通过正式图谱发布事件新增的节点。关闭停止创建自动任务，并使未完成自动任务在下一个安全检查点取消；已完成内容保留。再次开启生成新 epoch，不补扫关闭期间节点。批量补建只能由用户显式启动，并且仍排除问过的节点。

### 5.1 “新节点”和“已问过”的精确定义

默认严格规则：

```
graph.status == published
AND node.node_type != root
AND node.created_epoch >= active_policy_epoch_boundary
AND first_engaged_at IS NULL
AND legacy_history_checked == true
AND no compatible published package
AND no equivalent active build job
```

实际实现使用发布事务写入的单调 event sequence / epoch 标记，不单靠毫秒时间比较。

first_engaged_at 在以下任一成功持久化事件出现时写入，且永不因删除会话、清空记录而清除：

- 用户已发送与节点明确关联的有效问题，包括原有“学习此节点”自动发送的用户消息。
- 用户主动开始该节点学习活动、实验或考试。
- 用户已有该节点答案、有效学习证据或历史学习行为。

只看图谱、选中卡片、查看简介不算“已问过”；GET 读取接口不产生写操作。进入正式学习体验由显式 start 请求记录。

联合学习请求对明确选中的全部节点写交互标记；会话仅挂着某张图谱，不把图谱全部节点当作已问过。模糊文本提及不能由模型猜测后批量修改资格。

### 5.2 历史数据

上线默认只处理未来新增节点。对可显式补建的旧节点，后台先回填交互标记：优先使用节点问题记录、消息结构化 node_ids、练习/答案/证据；无法确认时记 unknown 并排除自动任务。回填维护幂等游标，不改写聊天正文，不以 localStorage 或“零星级”代替数据库记录。

节点删除重建、导入和合并需处理 lineage：重命名不算新节点；保留来源身份的复制/合并继承排除事实，不能靠复制洗成新节点。不同图谱下真正不同的学习范围可有不同内容包，但来源指纹复用不共享用户成绩。

### 5.3 用户先开始，后台后完成

入队、worker 领取、发布内容三个阶段都重新检查资格。所有交互开始与发布都竞争同一节点 eligibility_version：

- 先发布成功：包保持可用，随后用户正常学习。
- 用户先开始：first_engaged_at 与 eligibility_version 原子更新；旧 worker 不得发布，任务记 skipped_engaged。中间产物可短期保留供排错，但不绑定为当前内容。
- 当前没有内容时，提供“现场构建并进入学习 / 向导师提问”。现场构建是明确的 on_demand 模式，允许已开始节点使用；它不属于自动预构建，也不能覆盖当前学习版本。

领取租约只保护任务，不足以保护业务资格；发布事务需同时验证有效 lease_token、policy_epoch、eligibility_version、node_content_fingerprint 与用户/工作区权限。

## 6. 构建流水线与任务点

推荐流水线：资格检查 → 教学目标/大纲 → 内容块 → 图片/交互规格 → 题目与评分规格 → 校验 → 原子发布。

每阶段存 checkpoint 与产物 hash，worker 重启可以从成功阶段继续。模型生成一套受控结构化内容包，由注册组件渲染；默认不生成任意 React/JS 再直接塞进主应用执行。

构建状态：queued、planning、writing、assembling、validating、ready、failed、cancelled、skipped。前端显示真实阶段、可恢复错误与重试入口，不拿计时器伪装真实百分比。若并行子任务有已知总数，可显示“已完成 4/6 项”。

预构建的“任务点”是学习内容的一部分，不直接变成日历里的学习计划：

- read_checkpoint：读完一节并主动确认；仅记录阅读。
- formative_question：即时检验，允许解释和提示。
- simulation_goal：完成可验证的状态目标。
- submission：代码、文件、步骤记录或短答提交。
- checkpoint_exam：通过指定蓝图的考试。

每项带 stable_id、目标关联、必要/选做、依赖、完成谓词、预计时长、证据类型。进度存到学习者记录，不能写回共享教材。跨版本保留稳定任务 ID；变更任务另开新版本并展示进度迁移差异。

质量发布门：schema 完整、任务依赖无环、学习目标有覆盖、资源可渲染、来源可追踪、图有说明、题目分值和量表完整、答案不进入公开 payload、交互没有死路。高风险或不确定内容进入 needs_review，不假装“校验通过”就等于事实正确。

资源控制建议：每工作区初始并发 1、路径前瞻 3、有限自动重试（例如最多 3 次）、按日模型费用/令牌预算、单节点体积和时限。数值为初始产品配置，不是现有系统性能承诺。对模型不可用/预算耗尽不反复重试，显示可行动原因。

## 7. 实验：从文本题到有状态的场景

采用“可复用交互引擎 + 领域规格”。通用能力包括顺序编排、变量调节、分支决策、拖放分类（提供按钮替代）、代码运行、流程模拟与证据复盘。

一个实验描述：初始状态 → 可选动作 → 前置条件 → 状态转移 → 可观察反馈 → 成功/失败条件 → 学习目标 → 复盘。

### 7.1 示例：辣椒炒肉小剧场

此例用于产品机制说明，不把模拟分数当真实烹饪资质。

- 场景：灶台、锅、待处理材料、火力和计时控制。
- 状态：准备完成、锅热状态、当前火力、锅内材料、时间、步骤顺序。
- 训练：分批处理肉和辣椒 → 调整火力 → 回锅合炒 → 判断何时出锅；操作会改变画面和步骤轨迹。
- 反馈：用户把步骤做错，画面和文字解释其后果，允许回滚或重新试；用“为什么”连接教材中的原理。
- 考核：同一实验引擎关闭即时答案提示，记录动作序列和关键条件，再按版本化量表评分。
- 核心不是点满按钮；只有产生正确状态转移、达到目标才完成。计时、火力、顺序应影响结果，不靠硬编码“下一步”模拟交互。

其他领域可映射为：电路连线与观测、算法执行与复杂度实验、英语情境对话、统计抽样、历史决策推演。超出组件能力的复杂小游戏进入隔离子应用，采用已有 sandbox 基础设施；只接受明确事件契约和服务端可验证结果。

## 8. 测评体系

### 8.1 试卷体验

进入先看到：覆盖节点、学习目标、题型、总分、建议时长、通过线、关键项、是否限时、提示规则、重考规则。开始后锁定不可变试卷版本。

呈现仿真实试卷：试卷头、题型分区、题号、分值、草稿区/大题答题区、答题卡导航、未答标记、自动保存状态、交卷检查。小屏答题卡收进抽屉。

题型支持单选、多选、判断、填空、简答/论述、计算步骤、排序匹配，以及嵌入实践卡。各题有独立评分 rubric 与 objective_ids。题型是组合能力，不强迫每份试卷包含所有题型。

自动保存使用带 revision 的 patch；断线时保留本地未同步草稿并显示“未同步”，恢复后解决冲突。交卷有幂等键，页面刷新不创建新考试。正式成绩以服务端时间、答案快照和评分结果为准。

### 8.2 评分与防答案泄露

- 客观题在服务端按明确规则判分；多选是否部分分、填空规范化规则需在蓝图固定，不在答完后临时改变。
- 简答/大题按分项量表给出覆盖点、缺失点与引用答案片段；模型判分低置信度进入待复核，不提前发放通关金边。
- 实践题上传动作事件，服务端重放状态机或执行验证器，不能相信前端上报 score=100 或 completed=true。
- 训练即时反馈；考试在交卷/评分完成之后才展示解析。公开内容接口不返回 answer_key、隐藏测试或私有 rubric 细节。
- 当前学习 API 中有训练式逐题反馈；正式考试必须独立 assessment_mode 和反馈策略，不能直接沿用每题提交即透题的路径。

推荐默认通过条件：总分 >= 80/100，且所有标记为 must_pass 的关键任务通过。优秀可设 >= 90；这些值可由学科蓝图覆盖。实践关键失败不能靠选择题总分抵消。读完教材、使用提示、自动构建成功都不会自动授予成绩。

### 8.3 重试与辅导

失败不清除已完成阅读和已有荣誉。结果页定位“哪个目标未通过 → 回到哪一节 → 推荐哪个训练”。练习模式可以查看提示；正式认证尝试记录 assistance_level。看过答案后用等价新题重考，区分首次分数、最近分数、最好分数。

## 9. 图谱成就与路径关卡

节点底部显示阅读/实验任务进度；右上角显示学习类型。内容准备状态用小图标或文字，不抢占成就位置。

- 已学习：低强调勾选与任务完成数。
- 已验证：金色细边 + 勋章图形 + “已通过”，三种信号共同表达。
- 优秀：保留金边，增加一个简洁桂冠/等级标识，不做持续闪烁。
- 待复习：保留历史通关勋章，另加“建议复习”标签；不会因遗忘惩罚性地剥夺历史成绩。

装饰由服务端 achievement 投影驱动；用户手动“标记掌握”不发评测勋章。一场考试只能按其 coverage/objective 规则授予明确节点成就，综合考试不能不加区分给整个图谱镀金。

路径保持知识关系与行动路径区分：图谱 prerequisite 是关系事实，plan 顺序是计划。新增 checkpoint_rules 定义关卡覆盖、必要通过项、soft/hard gate。默认允许先探索后续教材，只对后续正式挑战启用必要门槛；用户能看到锁定原因和去前置节点的按钮。

完成反馈：短暂描边与一次性勋章揭示，支持减少动画；说明分数、通过的目标、下一节点。不要弹出多个庆祝窗口挡住继续学习。

## 10. 存储设计（逻辑结构，待迁移实现）

所有记录包含适用的 tenant/workspace 外键、创建时间和权限范围；学习者数据另外包含 subject_user_id。教材可共享于工作区，私人补充资料和个性化内容必须限制 audience_scope，不能因缓存复用泄露给其他成员。

**graph_learning_policies**：graph_id、enabled、mode、policy_epoch、effective_from_event_seq、lookahead_count、budget、revision。图谱级开关只由有权限的用户修改。

**node_learning_eligibility**：node_id、first_engaged_at、first_engagement_ref、eligibility_version、legacy_history_checked、lineage_ref。自动准备是节点级：任意有权限学习者已与该节点发生有效交互，就不再自动生成/替换共享包；用户个人进度独立保存。

**node_learning_packages**：node_id、package_version、schema_version、experience_kind、node_fingerprint、source_revision、audience_scope、status、manifest_json、quality_report、published_at。发布后不可原地改写，节点记录 current_package_id 指向当前版本。

**learning_blocks / assets**：package_id、block_id、order、kind、payload、asset_ref、caption、alt、source_refs、content_hash。图片等二进制进文件/对象存储，数据库存引用与校验值；教材结构与任务数据进入数据库。

**learning_tasks**：package_id、task_key、objective_ids、type、required、depends_on、completion_spec。唯一键 (package_id, task_key)。

**learning_enrollments / task_progress**：user_id、node_id、package_id、task_id、status、checkpoint_json、revision、completed_at。唯一键 (user_id, package_id, task_id)，防重复完成。

**learning_build_runs**：node_id、package_id、durable_job_id、policy_epoch、trigger(auto/manual/on_demand)、input_fingerprint、eligibility_version、stage、checkpoints、usage、error_code。持久任务复用 DurableJob，不再实现一套租约机制。

**assessment_blueprints / paper_versions**：范围、目标、题目引用/版本、分值、通过规则、提示规则、时限、私有答案、量表/验证器版本。公开和私有序列化分离。

**assessment_attempts**：user_id、paper_version_id、practice_session_id、status(in_progress/submitted/grading/needs_review/passed/failed)、answers_revision、started_at、deadline_at、submitted_at、total_score、critical_checks、assistance_level。复用 Exercise / AnswerRecord 作为可兼容题目和答案事实；补足题目快照与考试约束。

**node_achievements**：user_id、node_id、achievement_type、assessment_attempt_id、package_version、rubric_version、awarded_at、revoked_reason。唯一约束 (user_id,node_id,achievement_type,criteria_version)；撤销仅用于错判、舞弊或失效，不因复习到期自动撤销。

**domain_outbox**：发布节点、用户开始、评分完成等事件；与源业务同事务写入，消费者幂等处理。用于消除“数据库提交成功、任务没进队”的窗口。

## 11. 接口与事件契约（建议）

统一沿用现有工作区鉴权机制，下面路径是待新增设计，不宣称已经存在。

- GET/PATCH `/graphs/{id}/learning-policy`：读取/设置开关；PATCH 带 expected_revision。
- GET `/graphs/{id}/learning-builds`：分页返回真实阶段、排除原因、用量及可重试状态。
- POST `/nodes/{id}/learning-builds`：manual 或 on_demand + idempotency_key；返回 202 与 run_id。已问过节点的 manual_prebuild 返回明确 409；on_demand 按用户主动行为处理。
- POST `/learning-builds/{id}/cancel`、`/retry`：有所有权检查；重试仅在资格与版本仍匹配时成立。
- GET `/nodes/{id}/learning-page`：ready 包与学习者投影；无内容返回明确 availability，不能伪造已准备。
- POST `/nodes/{id}/learning-start`：显式登记交互并锁定 enrollment.package_version；与后台发布按 eligibility_version 排序。
- PATCH `/learning-enrollments/{id}/tasks/{task_id}`：操作/草稿与 expected_revision；权威完成由验证器判断。
- POST `/nodes/{id}/assessment-attempts`：锁定试卷与规则，幂等开始。
- PATCH `/assessment-attempts/{id}/answers`：自动保存，expected_revision。
- POST `/assessment-attempts/{id}/submit`：原子冻结答案、排队判分，重复提交返回同一结果。
- GET `/assessment-attempts/{id}/result`：待评分/待复核/最终结果，只有最终结果可发奖。

事件：node.published → build.requested → package.published；learning.started → progress.recorded；assessment.submitted → assessment.graded → evidence.recorded → achievement.awarded。全部带 event_id 与 aggregate_version。

异步推送可复用已有 SSE 基础设施；断线后 GET 补齐，事件按 seq 去重。worker 重启、浏览器关闭与用户离线都不影响已入库任务继续执行。

## 12. 原子性、版本与幂等

- 内容输入指纹仅覆盖该节点的教学定义、必要前置快照、来源版本、语言/难度和渲染 schema；整张图谱其他节点改名不应触发无关重建。
- 自动任务去重：(workspace, node, input_fingerprint, policy_epoch, trigger)。终态重试不要通过更换随机键绕过唯一性；需要显式 attempt / retry 操作。
- 现有 worker 失去租约后即使模型返回也不能发布；业务事务再次检查 token。
- 任务发布以 current_package_id CAS 更新和 ready 包事务提交为准；资源全部验证成功前不可见半成品。
- 用户已进入包 V1，后台更新 V2 时继续用 V1 完成任务/考试；新包提示可切换，不能让章节和题目中途改变。
- 评分、证据与成就通过事务/outbox 幂等传播。唯一约束保证网络重试不会重复发星、重复勋章或重复计划完成。
- 图谱删除、权限变更、节点失效需使后台任务停止发布；孤儿素材延迟清理，保留考试与证据必要快照。

## 13. 实施顺序与验收

### A. 数据闭环与三类入口

交付：新开关、严格资格判断、DurableJob 构建任务、版本化 JSON 教材与任务入库、教材渲染、旧节点排除、节点/右栏/计划入口统一。模型不可用显示真实失败原因。先实现教材块、图示、短练习三种核心内容。

验收：开关前旧节点不排队；开启后新节点被排队；重复事件只有一个任务；聊天先开始时 worker 不能晚发布；关闭和重开不补扫；刷新/worker 崩溃后任务与进度恢复；候选图谱不触发正式内容构建。

### B. 正式关卡与金边

交付：混合试卷、草稿、交卷冻结、服务端评分、待复核、证据回写、金边成就、失败后的定向补练。先复用既有客观题/填空/简答，再扩展复杂大题。

验收：客户端看不到答案；重复交卷/判分不重复发奖；关键项失败总分够也不通过；手动标记掌握不出金边；待复核不提前发奖；成绩保留版本；错误反馈可以定位教材。

### C. 实验组件与路径挑战

交付：顺序/变量/分支三个通用组件、实践动作回放验证、实践题嵌入试卷、综合关卡与明确覆盖关系。再按需要接隔离代码实验和复杂小游戏。

验收：不同操作真实影响实验状态；重放事件得到相同结果；非法转移被拒绝；只点击按钮但没满足规则不能完成；键盘和移动端可操作；考试模式不泄露训练提示。

### D. 质量与成本运营

观测：可用包覆盖率、首次进入等待时长、构建失败/跳过原因、无效预构建开销、内容纠错率、任务完成率、首次通过率、重考后迁移表现。分母和事件口径必须明确，不用“生成字数”冒充教学效果。

首版成功标准：学习者能从真实新节点获得可恢复的学习页，完成一个可验证活动，通过一次正式评测，并在图谱上获得由真实证据支撑的通关标记。

## 14. 本次设计交付与验证范围

交互原型包含：教材与任务勾选、局部答疑示例、带动作前置条件的烹饪实验、混合试卷、评分与金边状态、自动准备开关、新增节点、逐阶段构建、构建中提问导致发布被拦截。

原型没有调用模型、没有真实服务器评分、没有写数据库，也没有替换现有业务入口。实践试题为缩短演示直接引用训练完成状态；正式实现需遵循第 8 节独立尝试与服务端验证规则。

本地浏览器已验证阅读任务计数、实验前置条件与完整状态流、交卷判分、金边装饰、已问过节点排除、关闭期间新节点不补扫，以及 1024/736/375/320px 下四个页面的水平布局。验证对象仅为原型，不代表生产后端通过了对应验收。

## 15. 自动生产管线补充

详见 [图谱学习内容自动生产管线](graph-learning-production-pipeline.md)。该方案把第 6 节细化为可实施的后台编排：教学蓝图 → 生产任务依赖图 → SVG/图表/生图/HTML/子应用/试卷适配器 → 静态与行为校验 → 有限局部修复 → 再次检查节点资格 → 原子发布。

补充明确了现有聊天绑定的解耦、官方子应用 SDK、内容版本引用、私有评分隔离、素材冻结、预算与缓存、可选素材降级、必需实验失败阻断，以及构建任务与学习任务的区别。现有原型仍只模拟这些行为，不代表生产管线已经实现。
