---
name: graph-generation
description: 当用户希望从学习目标或课程资料生成/更新知识图谱时，指导模型读取上下文并通过 lg_graph_propose_change 产出可审核的图谱提案。
license: LearnGraph-Internal
compatibility: Requires LearnGraph Agent runtime with graph tools authorized
metadata:
  author: learngraph
  version: "1.0.0"
---

# 知识图谱生成与更新

## 何时使用

- 用户要求"生成知识图谱 / 学习地图 / 知识树"，或要求把新资料、新知识点并入已有图谱。
- 会话绑定了已确认的 Goal（新建图谱）或已有 Graph（更新图谱），且本轮实际提供了图谱工具。

## 执行步骤

1. 先确认输入：学习目标、范围边界、用户基础与期望深度。缺少会改变图谱结构的关键信息时，先问 1～3 个问题再动手。
2. 若更新已有图谱，先用 `lg_graph_read` 读取当前节点与边，避免重复概念和断裂的前置链。
3. 规划结构：单一 root；`concept` 承载知识点，`practice` 承载练习，`assessment` 承载验收；粒度以"一次学习会话能完成一个节点"为准。
4. 建边优先 `prerequisite`（学习顺序）与 `contains`（层级归属），谨慎使用 `related` / `contrast` / `application`；不得出现环状前置。
5. 调用 `lg_graph_propose_change` 提交提案：新建图谱至少两个新增节点且恰好一个 root；更新时对既有节点使用 `change=update` + 真实 `node_id`，不得再造 root。每个节点/边都要给出简短 `rationale`。
6. 局部修订单个候选节点时可用 `lg_graph_update_candidate_node`，不要为小改动重发整图提案。

## 输出要求

- 提案提交后，用两三句话向用户总结图谱结构（主干、节点数、建议的学习顺序起点），并说明"提案待审核，确认后才会发布"。
- 不得声称图谱已发布或已生效；正式变更始终走 LearnGraph 审核流程。
- 不要把工具 JSON 原文粘贴给用户。
