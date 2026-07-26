---
name: review-coach
description: 当用户要复习、自测或检查遗忘风险时，指导模型用 lg_review_list_due 与掌握度证据工具组织一次证据驱动的间隔复习。
license: LearnGraph-Internal
compatibility: Requires LearnGraph Agent runtime with review/mastery tools authorized
metadata:
  author: learngraph
  version: "1.0.0"
---

# 间隔复习教练

## 何时使用

- 用户说"该复习什么 / 帮我复习 / 考前检查薄弱点"，或在长时间未学习后回到工作区。
- 本轮实际提供了复习或掌握度工具。

## 执行步骤

1. 用 `lg_review_list_due` 获取到期节点；用 `lg_learning_mastery_read` 补充掌握度与最近证据，按"最薄弱且最影响后续学习"的顺序挑 3～7 个节点。
2. 对每个节点先做主动回忆再讲解：提一个检索式问题（可用 canvas 单选/填空组件），等用户作答后再给反馈，不要直接灌输答案。
3. 反馈要指出错在哪一步、对应哪个前置概念；用户连续答对则提高问题难度或跳到应用场景。
4. 复习产生的真实表现（答对/答错/自评）用 `lg_learning_evidence_record` 记录，说明证据类型；不得替用户编造掌握度。
5. 复习结束给出下一次建议复习时间和优先节点，数量控制在用户当天可完成的范围。

## 输出要求

- 摘要格式：本次复习了哪些节点 → 各自表现 → 已记录哪些证据 → 下次建议。
- 掌握度评级以工具返回为准，不要凭对话印象上调；证据写入是提案/记录性质，不修改正式图谱结构。
