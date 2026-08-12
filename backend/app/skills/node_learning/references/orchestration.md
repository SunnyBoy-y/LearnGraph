# 节点学习编排手册（详细版）

配合 `node-learning` SKILL.md 使用。本文档在需要"更细的维度选型、场景模板、组件写法"时用 `lg_skill_read` 展开；SKILL.md 的速查表与成本护栏是硬性约束，本文档只做展开，不降低约束。

## 1. 维度阶梯详解

| 档位 | 实现方式 | 成本量级 | 典型耗时 | 适合 | 别用它做 |
|---|---|---|---|---|---|
| L0 文本 | 正文 + Markdown 表格 / 列表 / 代码块 / ASCII 图 | ≈0 | 即时 | 定义、列举、步骤、小结论 | 大段文字堆砌（应拆表或分块） |
| L1 自绘图形 | magic_card 内联 SVG / 静态 HTML | ≈0（仅 token） | 快 | 结构图、流程图、时间线、概念关系、对比布局 | 需要真实照片的场合 |
| L2 图表 | `create_chart`（pie/line/bar） | 低 | 快 | 数值对比、趋势、占比、分布 | 无数据的装饰性图形 |
| L3 组件自测 | channel A：single_choice / multiple_choice / fill_blank / short_answer_table / option_group | 低 | 快 | 检索练习、判分题、即时反馈 | 用 magic_card 写表单 |
| L4 双向交互 | magic_card + 按钮 / 输入 / 翻卡 / 步进 / 拖拽 / 滑块 / 模拟器 | 中（HTML token） | 中 | 过程推导、动手探索、分支场景 | 纯展示型内容（用 L1） |
| L5 动画教学 | magic_card + CSS transition/keyframes 或 SVG animate | 中 | 中 | 流程推进、机制演示、时序、算法步骤高亮 | 动画 ≠ 交互：不要为了动而动 |
| L6 图片 | 引用（image_frame / file_id）→ 搜图（search_images + download_external_image）→ 生图（generate_image） | 中-高 | 慢（生图尤甚） | 真实照片、实物场景、抽象概念配图 | 结构图/概念图（用 L1，更准更省） |
| L7 静态页 | 单文件 HTML → `sandbox_publish_file` | 中-高 | 中 | 用户要"一页纸 / 学习卡片 / 保存分享" | 每次都产页面（对话内讲清即可） |

**跨档组合建议**：
- 简单节点：L0 + L3（一题）——最常见，成本最低。
- 中等节点：L0 + L1（或 L2）+ L3。
- 复杂节点：L0 骨架 + L4/L5（主演示）+ L3（自测）+ 可选 L6 配图；L7 仅在用户要求时追加。

## 2. 图片决策矩阵

| 教学需求 | 首选 | 次选 | 避免 |
|---|---|---|---|
| 概念关系 / 结构 / 流程图 | L1 自绘 SVG | 生图示意 | 搜图（多半搜不到准的） |
| 真实照片（名胜、实物、人物、器械） | 搜图 `search_images` | 引用会话已有图 | 生图（假照片） |
| 抽象概念配图（隐喻、氛围） | 生图 `generate_image` | 搜图 | 无 |
| 复用资料/文档里的图 | 引用 file_id → `image_frame` | — | 重新生成 |
| 用户明确"画一张" | 生图 | — | 搜图（用户要"造"不是"找"） |

- 搜图结果只是候选：先看缩略图/来源判断是否可用，再 `download_external_image` 落盘；不可用就明说，不硬凑。
- 生图失败（provider 未启用等）如实告知，不假装成功；不重试超过 1 次。

## 3. 场景模板

### 3.1 概念 / 定义型节点
1. L0：一句话定义 + 关键词拆解（表格：术语 → 人话）。
2. L1：一张结构图（SVG：概念的组成/边界/相邻概念）。
3. L3：一道单选或填空，判分 + 解析。
4. 反例 1 个（概念最容易错的地方）。

### 3.2 机制 / 流程型节点
1. L0：步骤清单（编号）。
2. L4 或 L5：分步推进——按钮逐步高亮（步进器）或 CSS 动画演示流程；每步配一句"发生了什么"。
3. L3：一道"某一步错了会怎样"的选择题。
4. 常见坑：1～2 条。

### 3.3 对比 / 分类型节点
1. L0：对比表格（维度 × 对象）。
2. L1：并排对比图（SVG）或 L2 图表（有数值时）。
3. L3：一道"判断属于哪类"的多选题。
4. 记忆口诀（可选，1 句）。

