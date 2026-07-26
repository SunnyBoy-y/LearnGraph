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

1. 收集排序依据：截止时间、每周可用时长、验收方式。这三项不足以排序时先澄清，不要凭空排期。
2. 用 `lg_roadmap_read` 读取当前路线；需要掌握度依据时用 `lg_learning_mastery_read`，让计划避开已掌握内容、优先补薄弱前置。
3. 调整路线用 `lg_roadmap_replan`，并在参数中说明重排理由（进度落后、目标变化、时间变化等）。
4. 落到日程时用 `lg_schedule_list` 查看现有安排，再用 `lg_schedule_create` / `lg_schedule_update` 写入；单次学习块控制在用户可持续的时长（默认 25～50 分钟），并预留复习时段。
5. 计划要与图谱前置关系一致：前置未掌握的节点不得排在其依赖项之前。

## 输出要求

- 输出一个用户可读的阶段化计划摘要（阶段 → 里程碑 → 本周动作），明确"哪天做什么、怎么算完成"。
- 写操作是提案性质：向用户说明路线/日程变更需确认后生效，不得声称已强制生效。
- 期限明显不够时，如实说明取舍方案（缩范围 / 降深度 / 延期限），由用户选择。
