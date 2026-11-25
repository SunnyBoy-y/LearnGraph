---
name: roadmap-planning
description: 当用户需要制定或调整学习路线与日程时，指导模型基于图谱、掌握度和可用时间使用 lg_roadmap_read / lg_roadmap_replan 及日程工具生成可审核的计划。
license: LearnGraph-Internal
compatibility: Requires LearnGraph Agent runtime with roadmap/schedule tools authorized
metadata:
  author: learngraph
  version: "1.0.0"
---

# 学习路线规划

## 何时使用

- 用户要求"制定学习计划 / 学习路线 / 排期"，或因进度、期限、可用时间变化要求重排。
- 会话绑定了已确认的 Goal 与 Graph，且本轮实际提供了路线或日程工具。

## 执行步骤

1. 先从会话和目标里拿已知条件（截止时间、每周可用时长、验收方式）；如果缺少其中 1～2 项但大致方向清楚，**先按常见可执行节奏（例如每天 30～45 分钟、按前置依赖分阶段）直接给一版路线/排期初稿**，并顺带说明默认按什么节奏排的、不对随时改——不要一上来连问三四个问题把用户拦住。若用户跳过提问或聊别的，立刻放下排期追问。
2. 用 `lg_roadmap_read` 读取当前路线；需要掌握度依据时用 `lg_learning_mastery_read`，让计划避开已掌握内容、优先补薄弱前置。
3. 调整路线用 `lg_roadmap_replan`，并在参数中说明重排理由（进度落后、目标变化、时间变化等）。
4. 落到日程时用 `lg_schedule_list` 查看现有安排，再用 `lg_schedule_create` / `lg_schedule_update` 写入；单次学习块控制在用户可持续的时长（默认 25～50 分钟），并预留复习时段。
5. 计划要与图谱前置关系一致：前置未掌握的节点不得排在其依赖项之前。

## 输出要求

- 输出一个一眼就能照着做的阶段化计划摘要（先做什么、每周怎么推进、做到什么算过关）。
- 写操作需要用户确认后才生效：用自然口吻请用户看一眼确认（或挑哪里要改），不要声称已经强制生效，也别用生硬的系统条款语气。
- 期限明显不够时，直接给出具体的取舍建议（砍掉哪块 / 降深度 / 延期限）并推荐其中最稳妥的一条。