### 3.4 数值 / 规律型节点
1. L0：规律表述 + 公式（代码块）。
2. L2：真实数据图表（必须 `sources`；无权威来源就不画数据图，改用定性说明）。
3. L3：一道计算/代入题（fill_blank 或 single_choice，带解析）。
4. 量级直觉：一个"多大算大"的具体例子。

### 3.5 动手 / 实操型节点
1. L0：操作步骤。
2. L4：模拟器或分支选择交互（"如果选 A 会…"），自包含 HTML 实现。
3. L3：简答（short_answer_table）让用户复述步骤或预判结果。
4. 产出物：用户要求时把步骤整理成单文件 HTML 发布。

### 3.6 已掌握节点（巩固 + 拔高）
1. `lg_learning_mastery_read` 确认已掌握。
2. 不重复全量讲解：L3 两题直接考（带判分），或一题拔高（应用题）。
3. 答对 → 建议进入下一节点 / 安排间隔复习；答错 → 降级为完整教学。

## 4. 交互组件选型

| 需求 | 用哪个 | 要点 |
|---|---|---|
| 选择题 / 判分练习 | channel A：single_choice / multiple_choice | `correct_option_ids` 或 `option.is_correct` + `explanation`；多题连发会自动堆叠成分页练习 |
| 填空 | channel A：fill_blank | `prompt` + `blank_ids` + `correct_answers` |
| 开放式回答 | channel A：short_answer_table | 给列名与空行 |
| 指标/状态展示 | channel A：metric_card | 学习进度、掌握度等 3～6 个指标 |
| 图片占位/展示 | channel A：image_frame | 有图加 `src` + status="ready" |
| 翻卡 / 步进 / 拖拽 / 模拟 / 动画 | channel B：magic_card | 一次提交全部 HTML/CSS/JS；禁网络；≤100k 字符；`preferred_height` 控制高度 |
| 日期/日程 | `lg_goal_ask` 的 date 类型（goal 模式） | 普通学习讲解不需要 |

- 交互组件一律等用户提交事件后再继续，不要自问自答。
- 判分题用户答对：`lg_learning_evidence_record`（source_type="exercise"，source_id 用组件/消息 id，summary 写覆盖知识点），confidence 按题目难度合理取值。

## 5. 动画与 SVG 速成

- **自包含**：所有样式/脚本内联在 magic_card 的 `preview_html` 里，禁 CDN、禁远程字体与图片。
- **SVG 图**：直接手写 `<svg viewBox>`；文字用 `<text>`；关系用 `<line>`/`<path>` + `<marker>` 箭头；配色 2～4 色，深色描边保证可读。
- **动画模式**：
  - 流程推进：CSS `@keyframes` 逐步点亮节点 + `steps()` 或 JS `setInterval` 步进。
  - 高亮聚焦：`transition: opacity/transform` 把无关元素淡出。
  - 时序：横向时间线，元素按 delay 依次入场。
  - 对比切换：按钮切换两套状态（用 JS 切换 class）。
- **可访问性**：动画给"重播"按钮；默认不自动无限循环；关键信息同时以文字呈现（动画是辅助不是唯一通道）。

## 6. 成本预算明细（量级参考）

| 动作 | 成本量级 | 说明 |
|---|---|---|
| 文本 / Markdown 表格 / ASCII | ≈0 | 默认手段 |
| SVG / 静态 HTML（magic_card） | ≈0（1～3k token） | 一次调用，禁网络 |
| create_chart | 低 | 一次调用 |
| canvas channel A 组件 | 低 | 一次调用 |
| search_web / search_images | 低（1 次网络） | 受联网开关控制 |
| download_external_image | 低 | 只有搜图命中才调用 |
| generate_image | **高（慢 + 计费）** | 每张都花真金白银；护栏：默认每会话 ≤2 张 |
| magic_card 大 HTML | 中（5～30k token） | 写得越长越贵；超过一屏内容拆两块 |
| sandbox 构建页面 | 中-高 | 涉及沙箱构建；仅 L7 场景 |

**一句话决策法**：先问"这一讲，不用图/不用交互能不能讲清楚？"——能，就停在 L0/L1；不能，再按阶梯升，并说明升的理由。

## 7. 提交前自检清单

- [ ] 选中的档位是否都服务于"更容易懂"？有没有为炫技加的维度？
- [ ] 生图是否超预算（≤2 张/会话）？是否先用过引用与搜图？
- [ ] 真实数据图表是否带 `sources`？没有权威来源是否改用了定性表达？
- [ ] magic_card 是否自包含、无网络依赖、≤100k 字符？
- [ ] 自测题是否有判分答案与解析？是否没有替用户编造掌握度/证据？
- [ ] 是否一句话说明了本讲的维度组合与成本理由？
